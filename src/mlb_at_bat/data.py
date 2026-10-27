"""Preserve raw Savant exports and build auditable, chronological datasets."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

KEY = ["game_pk", "at_bat_number", "pitch_number"]
PA_KEY = KEY[:2]
REQUIRED = set(KEY + ["game_date", "batter", "pitcher", "balls", "strikes",
                      "pitch_type", "description", "events"])
NULLS = {"", "null", "none", "nan", "na", "n/a"}
INTEGER_COLUMNS = set(KEY + ["batter", "pitcher", "balls", "strikes", "inning",
    "outs_when_up", "game_year", "on_1b", "on_2b", "on_3b", "zone",
    "home_score", "away_score", "bat_score", "fld_score", "post_home_score",
    "post_away_score", "post_bat_score", "post_fld_score", "hit_location"]
    + [f"fielder_{i}" for i in range(2, 10)])
FLOAT_COLUMNS = set("release_speed release_pos_x release_pos_y release_pos_z "
    "pfx_x pfx_z plate_x plate_z vx0 vy0 vz0 ax ay az sz_top sz_bot "
    "hit_distance_sc launch_speed launch_angle effective_speed release_spin_rate "
    "release_extension spin_axis hc_x hc_y estimated_ba_using_speedangle "
    "estimated_woba_using_speedangle woba_value woba_denom babip_value iso_value "
    "launch_speed_angle delta_home_win_exp delta_run_exp".split())
OUTCOMES = {
    "single": "single", "double": "double", "triple": "triple",
    "home_run": "home_run", "walk": "walk", "intent_walk": "walk",
    "hit_by_pitch": "hit_by_pitch", "strikeout": "strikeout",
    "strikeout_double_play": "strikeout", "field_error": "reached_on_error",
    "catcher_interf": "catcher_interference", "fielders_choice": "fielders_choice",
    "fielders_choice_out": "fielders_choice", "force_out": "fielders_choice",
    **dict.fromkeys(["field_out", "grounded_into_double_play",
        "double_play", "triple_play", "sac_fly", "sac_bunt",
        "sac_fly_double_play", "sac_bunt_double_play"], "other_out"),
}
AUTOMATIC = {"automatic_ball", "automatic_strike"}
SAMPLE_COMMIT = "e588989359059ba04a510cc12aefa972f0a715d6"
SAMPLE_URL = (f"https://raw.githubusercontent.com/jldbc/pybaseball/{SAMPLE_COMMIT}/"
              "tests/pybaseball/data/small_request_raw.csv")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def decode_csv(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Read the original headers before pandas can rename duplicate columns."""
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
    try:
        headers = [name.strip() for name in next(reader)]
    except StopIteration as exc:
        raise ValueError("Empty response; expected a Savant CSV header") from exc
    if not REQUIRED.issubset(headers):
        raise ValueError(f"Not a supported pitch CSV; missing {sorted(REQUIRED - set(headers))}")
    if any(not name or name.startswith("_") for name in headers):
        raise ValueError("Blank headers and reserved '_' column names are unsupported")
    rows = []
    try:
        for record, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV record {record}: expected {len(headers)} fields, got {len(row)}")
            rows.append(row)
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV: {exc}") from exc
    return headers, rows


def _write_new(path: Path, content: bytes) -> None:
    """Atomically create an immutable raw file; never replace existing data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write(content)
    try:
        os.link(temp, path)
    finally:
        temp.unlink()


def fetch_csv(url: str, destination: Path, *, source_type: str,
              expected_date: str | None = None) -> dict:
    manifest_path = destination.with_suffix(".manifest.json")
    if destination.exists():
        if not manifest_path.exists():
            raise ValueError(f"Refusing to replace unmanifested raw file: {destination}")
        manifest = json.loads(manifest_path.read_text())
        if (manifest["sha256"] != sha256(destination.read_bytes())
                or manifest["url"] != url or manifest["source_type"] != source_type):
            raise ValueError(f"Cached raw file or provenance mismatch: {destination}")
        return manifest
    if manifest_path.exists():
        raise ValueError(f"Manifest exists without its raw file: {manifest_path}")
    request = Request(url, headers={"User-Agent": "MLBAtBatSimulator/0.1", "Accept": "text/csv"})
    # urllib's default HTTPS context preserves certificate verification.
    with urlopen(request, timeout=90) as response:
        content = response.read(128 * 1024 * 1024 + 1)
        final_url = response.geturl()
    if len(content) > 128 * 1024 * 1024:
        raise ValueError("Response exceeds the 128 MiB per-request safety limit")
    headers, rows = decode_csv(content)
    if expected_date is not None:
        dates = {r[headers.index("game_date")] for r in rows}
        if dates - {expected_date}:
            raise ValueError(f"Unexpected game dates: {sorted(dates)}")
        if "game_type" in headers and any(r[headers.index("game_type")] != "R" for r in rows):
            raise ValueError("Regular-season query returned a different game type")
    manifest = {"source_type": source_type, "url": url, "resolved_url": final_url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": sha256(content),
        "bytes": len(content), "rows": len(rows), "columns": headers,
        "expected_game_date": expected_date}
    _write_new(destination, content)
    _write_new(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def fetch_dates(start: date, end: date, output: Path) -> list[dict]:
    if end < start:
        raise ValueError("End date must be on or after start date")
    manifests = []
    current = start
    while current <= end:
        query = urlencode({"all": "true", "type": "details", "player_type": "pitcher",
            "game_date_gt": current.isoformat(), "game_date_lt": current.isoformat(), "hfGT": "R|"})
        manifests.append(fetch_csv(f"https://baseballsavant.mlb.com/statcast_search/csv?{query}",
            output / f"{current}.csv", source_type="baseball_savant_live_export",
            expected_date=current.isoformat()))
        current += timedelta(days=1)
    return manifests


def read_export(path: Path) -> tuple[pd.DataFrame, dict]:
    content = path.read_bytes()
    headers, records = decode_csv(content)
    raw = pd.DataFrame(records, columns=range(len(headers)), dtype="string")
    for column in raw:
        raw[column] = raw[column].str.strip()
        raw[column] = raw[column].mask(raw[column].str.lower().isin(NULLS))
    frame = pd.DataFrame(index=raw.index)
    for name in dict.fromkeys(headers):
        positions = [i for i, header in enumerate(headers) if header == name]
        value = raw[positions[0]].copy()
        for position in positions[1:]:
            candidate = raw[position]
            conflict = value.notna() & candidate.notna() & value.ne(candidate).fillna(False)
            if conflict.any():
                raise ValueError(f"{path.name}: conflicting duplicate header {name!r}")
            value = value.fillna(candidate)
        frame[name] = value
    frame["_source_file"] = str(path)
    frame["_source_record"] = pd.Series(range(1, len(frame) + 1), dtype="Int64")
    metadata = {"file": str(path), "sha256": sha256(content), "rows": len(frame),
        "duplicate_headers": [name for name, n in Counter(headers).items() if n > 1],
        "source_type": "user_supplied_csv"}
    manifest_path = path.with_suffix(".manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["sha256"] != metadata["sha256"]:
            raise ValueError(f"Raw checksum mismatch: {path}")
        metadata["provenance"] = manifest
        metadata["source_type"] = manifest["source_type"]
    return frame, metadata


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.fillna("<missing>").value_counts().items()}


def clean_exports(inputs: list[Path], output: Path) -> dict:
    if not inputs:
        raise ValueError("No CSV inputs supplied")
    # Derived outputs must never replace a raw input or its provenance.
    output = output.resolve()
    if any(path.resolve().parent == output or output in path.resolve().parents for path in inputs):
        raise ValueError("Keep processed outputs separate from the raw input directory")
    frames, sources = [], []
    for path in sorted(set(inputs)):
        frame, metadata = read_export(path)
        frames.append(frame)
        sources.append(metadata)
    frame = pd.concat(frames, ignore_index=True)
    raw_columns = sorted(c for c in frame if not c.startswith("_"))
    # Detect source conflicts before numeric coercion could erase differing bad values.
    exact_duplicates = frame.duplicated(subset=raw_columns)
    exact_duplicate_count = int(exact_duplicates.sum())
    frame = frame.loc[~exact_duplicates].copy()
    numeric_issues = {}
    for column in sorted((INTEGER_COLUMNS | FLOAT_COLUMNS) & set(frame.columns)):
        value = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].notna() & (value.isna() | ~np.isfinite(value.astype(float)))
        if column in INTEGER_COLUMNS:
            invalid |= value.notna() & ((value % 1 != 0) | (value.abs() > 2**53 - 1))
        numeric_issues[column] = int(invalid.sum())
        frame[column] = value.mask(invalid).astype("Int64" if column in INTEGER_COLUMNS else "Float64")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    reasons = pd.Series("", index=frame.index, dtype="string")

    def reject(mask: pd.Series, reason: str) -> None:
        reasons.loc[mask.fillna(True)] += reason + ";"

    reject(dates.isna(), "invalid_game_date")
    frame["game_date"] = dates
    for column in KEY + ["batter", "pitcher"]:
        reject(frame[column].isna() | (frame[column] <= 0), f"invalid_{column}")
    reject(~frame["balls"].between(0, 3), "invalid_balls")
    reject(~frame["strikes"].between(0, 2), "invalid_strikes")
    for column, low, high in [("outs_when_up", 0, 2), ("inning", 1, None)]:
        if column in frame:
            reject(frame[column].notna() & ((frame[column] < low)
                | ((frame[column] > high) if high is not None else False)), f"invalid_{column}")
    valid_key = frame[KEY].notna().all(axis=1)
    conflicts = valid_key & frame.duplicated(subset=KEY, keep=False)
    reject(conflicts, "conflicting_pitch_key")
    for column in ["pitch_type", "stand", "p_throws", "game_type"]:
        if column in frame:
            frame[column] = frame[column].str.upper()
    for column in ["description", "events"]:
        frame[column] = frame[column].str.lower()
    rejected = frame.loc[reasons.ne("")].copy()
    rejected["_rejection_reason"] = reasons.loc[rejected.index].str.rstrip(";")
    pitches = frame.loc[reasons.eq("")].sort_values(["game_date", *KEY], kind="stable").copy()
    pitches["is_automatic_event"] = pitches["description"].isin(AUTOMATIC)
    pitches["pitch_type_eligible"] = (pitches["pitch_type"].notna()
        & ~pitches["pitch_type"].isin(["UN", "UNKNOWN"])
        & ~pitches["is_automatic_event"])
    bad_pas = set(rejected.loc[rejected[PA_KEY].notna().all(axis=1), PA_KEY]
                  .itertuples(index=False, name=None))
    appearances = []
    for key, group in pitches.groupby(PA_KEY, sort=True):
        last = group.iloc[-1]
        terminal = group.loc[group["events"].isin(OUTCOMES)]
        completed = len(terminal) == 1 and terminal.index[0] == group.index[-1]
        sequence_complete = group["pitch_number"].tolist() == list(range(1, len(group) + 1))
        stable_matchup = group["batter"].nunique() == 1 and group["pitcher"].nunique() == 1
        status = ("completed" if completed else "ambiguous_terminal_events"
                  if len(terminal) else "interrupted_or_unresolved")
        appearances.append({"game_pk": int(key[0]), "at_bat_number": int(key[1]),
            "game_date": group.iloc[0]["game_date"], "batter": int(last["batter"]),
            "pitcher": int(last["pitcher"]), "recorded_pitches": len(group),
            "terminal_event": last["events"] if completed else pd.NA,
            "outcome": OUTCOMES[str(last["events"])] if completed else pd.NA,
            "status": status, "sequence_complete": sequence_complete,
            "stable_matchup": stable_matchup, "has_rejected_rows": key in bad_pas,
            "outcome_eligible": completed and sequence_complete and stable_matchup and key not in bad_pas})
    pa_columns = ["game_pk", "at_bat_number", "game_date", "batter", "pitcher",
        "recorded_pitches", "terminal_event", "outcome", "status", "sequence_complete",
        "stable_matchup", "has_rejected_rows", "outcome_eligible"]
    pas = pd.DataFrame(appearances, columns=pa_columns)
    for column in ["game_pk", "at_bat_number", "batter", "pitcher", "recorded_pitches"]:
        pas[column] = pas[column].astype("Int64")
    for column in ["terminal_event", "outcome", "status"]:
        pas[column] = pas[column].astype("string")
    for column in ["sequence_complete", "stable_matchup", "has_rejected_rows", "outcome_eligible"]:
        pas[column] = pas[column].astype("boolean")
    pas["game_date"] = pd.to_datetime(pas["game_date"])
    rejections = Counter(reason for value in rejected["_rejection_reason"]
                         for reason in value.split(";"))
    observed_events = set(pitches["events"].dropna())
    report = {
        "schema_version": 1, "inputs": sources, "input_rows": sum(s["rows"] for s in sources),
        "clean_pitch_rows": len(pitches), "rejected_rows": len(rejected),
        "exact_duplicate_rows_removed": exact_duplicate_count,
        "conflicting_key_rows_quarantined": int(conflicts.sum()),
        "rejection_reasons": dict(rejections),
        "numeric_parse_issues": {k: v for k, v in numeric_issues.items() if v},
        "games": int(pitches["game_pk"].nunique()), "plate_appearance_groups": len(pas),
        "eligible_pitch_type_rows": int(pitches["pitch_type_eligible"].sum()),
        "eligible_completed_plate_appearances": int(pas["outcome_eligible"].sum()),
        "plate_appearance_statuses": _counts(pas["status"]),
        "sequence_gap_groups": int((~pas["sequence_complete"]).sum()),
        "pitch_types": _counts(pitches["pitch_type"]), "events": _counts(pitches["events"]),
        "outcomes": _counts(pas["outcome"]),
        "events_without_batting_outcome_mapping": sorted(observed_events - set(OUTCOMES)),
        "columns": {c: {"dtype": str(pitches[c].dtype),
            "missing": int(pitches[c].isna().sum())} for c in pitches},
        "deprecated_columns": sorted(c for c in raw_columns if "deprecated" in c),
        "notes": ["Empty events and absent contact measurements are often structural, not errors.",
            "Candidate eligibility flags do not prove full game coverage or model readiness.",
            "Unknown event codes are retained and excluded from completed batting labels.",
            "Unresolved groups need game-feed reconciliation; no outcome is invented.",
            "These outputs include post-pitch fields: never use the whole table as model features.",
            "Repeated records are removed; differing records with the same key are all quarantined."]}
    assert report["input_rows"] == len(pitches) + len(rejected) + exact_duplicate_count
    output.mkdir(parents=True, exist_ok=True)
    pitches.reset_index(drop=True).to_parquet(output / "pitches.parquet", index=False)
    pas.to_parquet(output / "plate_appearances.parquet", index=False)
    rejected.reset_index(drop=True).to_parquet(output / "rejected_rows.parquet", index=False)
    (output / "quality_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
