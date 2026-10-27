"""Import pitcher-season summaries without treating them as individual pitches."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data import NULLS, _write_new, sha256

NAME = "last_name, first_name"
REQUIRED = {NAME, "player_id", "year", "p_formatted_ip", "p_out", "pa"}
PERCENTAGES = {"k_percent", "bb_percent", "hard_hit_percent", "whiff_percent", "swing_percent"}
FLOATS = PERCENTAGES | set("batting_avg slg_percent on_base_percent on_base_plus_slg "
    "isolated_power babip p_era p_opp_batting_avg p_opp_on_base_avg woba "
    "avg_best_speed avg_hyper_speed".split())
INTEGERS = set("player_id year player_age p_game pa ab hit single double triple home_run "
    "strikeout walk p_earned_run p_run p_save p_blown_save p_out p_win p_loss "
    "p_wild_pitch p_balk p_shutout p_total_stolen_base p_pickoff_attempt_1b "
    "p_pickoff_attempt_2b p_pickoff_attempt_3b p_pickoff_1b p_pickoff_2b p_pickoff_3b "
    "p_lob p_rbi p_stolen_base_2b p_stolen_base_3b p_stolen_base_home p_quality_start "
    "p_walkoff p_run_support p_ab_scoring p_automatic_ball p_ball p_called_strike "
    "p_catcher_interf p_caught_stealing_2b p_caught_stealing_3b p_caught_stealing_home "
    "p_complete_game p_defensive_indiff p_foul p_foul_tip p_game_finished "
    "p_game_in_relief p_gnd_into_dp p_gnd_into_tp p_gnd_rule_double p_hit_by_pitch "
    "p_hit_fly p_hit_ground p_hit_line_drive p_hit_into_play p_hit_scoring p_hold "
    "p_intent_ball p_intent_walk p_missed_bunt p_out_fly p_out_ground p_out_line_drive "
    "p_passed_ball p_pickoff_error_1b p_pickoff_error_2b p_pickoff_error_3b p_pitchout "
    "p_relief_no_out p_sac_bunt p_sac_fly p_starting_p p_swinging_strike p_unearned_run "
    "p_total_ball p_total_bases p_total_caught_stealing p_total_pickoff "
    "p_total_pickoff_attempt p_total_pickoff_error p_total_pitches p_total_sacrifices "
    "p_total_strike p_total_swinging_strike p_inh_runner p_inh_runner_scored "
    "p_beq_runner p_beq_runner_scored p_reached_on_error".split())
KNOWN = INTEGERS | FLOATS | {NAME, "p_formatted_ip"}
RESERVED_OUTPUTS = {"player_name", "season", "innings_pitched_display", "innings_pitched_decimal",
    "has_quality_flags", "strikeouts_per_pa", "walks_per_pa", "home_runs_per_pa", "hits_per_pa"}
RESERVED_OUTPUTS |= {name.removesuffix("_percent") + "_fraction" for name in PERCENTAGES}


def read_summary(content: bytes) -> pd.DataFrame:
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
    try:
        headers = [value.strip() for value in next(reader)]
        if len(set(headers)) != len(headers):
            raise ValueError("Pitcher-summary CSV has duplicate column names")
        if not REQUIRED.issubset(headers):
            raise ValueError(f"Not a pitcher-season CSV; missing {sorted(REQUIRED - set(headers))}")
        if any(not name or name.startswith("_") for name in headers):
            raise ValueError("Empty or reserved column names in pitcher summary")
        if set(headers) & RESERVED_OUTPUTS:
            raise ValueError(f"Source columns collide with derived fields: {sorted(set(headers) & RESERVED_OUTPUTS)}")
        rows = []
        for number, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV record {number} has {len(row)} fields; expected {len(headers)}")
            rows.append(row)
    except StopIteration as exc:
        raise ValueError("Empty pitcher-summary file") from exc
    except csv.Error as exc:
        raise ValueError(f"Malformed pitcher-summary CSV: {exc}") from exc
    frame = pd.DataFrame(rows, columns=headers, dtype="string")
    for column in frame:
        frame[column] = frame[column].str.strip()
        frame[column] = frame[column].mask(frame[column].str.lower().isin(NULLS))
    return frame


def archive_upload(source: Path, destination: Path) -> dict:
    """Preserve exact uploaded bytes; label origin separately from a direct download."""
    content = source.read_bytes()
    frame = read_summary(content)
    digest = sha256(content)
    manifest_path = destination.with_suffix(".manifest.json")
    if destination.exists() or manifest_path.exists():
        if not destination.exists() or not manifest_path.exists():
            raise ValueError("Incomplete raw archive; choose a new raw destination")
        manifest = json.loads(manifest_path.read_text())
        if (sha256(destination.read_bytes()) != digest or manifest["sha256"] != digest
                or manifest["source_type"] != "user_uploaded_pitcher_season_summary"):
            raise ValueError("Existing raw archive differs; refusing to overwrite it")
        return manifest
    manifest = {"source_type": "user_uploaded_pitcher_season_summary",
        "original_filename": source.name, "sha256": digest, "bytes": len(content),
        "rows": len(frame), "columns": frame.columns.tolist(),
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "export_url": None, "data_as_of": None, "game_type": None,
        "source_note": "User-supplied summary; export URL, date range, and game filters unverified."}
    _write_new(destination, content)
    _write_new(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def clean_pitcher_summary(source: Path, output: Path, *, raw_output: Path,
                          min_innings: int = 10) -> dict:
    if min_innings < 0:
        raise ValueError("Minimum innings must be nonnegative")
    output = output.resolve()
    if any(path.resolve().parent == output or output in path.resolve().parents
           for path in [source, raw_output]):
        raise ValueError("Keep processed output separate from source and raw archive")
    manifest = archive_upload(source, raw_output)
    frame = read_summary(raw_output.read_bytes())
    source_columns = frame.columns.tolist()
    empty_source_columns = sorted(c for c in source_columns if frame[c].isna().all())
    frame["_source_record"] = pd.Series(range(1, len(frame) + 1), dtype="Int64")
    duplicates = frame.duplicated(subset=source_columns)
    duplicate_count = int(duplicates.sum())
    frame = frame.loc[~duplicates].copy()
    parse_issues = {}
    flags = pd.Series("", index=frame.index, dtype="string")
    for column in sorted((INTEGERS | FLOATS) & set(frame)):
        numeric = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].notna() & (numeric.isna() | ~np.isfinite(numeric.astype(float)))
        if column in INTEGERS:
            invalid |= numeric.notna() & ((numeric % 1 != 0) | (numeric.abs() > 2**53 - 1))
        if invalid.any():
            parse_issues[column] = int(invalid.sum())
            flags.loc[invalid] += f"invalid_numeric:{column};"
        frame[column] = numeric.mask(invalid).astype("Int64" if column in INTEGERS else "Float64")
    reasons = pd.Series("", index=frame.index, dtype="string")

    def reject(mask, reason):
        reasons.loc[mask.fillna(True)] += reason + ";"

    for column in ["player_id", "year", "pa"]:
        reject(frame[column].isna() | (frame[column] <= 0), f"invalid_{column}")
    reject(frame["p_out"].isna() | (frame["p_out"] < 0), "invalid_p_out")
    reject(frame["p_out"].notna() & (frame["p_out"] < min_innings * 3), "below_minimum_innings")
    valid_keys = frame[["player_id", "year"]].notna().all(axis=1)
    reject(valid_keys & frame.duplicated(["player_id", "year"], keep=False), "conflicting_player_season")
    rejected = frame.loc[reasons.ne("")].copy()
    rejected["_rejection_reason"] = reasons.loc[rejected.index].str.rstrip(";")
    frame = frame.loc[reasons.eq("")].copy()
    flags = flags.loc[frame.index]
    checks = {}

    def check(name, columns, failure, *, positive=None):
        missing = sorted(set(columns) - set(frame))
        if missing:
            checks[name] = {"status": "not_run", "missing_columns": missing}
            return
        eligible = frame[columns].notna().all(axis=1)
        if positive:
            eligible &= frame[positive].gt(0).fillna(False)
        bad = failure(frame).fillna(False) & eligible
        flags.loc[bad] += name + ";"
        checks[name] = {"status": "evaluated", "evaluated_rows": int(eligible.sum()),
            "not_evaluated_rows": int((~eligible).sum()), "flagged_rows": int(bad.sum())}

    innings = frame["p_formatted_ip"].str.extract(r"^(\d+)(?:\.([012]))?$")
    parsed_outs = (pd.to_numeric(innings[0], errors="coerce") * 3
                   + pd.to_numeric(innings[1].fillna("0"), errors="coerce")).astype("Int64")
    bad_innings = parsed_outs.isna() | parsed_outs.ne(frame["p_out"]).fillna(False)
    flags.loc[bad_innings] += "innings_notation_or_outs_mismatch;"
    checks["innings_notation_or_outs_mismatch"] = {"status": "evaluated",
        "evaluated_rows": len(frame), "not_evaluated_rows": 0, "flagged_rows": int(bad_innings.sum())}
    check("hit_components_mismatch", ["hit", "single", "double", "triple", "home_run"],
          lambda f: f.hit.ne(f.single + f.double + f.triple + f.home_run))
    check("total_bases_mismatch", ["p_total_bases", "single", "double", "triple", "home_run"],
          lambda f: f.p_total_bases.ne(f.single + 2*f.double + 3*f.triple + 4*f.home_run))
    check("pitch_total_mismatch", ["p_total_pitches", "p_total_ball", "p_total_strike"],
          lambda f: f.p_total_pitches.ne(f.p_total_ball + f.p_total_strike))
    check("run_components_mismatch", ["p_run", "p_earned_run", "p_unearned_run"],
          lambda f: f.p_run.ne(f.p_earned_run + f.p_unearned_run))
    check("games_starts_relief_mismatch", ["p_game", "p_starting_p", "p_game_in_relief"],
          lambda f: f.p_game.ne(f.p_starting_p + f.p_game_in_relief))
    check("era_rounding_mismatch", ["p_era", "p_earned_run", "p_out"],
          lambda f: (f.p_era - 27*f.p_earned_run/f.p_out).abs().gt(.0050001), positive="p_out")
    for name, count in [("k_percent", "strikeout"), ("bb_percent", "walk")]:
        check(f"{name}_rounding_mismatch", [name, count, "pa"],
              lambda f, n=name, c=count: (f[n] - 100*f[c]/f.pa).abs().gt(.0500001), positive="pa")
    for name, count in [("batting_avg", "hit"), ("slg_percent", "p_total_bases")]:
        check(f"{name}_rounding_mismatch", [name, count, "ab"],
              lambda f, n=name, c=count: (f[n] - f[c]/f.ab).abs().gt(.0005001), positive="ab")
    numeric_columns = sorted((INTEGERS | FLOATS) & set(frame))
    negative = frame[numeric_columns].lt(0).any(axis=1)
    flags.loc[negative] += "negative_statistic;"
    checks["negative_statistic"] = {"status": "evaluated", "evaluated_rows": len(frame),
        "not_evaluated_rows": 0, "flagged_rows": int(negative.sum())}
    for column in sorted(PERCENTAGES & set(frame)):
        check(f"{column}_outside_0_100", [column], lambda f, c=column: ~f[c].between(0, 100))
        frame[column.removesuffix("_percent") + "_fraction"] = frame[column] / 100
    frame["innings_pitched_decimal"] = frame["p_out"] / 3
    derived_rates = {}
    for numerator, label in [("strikeout", "strikeouts_per_pa"), ("walk", "walks_per_pa"),
                             ("home_run", "home_runs_per_pa"), ("hit", "hits_per_pa")]:
        if numerator in frame:
            frame[label] = frame[numerator] / frame["pa"]
            derived_rates[label] = f"{numerator} / pa"
    # Preserve the essential schema even for an empty input.
    dropped_columns = sorted(set(empty_source_columns) - REQUIRED)
    frame = frame.drop(columns=dropped_columns)
    frame["_quality_flags"] = flags.str.rstrip(";")
    frame["has_quality_flags"] = frame["_quality_flags"].ne("")
    frame = frame.rename(columns={NAME: "player_name", "year": "season",
                                  "p_formatted_ip": "innings_pitched_display"})
    frame = frame.sort_values(["season", "player_id"], kind="stable").reset_index(drop=True)
    renamed = {"player_name": NAME, "season": "year", "innings_pitched_display": "p_formatted_ip"}
    fields = {c: {"dtype": str(frame[c].dtype), "missing": int(frame[c].isna().sum()),
                  "source_column": renamed.get(c, c) if renamed.get(c, c) in source_columns else None}
              for c in frame}
    report = {"dataset_type": "pitcher_season_summary", "schema_version": 1,
        "source": manifest, "raw_archive": str(raw_output), "input_rows": manifest["rows"],
        "input_columns": len(source_columns), "clean_rows": len(frame),
        "unique_pitchers": int(frame.player_id.nunique()), "seasons": sorted(frame.season.unique().tolist()),
        "minimum_innings_filter": min_innings, "rejected_rows": len(rejected),
        "exact_duplicates_removed": duplicate_count, "numeric_parse_issues": parse_issues,
        "all_missing_source_columns": empty_source_columns, "dropped_all_missing_columns": dropped_columns,
        "unknown_source_columns_preserved": sorted(set(source_columns) - KNOWN),
        "flagged_pitchers": int(frame.has_quality_flags.sum()), "checks": checks,
        "derived_rates": derived_rates, "columns": fields,
        "notes": ["Season labels come from the file; full MLB coverage and export filters are unverified.",
            "Innings display is baseball notation; decimal innings are p_out / 3.",
            "Percentage-point fields are preserved; *_fraction fields divide them by 100.",
            "Source discrepancies are flagged without changing the supplied totals.",
            "This is aggregate pitcher data, not pitch sequences or batter-vs-pitcher history.",
            "Full-season totals must not be used to predict earlier pitches from the same season.",
            "The innings threshold excludes lower-workload pitchers from this dataset."]}
    assert report["input_rows"] == len(frame) + len(rejected) + duplicate_count
    output.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output / "pitcher_profiles.parquet", index=False)
    frame.to_csv(output / "pitcher_profiles.csv", index=False)
    rejected.to_parquet(output / "rejected_rows.parquet", index=False)
    summary_columns = [c for c in ["player_id", "player_name", "season", "innings_pitched_display",
        "innings_pitched_decimal", "pa", "p_game", "p_era", "k_percent", "bb_percent",
        "strikeouts_per_pa", "walks_per_pa", "has_quality_flags", "_quality_flags"] if c in frame]
    frame[summary_columns].to_csv(output / "pitcher_summary.csv", index=False)
    flag_columns = [c for c in ["player_id", "player_name", "season", "p_game", "p_starting_p",
                               "p_game_in_relief", "_quality_flags"] if c in frame]
    frame.loc[frame.has_quality_flags, flag_columns].to_csv(output / "flagged_pitchers.csv", index=False)
    (output / "quality_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
