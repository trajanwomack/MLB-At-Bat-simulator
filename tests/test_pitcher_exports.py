import csv
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from mlb_at_bat.pitcher_exports import (NAME, archive_export, arsenal_long,
                                       build_master, clean_pitcher_exports, read_export)


def write_csv(path, rows):
    columns = list(dict.fromkeys(c for row in rows for c in row))
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def source_row(**changes):
    return {NAME: "Example, Pitcher", "player_id": "123", "year": "2026", **changes}


def make_sources(tmp_path):
    return {name: write_csv(tmp_path / f"{name}_upload.csv", [row]) for name, row in {
        "stats": source_row(p_out=30, p_formatted_ip="10.0", pa=50,
            p_total_pitches=200, hard_hit_percent=40, whiff_percent=20, swing_percent=50),
        "bat_tracking": source_row(avg_swing_speed=72, attack_direction=-3, swords=2),
        "contact": source_row(barrel=1, hard_hit_percent=40, launch_angle_avg=-1),
        "location": source_row(pitch_count=200, pitch_count_fastball=120,
            pitch_count_breaking=60, pitch_count_offspeed=10, in_zone=110, out_zone=85,
            whiff_percent=20, swing_percent=50),
        "arsenal": source_row(pitch_hand="R", n=190, arm_angle=-7, n_ff_formatted=60,
            n_sl_formatted=40, ff_avg_speed=95, sl_avg_speed=85, ff_avg_break_z=-20,
            n_fastball_formatted=60, n_breaking_formatted=40),
    }.items()}


def test_signed_angles_and_movement_are_preserved(tmp_path):
    p = write_csv(tmp_path / "bat.csv", [source_row(attack_direction=-3, attack_angle=-5, swords=2)])
    frame, report = read_export(p, "bat_tracking")
    assert frame.attack_direction.tolist() == [-3]
    assert frame.attack_angle.tolist() == [-5]
    assert str(frame.swords.dtype) == "Int64"
    a = write_csv(tmp_path / "arsenal.csv", [source_row(pitch_hand="l", n=200,
        n_ff_formatted=100, arm_angle=-7, ff_avg_break_z=-20)])
    frame, _ = read_export(a, "arsenal")
    assert frame.ff_avg_break_z.tolist() == [-20]
    assert frame.pitch_hand.tolist() == ["L"]


def test_formatted_usage_is_percentage_and_families_do_not_double_long_rows(tmp_path):
    sources = make_sources(tmp_path)
    frame, _ = read_export(sources["arsenal"], "arsenal")
    assert frame.arsenal_pitch_total.tolist() == [190]
    assert frame.ff_usage_percent.tolist() == [60]
    long = arsenal_long(frame)
    assert long.pitch_type.tolist() == ["FF", "SL"]
    assert long.usage_fraction.tolist() == [.6, .4]
    assert long.arsenal_pitch_total.tolist() == [190, 190]
    assert "pitch_count" not in long
    assert len(long) == 2


def test_master_left_join_preserves_missing_cohort_members_and_deduplicates_agreement():
    stats = pd.DataFrame({"player_id": [1, 2], "season": [2026, 2026],
        "player_name": ["A", "B"], "hard_hit_percent": [40.0, 45.0]})
    contact = pd.DataFrame({"player_id": [1], "season": [2026], "player_name": ["A"],
        "hard_hit_percent": [40.0], "barrel": [2]})
    result, report = build_master(stats, {"contact": contact})
    assert len(result) == 2
    assert result.has_contact.tolist() == [True, False]
    assert result.loc[1, "contact__barrel"] != 0
    assert pd.isna(result.loc[1, "contact__barrel"])
    assert "contact__hard_hit_percent" not in result
    assert report["coverage"]["contact"]["missing_stats_rows"] == 1
    assert report["shared_metrics"]["contact.hard_hit_percent"]["action"] == "use_stats_column"


def test_conflicting_shared_metrics_keep_both_sources():
    stats = pd.DataFrame({"player_id": [1], "season": [2026], "player_name": ["A"], "hard_hit_percent": [40.0]})
    contact = stats.copy()
    contact["hard_hit_percent"] = 41.0
    result, report = build_master(stats, {"contact": contact})
    assert result.hard_hit_percent.tolist() == [40.0]
    assert result.contact__hard_hit_percent.tolist() == [41.0]
    assert report["shared_metrics"]["contact.hard_hit_percent"]["differing_rows"] == 1


def test_invalid_keys_counts_units_and_duplicate_records_cannot_expand_join(tmp_path):
    for changes, pattern in [({"player_id": "bad"}, "invalid numeric"),
                             ({"swords": "2.5"}, "invalid numeric"),
                             ({"fast_swing_rate": "101"}, "invalid numeric")]:
        path = write_csv(tmp_path / "bad.csv", [source_row(**{"swords": 2, **changes})])
        with pytest.raises(ValueError, match=pattern):
            read_export(path, "bat_tracking")
    path = write_csv(tmp_path / "duplicate.csv", [source_row(swords=2), source_row(swords=3)])
    with pytest.raises(ValueError, match="duplicate player-season"):
        read_export(path, "bat_tracking")


def test_archive_preserves_original_bytes_and_refuses_replacement(tmp_path):
    source = write_csv(tmp_path / "source.csv", [source_row(swords=2)])
    target = tmp_path / "raw/bat.csv"
    first = archive_export(source, target, "bat_tracking")
    assert first["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert target.read_bytes() == source.read_bytes()
    assert archive_export(source, target, "bat_tracking") == first
    source = write_csv(source, [source_row(swords=3)])
    with pytest.raises(ValueError, match="refusing to overwrite"):
        archive_export(source, target, "bat_tracking")


def test_full_pipeline_reports_denominator_differences_and_is_repeatable(tmp_path):
    sources = make_sources(tmp_path)
    before = {name: p.read_bytes() for name, p in sources.items()}
    output = tmp_path / "processed"
    raw = tmp_path / "raw"
    report = clean_pitcher_exports(sources, raw, output)
    assert report["master_pitchers"] == 1
    assert report["arsenal_pitch_type_rows"] == 2
    assert all(x["missing_stats_rows"] == 0 for x in report["joins"]["coverage"].values())
    compare = report["count_comparisons"]
    assert compare["arsenal_vs_location_total_pitches"]["different_rows"] == 1
    assert compare["arsenal_vs_location_total_pitches"]["left_minus_right_min"] == -10
    assert compare["unassigned_pitch_family"]["different_rows"] == 1
    assert report["arsenal_usage"]["outside_rounding_tolerance"] == 0
    master = pd.read_parquet(output / "pitcher_master.parquet")
    assert master.arsenal__arsenal_pitch_total.tolist() == [190]
    assert master.location__pitch_count.tolist() == [200]
    assert master.has_source_total_difference.tolist() == [True]
    differences = pd.read_csv(output / "source_differences.csv")
    assert set(differences.check) == {"arsenal_vs_location_total_pitches", "unassigned_zone_pitches", "unassigned_pitch_family"}
    assert set(differences.player_id) == {123}
    assert pd.read_csv(output / "pitch_arsenal_types.csv").pitch_type.tolist() == ["FF", "SL"]
    second = clean_pitcher_exports(sources, raw, output)
    assert second == report
    pd.testing.assert_frame_equal(master, pd.read_parquet(output / "pitcher_master.parquet"))
    assert {name:p.read_bytes() for name,p in sources.items()} == before


def test_sources_outside_master_are_visible_in_coverage_report():
    stats = pd.DataFrame({"player_id": [1], "season": [2026], "player_name": ["A"]})
    extra = pd.DataFrame({"player_id": [1, 2], "season": [2026, 2026],
        "player_name": ["A", "B"], "swords": [2, 3]})
    result, report = build_master(stats, {"bat_tracking": extra})
    assert len(result) == 1
    assert report["coverage"]["bat_tracking"]["outside_stats_cohort"] == 1
