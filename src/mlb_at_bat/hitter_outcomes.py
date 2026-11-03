"""Observed hitter-season plate-appearance distributions and independent sampling."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from numbers import Integral
from pathlib import Path
import random
import tempfile

import pandas as pd

KEY = ["player_id", "season"]
OUTCOME_COLUMNS = {
    "strikeout": "strikeout",
    "walk": "walk",
    "hit_by_pitch": "b_hit_by_pitch",
    "single": "single",
    "double": "double",
    "triple": "triple",
    "home_run": "home_run",
    "other_pa_outcome": "other_pa_outcome",
}
HIT_COLUMNS = ["single", "double", "triple", "home_run"]
COUNT_COLUMNS = ["pa", "hit", "strikeout", "walk", "b_hit_by_pitch", *HIT_COLUMNS]


class HitterBaselineUnavailable(ValueError):
    """A hitter-season has no usable outcome distribution."""


def _integer(value, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _count_column(series: pd.Series, name: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").astype("Float64")
    bad = series.notna() & (
        values.isna() | values.abs().gt(2**53 - 1) | values.lt(0) | values.mod(1).ne(0)
    )
    bad |= series.map(lambda value: isinstance(value, bool))
    if bad.any():
        raise ValueError(f"{name}: counts must be finite nonnegative integers or missing")
    return values.astype("Int64")


@dataclass(frozen=True)
class HitterOutcomeBaseline:
    """Treat probability/profile tables as read-only; distribution() returns a copy."""

    probabilities: pd.DataFrame
    profiles: pd.DataFrame
    min_pa: int

    def distribution(self, player_id: int, season: int) -> pd.DataFrame:
        """Return all eight outcomes for a usable hitter-season, including zeros."""
        player_id = _integer(player_id, "player_id")
        season = _integer(season, "season")
        selected = self.profiles.loc[
            self.profiles.player_id.eq(player_id) & self.profiles.season.eq(season)
        ]
        if selected.empty:
            raise HitterBaselineUnavailable(f"{player_id}/{season}: hitter-season not found")
        status = selected.iloc[0].quality_status
        if status != "ready":
            raise HitterBaselineUnavailable(f"{player_id}/{season}: {status}")
        return self.probabilities.loc[
            self.probabilities.player_id.eq(player_id) & self.probabilities.season.eq(season)
        ].copy().reset_index(drop=True)

    def sample_plate_appearances(
        self, player_id: int, season: int, *, n_pa: int = 100, seed: int | None = None
    ) -> pd.DataFrame:
        """Sample independent terminal outcomes; no pitcher or pitch sequence is modeled.

        A fixed seed restarts the same sequence without changing global RNG state.
        n_pa=0 returns an empty sample with the same columns.
        """
        n_pa = _integer(n_pa, "n_pa", minimum=0)
        if seed is not None:
            seed = _integer(seed, "seed", minimum=0)
        distribution = self.distribution(player_id, season)
        indices = random.Random(seed).choices(
            range(len(distribution)), weights=distribution.probability.astype(float).tolist(), k=n_pa
        )
        samples = distribution.iloc[indices].copy().reset_index(drop=True)
        samples.insert(0, "pa_number", pd.Series(range(1, n_pa + 1), dtype="Int64"))
        return samples


def build_hitter_baseline(hitters: pd.DataFrame, *, min_pa: int = 100) -> HitterOutcomeBaseline:
    """Calculate outcome_count / PA from cleaned counts, preserving unavailable rows.

    Missing counts, zero PA, inconsistent hit components, or a negative remainder
    prevent sampling. No missing value is filled with zero, no probability is
    smoothed, and no distribution is renormalized. min_pa is only a review flag.
    """
    min_pa = _integer(min_pa, "min_pa", minimum=0)
    required = [*KEY, "player_name", *COUNT_COLUMNS]
    if hitters.columns.duplicated().any() or not set(required).issubset(hitters):
        raise ValueError(f"hitters: requires unique columns including {required}")
    profiles = hitters[required].copy(deep=True)
    if profiles.empty:
        raise ValueError("No hitter-seasons supplied")
    for column in KEY + COUNT_COLUMNS:
        profiles[column] = _count_column(profiles[column], column)
    if profiles[KEY].isna().any(axis=None) or profiles[KEY].le(0).any(axis=None):
        raise ValueError("hitters: player_id and season must be positive and populated")
    if profiles.duplicated(KEY).any():
        raise ValueError("hitters: duplicate player-season keys")
    profiles = profiles.sort_values(KEY, kind="stable").reset_index(drop=True)
    profiles["hit_component_sum"] = profiles[HIT_COLUMNS].sum(axis=1, min_count=4).astype("Int64")
    profiles["hit_components_agree"] = profiles.hit.eq(profiles.hit_component_sum)
    profiles["other_pa_outcome"] = (
        profiles.pa - profiles.strikeout - profiles.walk - profiles.b_hit_by_pitch - profiles.hit
    )
    profiles["has_missing_counts"] = profiles[COUNT_COLUMNS].isna().any(axis=1).astype("boolean")
    profiles["meets_min_pa"] = profiles.pa.ge(min_pa)
    profiles["quality_status"] = pd.Series("ready", index=profiles.index, dtype="string")
    # Later conditions take precedence; the audit columns retain the other facts.
    for status, mask in [
        ("unavailable_negative_remainder", profiles.other_pa_outcome.lt(0)),
        ("unavailable_hit_mismatch", profiles.hit_components_agree.eq(False)),
        ("unavailable_zero_pa", profiles.pa.eq(0)),
        ("unavailable_missing_counts", profiles.has_missing_counts),
    ]:
        profiles.loc[mask.fillna(False), "quality_status"] = status
    profiles["is_available"] = profiles.quality_status.eq("ready").astype("boolean")

    identifiers = [*KEY, "player_name", "pa", "meets_min_pa", "quality_status", "is_available"]
    tables = []
    for outcome, column in OUTCOME_COLUMNS.items():
        table = profiles[identifiers].copy()
        table["outcome"] = outcome
        table["count_source"] = column if outcome != "other_pa_outcome" else "derived_remainder"
        table["outcome_count"] = profiles[column]
        table["probability"] = profiles[column] / profiles.pa.where(profiles.is_available)
        tables.append(table)
    # Stable key sorting keeps the documented outcome order for every hitter.
    probabilities = pd.concat(tables, ignore_index=True).sort_values(KEY, kind="stable").reset_index(drop=True)
    return HitterOutcomeBaseline(probabilities, profiles, min_pa)


def compare_outcome_frequencies(distribution: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    """Compare sampled frequencies with one usable distribution, retaining zero rows."""
    if (distribution.empty or distribution.duplicated("outcome").any()
            or len(distribution[KEY].drop_duplicates()) != 1
            or not distribution.is_available.all()):
        raise ValueError("Provide one usable distribution from baseline.distribution()")
    if not samples.empty:
        if not samples[KEY].eq(distribution[KEY].iloc[0]).all(axis=None):
            raise ValueError("Samples belong to a different hitter-season")
        if not samples.outcome.isin(distribution.outcome).all():
            raise ValueError("Samples contain an outcome outside this distribution")
    result = distribution.copy()
    result["sampled_count"] = result.outcome.map(samples.outcome.value_counts()).fillna(0).astype("Int64")
    result["expected_count"] = result.probability * len(samples)
    result["sampled_percent"] = (
        100 * result.sampled_count / len(samples) if len(samples) else
        pd.Series(pd.NA, index=result.index, dtype="Float64")
    )
    result["difference_percentage_points"] = result.sampled_percent - 100 * result.probability
    return result


def load_hitter_baseline(hitters: Path, *, min_pa: int = 100) -> HitterOutcomeBaseline:
    """Load a cleaned CSV or Parquet hitter table and build the count-based baseline."""
    hitters = Path(hitters)
    if hitters.suffix.lower() == ".csv":
        frame = pd.read_csv(hitters)
    elif hitters.suffix.lower() == ".parquet":
        frame = pd.read_parquet(hitters)
    else:
        raise ValueError("Hitter baseline input must be a cleaned .csv or .parquet table")
    return build_hitter_baseline(frame, min_pa=min_pa)


def write_hitter_baseline(hitters: Path, output: Path, *, min_pa: int = 100) -> dict:
    """Export profiles, probabilities, and a reproducible report with source hash."""
    hitters, output = Path(hitters), Path(output)
    if output.resolve() == hitters.resolve().parent or output.resolve() in hitters.resolve().parents:
        raise ValueError("Output must not contain the source input")
    baseline = load_hitter_baseline(hitters, min_pa=min_pa)
    profiles = baseline.profiles
    report = {
        "schema_version": 1,
        "baseline_type": "season_hitter_outcomes",
        "conditioning_variables": KEY,
        "outcome_categories": list(OUTCOME_COLUMNS),
        "hitter_seasons": len(profiles),
        "outcome_rows": len(baseline.probabilities),
        "available_hitter_seasons": int(profiles.is_available.sum()),
        "quality_status_counts": {
            str(status): int(count) for status, count in profiles.quality_status.value_counts().sort_index().items()
        },
        "min_pa_flag_threshold": baseline.min_pa,
        "meets_min_pa": int(profiles.meets_min_pa.sum()),
        "below_min_pa_retained": int(profiles.meets_min_pa.eq(False).sum()),
        "missing_pa": int(profiles.pa.isna().sum()),
        "hit_component_mismatches": int(profiles.hit_components_agree.eq(False).sum()),
        "negative_remainders": int(profiles.other_pa_outcome.lt(0).sum()),
        "sources": {"hitters": {
            "filename": hitters.name, "sha256": hashlib.sha256(hitters.read_bytes()).hexdigest()
        }},
        "notes": [
            "Each usable probability is an outcome count divided by plate appearances, not at-bats.",
            "Walk counts already include intentional walks; no intentional-walk count is added.",
            "Other PA outcome is PA minus strikeouts, walks, HBP, and hits; it is not labeled an out.",
            "Missing counts are not zeros; inconsistent rows remain visible with null probabilities.",
            "No smoothing, probability normalization, or low-PA exclusions are applied.",
            "Samples are independent hypothetical plate appearances, with no pitcher or pitch context.",
            "Season exports have no verified as-of date; no historical predictive performance is claimed.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hitter-baseline-", dir=output.parent) as temp:
        stage = Path(temp)
        for name, frame in [("hitter_outcome_probabilities", baseline.probabilities),
                            ("hitter_outcome_profiles", profiles)]:
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
