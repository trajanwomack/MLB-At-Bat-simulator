"""Season-repertoire baseline for independent pitch selections; no ML fitting."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isclose
from numbers import Integral
from pathlib import Path
import random
import tempfile

import pandas as pd

from .pitcher_exports import KEY, PITCH_TYPES

READY_STATUSES = {"ready", "normalized_rounding"}
USAGE_DECIMAL_PLACES = 1
EPSILON = 1e-9


class RepertoireUnavailable(ValueError):
    """A requested pitcher-season has no usable pitch distribution."""


def _integer(value, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _numeric(series: pd.Series, name: str, *, integer: bool = False) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").astype("Float64")
    bad = series.notna() & (values.isna() | values.abs().gt(2**53 - 1))
    bad |= series.map(lambda value: isinstance(value, bool))
    if integer:
        bad |= values.notna() & (values.le(0) | values.mod(1).ne(0))
    if bad.any():
        raise ValueError(f"Invalid numeric values in {name}")
    return values.astype("Int64" if integer else "Float64")


def _keys(frame: pd.DataFrame, required: set[str], label: str) -> pd.DataFrame:
    if frame.columns.duplicated().any() or not required.issubset(frame):
        raise ValueError(f"{label}: requires unique columns including {sorted(required)}")
    result = frame.copy(deep=True)
    for name in KEY:
        result[name] = _numeric(result[name], name, integer=True)
        if result[name].isna().any():
            raise ValueError(f"{label}: missing {name}")
    return result


@dataclass(frozen=True)
class PitchSelectionBaseline:
    """Probability rows and one quality/coverage row per pitcher-season.

    Treat these dataframes as read-only; repertoire() returns a separate copy.
    Unavailable groups retain null probabilities, never fabricated fallbacks.
    """

    probabilities: pd.DataFrame
    repertoires: pd.DataFrame

    def repertoire(self, player_id: int, season: int) -> pd.DataFrame:
        """Look up a usable repertoire, or raise with its availability reason."""
        player_id = _integer(player_id, "player_id")
        season = _integer(season, "season")
        selected = self.repertoires.loc[
            self.repertoires.player_id.eq(player_id) & self.repertoires.season.eq(season)
        ]
        if selected.empty:
            raise RepertoireUnavailable(f"{player_id}/{season}: pitcher-season not found")
        status = selected.iloc[0].quality_status
        if status not in READY_STATUSES:
            raise RepertoireUnavailable(f"{player_id}/{season}: {status}")
        return self.probabilities.loc[
            self.probabilities.player_id.eq(player_id) & self.probabilities.season.eq(season)
        ].copy().reset_index(drop=True)

    def sample_pitches(
        self, player_id: int, season: int, *, n_pitches: int = 1, seed: int | None = None
    ) -> pd.DataFrame:
        """Sample independent pitches, with replacement, using a local RNG.

        The same seed restarts the same sequence; no global random state changes.
        Zero requests return an empty table. These are pitches, not at-bats.
        """
        n_pitches = _integer(n_pitches, "n_pitches", minimum=0)
        if seed is not None:
            seed = _integer(seed, "seed", minimum=0)
        repertoire = self.repertoire(player_id, season)
        # Zero-weight rows are preserved in the lookup but never sampled.
        indices = random.Random(seed).choices(
            range(len(repertoire)),
            weights=repertoire.sampling_probability.astype(float).tolist(),
            k=n_pitches,
        )
        samples = repertoire.iloc[indices].copy().reset_index(drop=True)
        samples.insert(0, "pitch_number", pd.Series(range(1, n_pitches + 1), dtype="Int64"))
        return samples


def build_pitch_baseline(
    arsenal: pd.DataFrame, *, pitchers: pd.DataFrame | None = None
) -> PitchSelectionBaseline:
    """Build P(pitch type | pitcher, season) from one-decimal usage percentages.

    Input is the cleaned individual-pitch long table, not broad pitch families.
    Optional pitchers supplies the full cohort, including absent repertoires.
    Invalid keys/units/duplicates raise ValueError. Legitimate missing/incomplete
    repertoires stay in the output with an unavailable status. Inputs are copied.
    """
    required = {*KEY, "player_name", "pitch_type", "usage_percent", "arsenal_pitch_total"}
    frame = _keys(arsenal, required, "arsenal")
    additions = {"sampling_probability", "quality_status", "reported_usage_sum_percent",
                 "rounding_tolerance_percent"}
    if additions.intersection(frame):
        raise ValueError("arsenal: expected source usage rows, not an existing baseline")
    frame["pitch_type"] = frame.pitch_type.astype("string").str.strip().str.upper()
    if frame.pitch_type.isna().any() or not frame.pitch_type.isin(
        [code.upper() for code in PITCH_TYPES]
    ).all():
        raise ValueError("arsenal: unknown pitch type or broad pitch family")
    if frame.duplicated(KEY + ["pitch_type"]).any():
        raise ValueError("arsenal: duplicate pitcher-season-pitch type")
    frame["usage_percent"] = _numeric(frame.usage_percent, "usage_percent")
    usage = frame.usage_percent
    if (usage.notna() & ~usage.between(0, 100)).any():
        raise ValueError("usage_percent must be between 0 and 100")
    if ((usage * 10 - (usage * 10).round()).abs().gt(EPSILON)).any():
        raise ValueError("usage_percent must have at most one decimal place")
    frame["arsenal_pitch_total"] = _numeric(
        frame.arsenal_pitch_total, "arsenal_pitch_total", integer=True
    )
    frame = frame.sort_values(KEY + ["pitch_type"], kind="stable").reset_index(drop=True)
    groups = {key: group for key, group in frame.groupby(KEY, sort=False)}
    if pitchers is None:
        cohort = frame[KEY + ["player_name"]].drop_duplicates(KEY)
    else:
        cohort = _keys(pitchers, {*KEY, "player_name"}, "pitchers")[KEY + ["player_name"]]
        if cohort.duplicated(KEY).any():
            raise ValueError("pitchers: duplicate player-season keys")
        if set(groups) - set(cohort[KEY].itertuples(index=False, name=None)):
            raise ValueError("arsenal contains pitcher-seasons outside the supplied cohort")
    cohort = cohort.sort_values(KEY).reset_index(drop=True)
    if cohort.empty:
        raise ValueError("No pitcher-seasons supplied")

    frame["sampling_probability"] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    frame["quality_status"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame["reported_usage_sum_percent"] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    frame["rounding_tolerance_percent"] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    summaries = []
    for player_id, season, player_name in cohort.itertuples(index=False, name=None):
        group = groups.get((player_id, season), frame.iloc[:0])
        if group.arsenal_pitch_total.nunique(dropna=False) > 1:
            raise ValueError(f"{player_id}/{season}: inconsistent arsenal_pitch_total")
        if group.player_name.nunique(dropna=False) > 1:
            raise ValueError(f"{player_id}/{season}: inconsistent player_name")
        n_types = len(group)
        missing = int(group.usage_percent.isna().sum())
        total = group.usage_percent.sum(min_count=1)
        tolerance = n_types * 0.5 * 10**-USAGE_DECIMAL_PLACES
        if group.empty:
            status = "unavailable_no_repertoire"
        elif missing:
            status = "unavailable_missing_usage"
        elif total == 0:
            status = "unavailable_zero_usage"
        elif abs(total - 100) > tolerance + EPSILON:
            status = "unavailable_incomplete_usage"
        elif isclose(total, 100, abs_tol=EPSILON, rel_tol=0):
            status = "ready"
        else:
            status = "normalized_rounding"
        if status in READY_STATUSES:
            frame.loc[group.index, "sampling_probability"] = group.usage_percent / total
        frame.loc[group.index, "quality_status"] = status
        frame.loc[group.index, "reported_usage_sum_percent"] = total
        frame.loc[group.index, "rounding_tolerance_percent"] = tolerance
        summaries.append({
            "player_id": player_id, "season": season, "player_name": player_name,
            "arsenal_pitch_total": group.arsenal_pitch_total.iloc[0] if n_types else pd.NA,
            "reported_pitch_types": n_types,
            "positive_usage_pitch_types": int(group.usage_percent.gt(0).sum()),
            "missing_usage_rows": missing,
            "reported_usage_sum_percent": total,
            "rounding_tolerance_percent": tolerance,
            "quality_status": status,
            "is_available": status in READY_STATUSES,
        })
    summary = pd.DataFrame(summaries)
    for column in KEY + ["arsenal_pitch_total", "reported_pitch_types",
                         "positive_usage_pitch_types", "missing_usage_rows"]:
        summary[column] = summary[column].astype("Int64")
    for column in ["reported_usage_sum_percent", "rounding_tolerance_percent"]:
        summary[column] = summary[column].astype("Float64")
    summary["is_available"] = summary.is_available.astype("boolean")
    return PitchSelectionBaseline(frame, summary)


def compare_pitch_frequencies(repertoire: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    """Compare one lookup's probabilities with sampled frequencies, including zeros."""
    if repertoire.empty or repertoire.duplicated("pitch_type").any():
        raise ValueError("Provide one nonempty repertoire from baseline.repertoire()")
    if len(repertoire[KEY].drop_duplicates()) != 1:
        raise ValueError("Comparison requires exactly one pitcher-season")
    if not samples.empty:
        key = repertoire[KEY].iloc[0]
        if not samples[KEY].eq(key).all(axis=None):
            raise ValueError("Samples belong to a different pitcher-season")
        if not samples.pitch_type.isin(repertoire.pitch_type).all():
            raise ValueError("Samples contain a pitch outside this repertoire")
    result = repertoire.copy()
    counts = samples.pitch_type.value_counts()
    result["sampled_count"] = result.pitch_type.map(counts).fillna(0).astype("Int64")
    result["expected_count"] = result.sampling_probability * len(samples)
    result["sampled_percent"] = (
        100 * result.sampled_count / len(samples) if len(samples) else
        pd.Series(pd.NA, index=result.index, dtype="Float64")
    )
    result["difference_percentage_points"] = (
        result.sampled_percent - 100 * result.sampling_probability
    )
    return result


def load_pitch_baseline(arsenal: Path, pitchers: Path) -> PitchSelectionBaseline:
    """Read cleaned CSV/Parquet inputs; neither raw source nor model is modified."""
    def read(path: Path) -> pd.DataFrame:
        path = Path(path)
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        raise ValueError("Pitch-selection inputs must be cleaned .csv or .parquet tables")
    return build_pitch_baseline(read(arsenal), pitchers=read(pitchers))


def write_pitch_baseline(arsenal: Path, pitchers: Path, output: Path) -> dict:
    """Export the probability lookup, cohort coverage, and input hash provenance."""
    arsenal, pitchers, output = Path(arsenal), Path(pitchers), Path(output)
    baseline = load_pitch_baseline(arsenal, pitchers)
    if any(output.resolve() == source.resolve().parent or output.resolve() in source.resolve().parents
           for source in (arsenal, pitchers)):
        raise ValueError("Output must not contain a source input")
    report = {
        "schema_version": 1,
        "baseline_type": "season_repertoire",
        "conditioning_variables": KEY,
        "usage_decimal_places": USAGE_DECIMAL_PLACES,
        "pitcher_seasons": len(baseline.repertoires),
        "pitch_type_rows": len(baseline.probabilities),
        "available_pitcher_seasons": int(baseline.repertoires.is_available.sum()),
        "quality_status_counts": {
            str(key): int(value) for key, value in
            baseline.repertoires.quality_status.value_counts().sort_index().items()
        },
        "zero_usage_rows": int(baseline.probabilities.usage_percent.eq(0).sum()),
        "pitcher_seasons_missing_pitch_total": int(baseline.repertoires.arsenal_pitch_total.isna().sum()),
        "sources": {
            name: {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in [("arsenal", arsenal), ("pitchers", pitchers)]
        },
        "notes": [
            "Source usage percentages are preserved; only sampling probabilities are normalized.",
            "Rounding tolerance is 0.05 percentage points per reported pitch type.",
            "Missing or materially incomplete repertoires cannot be sampled.",
            "Missing pitch totals remain missing; no per-type observed counts are reconstructed.",
            "Selections are independent; count, hitter, and pitch history are not modeled.",
            "These season summaries have no verified as-of date and are not historical pre-pitch features.",
            "No ML fitting, game-outcome simulation, or predictive evaluation is performed.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pitch-baseline-", dir=output.parent) as temp:
        stage = Path(temp)
        for name, frame in [("pitch_selection_probabilities", baseline.probabilities),
                            ("pitcher_repertoires", baseline.repertoires)]:
            frame.to_csv(stage / f"{name}.csv", index=False, lineterminator="\n")
            frame.to_parquet(stage / f"{name}.parquet", index=False)
        report_name = "quality_report.json"
        (stage / report_name).write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
        )
        output.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in stage.iterdir() if p.name != report_name) + [stage / report_name]:
            path.replace(output / path.name)
    return report
