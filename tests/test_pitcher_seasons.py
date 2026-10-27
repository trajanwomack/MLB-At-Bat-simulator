import csv
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from mlb_at_bat.pitcher_seasons import NAME, archive_upload, clean_pitcher_summary


def row(**changes):
    values = {NAME: "Example, Pitcher", "player_id": "123", "year": "2026",
        "p_formatted_ip": "10.1", "p_out": "31", "pa": "50", "ab": "43",
        "p_game": "10", "p_starting_p": "1", "p_game_in_relief": "9",
        "hit": "10", "single": "7", "double": "2", "triple": "0", "home_run": "1",
        "p_total_bases": "15", "strikeout": "10", "walk": "5", "k_percent": "20.0",
        "bb_percent": "10.0", "batting_avg": ".233", "slg_percent": ".349",
        "p_total_pitches": "200", "p_total_ball": "80", "p_total_strike": "120",
        "p_earned_run": "3", "p_unearned_run": "1", "p_run": "4", "p_era": "2.61",
        "p_opp_batting_avg": "", "p_opp_on_base_avg": "null"}
    values.update(changes)
    return values


def write_source(tmp_path, rows):
    source = tmp_path / "uploaded.csv"
    keys = list(dict.fromkeys(k for r in rows for k in r)) if rows else list(row())
    with source.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return source


def clean(tmp_path, rows):
    source = write_source(tmp_path, rows)
    output = tmp_path / "processed"
    report = clean_pitcher_summary(source, output, raw_output=tmp_path / "raw/stats.csv")
    profiles = pd.read_parquet(output / "pitcher_profiles.parquet")
    return source, output, report, profiles


def test_innings_percentage_units_and_structural_blanks(tmp_path):
    source, output, report, profiles = clean(tmp_path, [row()])
    assert profiles.innings_pitched_display.tolist() == ["10.1"]
    assert profiles.innings_pitched_decimal.iloc[0] == pytest.approx(10 + 1/3)
    assert profiles.k_percent.iloc[0] == 20.0
    assert profiles.k_fraction.iloc[0] == .2
    assert profiles.strikeouts_per_pa.iloc[0] == .2
    assert str(profiles.player_id.dtype) == "Int64"
    assert profiles.player_name.tolist() == ["Example, Pitcher"]
    assert not profiles.has_quality_flags.any()
    assert report["dropped_all_missing_columns"] == ["p_opp_batting_avg", "p_opp_on_base_avg"]
    assert "p_opp_batting_avg" not in profiles
    raw = tmp_path / "raw/stats.csv"
    assert raw.read_bytes() == source.read_bytes()
    original = raw.read_bytes()
    assert clean_pitcher_summary(source, output, raw_output=raw) == report
    assert raw.read_bytes() == original


def test_games_discrepancy_is_flagged_without_editing_supplied_counts(tmp_path):
    _, output, report, profiles = clean(tmp_path, [row(p_game="10", p_starting_p="10", p_game_in_relief="1")])
    assert report["clean_rows"] == 1 and report["rejected_rows"] == 0
    assert report["flagged_pitchers"] == 1
    assert report["checks"]["games_starts_relief_mismatch"]["flagged_rows"] == 1
    assert profiles.p_game.iloc[0] == 10
    assert profiles.p_starting_p.iloc[0] == 10
    assert profiles.p_game_in_relief.iloc[0] == 1
    flags = pd.read_csv(output / "flagged_pitchers.csv")
    assert flags._quality_flags.tolist() == ["games_starts_relief_mismatch"]


def test_invalid_identity_threshold_and_conflicting_seasons_are_quarantined(tmp_path):
    _, output, report, profiles = clean(tmp_path, [
        row(player_id="bad"), row(player_id="2", p_out="29", p_formatted_ip="9.2"),
        row(player_id="3", p_out="31"), row(player_id="3", p_out="32", p_formatted_ip="10.2"),
        row(player_id="4"), row(player_id="4"),
    ])
    assert report["exact_duplicates_removed"] == 1
    assert report["rejected_rows"] == 4
    assert profiles.player_id.tolist() == [4]
    rejected = pd.read_parquet(output / "rejected_rows.parquet")
    assert rejected._rejection_reason.str.contains("conflicting_player_season").sum() == 2
    assert report["input_rows"] == 6


def test_malformed_innings_retained_with_flag_not_decimal_misinterpreted(tmp_path):
    _, _, report, profiles = clean(tmp_path, [row(p_formatted_ip="10.3")])
    assert profiles.innings_pitched_display.tolist() == ["10.3"]
    assert profiles.has_quality_flags.tolist() == [True]
    assert report["checks"]["innings_notation_or_outs_mismatch"]["flagged_rows"] == 1


def test_corrupt_cache_and_changed_upload_are_never_overwritten(tmp_path):
    source = write_source(tmp_path, [row()])
    target = tmp_path / "raw/stats.csv"
    manifest = archive_upload(source, target)
    assert manifest["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["source_type"] == "user_uploaded_pitcher_season_summary"
    assert manifest["export_url"] is None
    original = target.read_bytes()
    source.write_text(source.read_text(encoding="utf-8-sig").replace("2026", "2025"))
    with pytest.raises(ValueError, match="refusing to overwrite"):
        archive_upload(source, target)
    assert target.read_bytes() == original


def test_optional_missing_checks_are_not_reported_as_passing(tmp_path):
    required = {k: row()[k] for k in [NAME, "player_id", "year", "p_formatted_ip", "p_out", "pa"]}
    _, _, report, _ = clean(tmp_path, [required])
    assert report["checks"]["era_rounding_mismatch"]["status"] == "not_run"
    assert report["checks"]["era_rounding_mismatch"]["missing_columns"] == ["p_earned_run", "p_era"]


def test_unknown_fields_preserved_and_nonnumeric_optional_stat_flagged(tmp_path):
    _, _, report, profiles = clean(tmp_path, [row(p_era="invalid", future_metric="some-value")])
    assert profiles.p_era.isna().all()
    assert "p_era" not in report["dropped_all_missing_columns"]
    assert report["numeric_parse_issues"] == {"p_era": 1}
    assert report["checks"]["era_rounding_mismatch"]["evaluated_rows"] == 0
    assert report["checks"]["era_rounding_mismatch"]["not_evaluated_rows"] == 1
    assert profiles.future_metric.tolist() == ["some-value"]
    assert report["unknown_source_columns_preserved"] == ["future_metric"]


def test_wrong_dataset_type_and_overlapping_output_rejected_before_archive(tmp_path):
    source = tmp_path / "wrong.csv"
    source.write_text("game_pk,pitch_number\n123,1\n")
    raw = tmp_path / "raw/stats.csv"
    with pytest.raises(ValueError, match="Not a pitcher-season CSV"):
        clean_pitcher_summary(source, tmp_path / "out", raw_output=raw)
    assert not raw.exists()
    source = write_source(tmp_path, [row()])
    with pytest.raises(ValueError, match="separate"):
        clean_pitcher_summary(source, tmp_path, raw_output=raw)


def test_empty_summary_explicitly_reports_zero_pitchers(tmp_path):
    _, _, report, profiles = clean(tmp_path, [])
    assert report["input_rows"] == report["clean_rows"] == report["unique_pitchers"] == 0
    assert profiles.empty


def test_source_cannot_overwrite_derived_fields(tmp_path):
    with pytest.raises(ValueError, match="collide with derived fields"):
        clean(tmp_path, [row(season="2025")])
