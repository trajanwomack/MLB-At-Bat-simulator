"""Behavior and integration tests for the statistical pitch-selection baseline."""

import hashlib
import json
from pathlib import Path
import random

import pandas as pd
import pytest

from mlb_at_bat.cli import main
from mlb_at_bat.pitch_selection import (
    RepertoireUnavailable,
    build_pitch_baseline,
    compare_pitch_frequencies,
    write_pitch_baseline,
)


ROOT = Path(__file__).resolve().parents[1]


def arsenal(usages=(60.0, 40.0), codes=("FF", "SL")):
    return pd.DataFrame({
        "player_id": [123] * len(usages),
        "season": [2026] * len(usages),
        "player_name": ["Example, Pitcher"] * len(usages),
        "pitch_type": list(codes),
        "usage_percent": list(usages),
        "arsenal_pitch_total": [1000] * len(usages),
    })


def test_exact_distribution_and_lookup_preserve_source_and_return_a_copy():
    source = arsenal()
    original = source.copy(deep=True)
    baseline = build_pitch_baseline(source)
    lookup = baseline.repertoire(123, 2026)
    assert lookup.sampling_probability.tolist() == [.6, .4]
    assert lookup.usage_percent.tolist() == [60, 40]
    assert baseline.repertoires.quality_status.tolist() == ["ready"]
    assert baseline.repertoires.arsenal_pitch_total.tolist() == [1000]
    lookup.loc[0, "sampling_probability"] = 0
    assert baseline.repertoire(123, 2026).sampling_probability.iloc[0] == .6
    pd.testing.assert_frame_equal(source, original)


@pytest.mark.parametrize("usages", [
    (33.3, 33.3, 33.3), (33.4, 33.4, 33.3),
    (25.0, 25.0, 25.1, 25.1), (25.0, 25.0, 24.9, 24.9),
])
def test_only_rounding_differences_are_normalized(usages):
    source = arsenal(usages, ("FF", "SL", "CH", "CU")[:len(usages)])
    baseline = build_pitch_baseline(source)
    lookup = baseline.repertoire(123, 2026)
    expected = source.sort_values("pitch_type").usage_percent.tolist()
    assert lookup.usage_percent.tolist() == expected
    assert lookup.sampling_probability.tolist() == pytest.approx([u / sum(usages) for u in expected])
    assert lookup.sampling_probability.sum() == pytest.approx(1)
    assert lookup.quality_status.unique().tolist() == ["normalized_rounding"]
    assert baseline.repertoires.rounding_tolerance_percent.iloc[0] == pytest.approx(.05 * len(usages))


@pytest.mark.parametrize(("usages", "status"), [
    ((60, pd.NA), "unavailable_missing_usage"),
    ((pd.NA, pd.NA), "unavailable_missing_usage"),
    ((99.9, pd.NA), "unavailable_missing_usage"),
    ((0, 0), "unavailable_zero_usage"),
    ((60, 20), "unavailable_incomplete_usage"),
    ((60, 40.2), "unavailable_incomplete_usage"),
])
def test_unavailable_groups_never_get_partial_or_invented_probabilities(usages, status):
    baseline = build_pitch_baseline(arsenal(usages))
    assert baseline.repertoires.quality_status.tolist() == [status]
    assert not baseline.repertoires.is_available.iloc[0]
    assert baseline.probabilities.sampling_probability.isna().all()
    with pytest.raises(RepertoireUnavailable, match=status):
        baseline.sample_pitches(123, 2026, n_pitches=10)


def test_cohort_retains_absent_repertoires_including_an_entirely_empty_arsenal():
    cohort = pd.DataFrame({"player_id": [123, 456], "season": [2026, 2026], "player_name": ["A", "B"]})
    baseline = build_pitch_baseline(arsenal(), pitchers=cohort)
    assert baseline.repertoires.quality_status.tolist() == ["ready", "unavailable_no_repertoire"]
    with pytest.raises(RepertoireUnavailable, match="unavailable_no_repertoire"):
        baseline.repertoire(456, 2026)
    empty = build_pitch_baseline(arsenal().iloc[:0], pitchers=cohort)
    assert empty.probabilities.empty
    assert empty.repertoires.quality_status.tolist() == ["unavailable_no_repertoire"] * 2
    with pytest.raises(RepertoireUnavailable, match="not found"):
        baseline.repertoire(999, 2026)


@pytest.mark.parametrize(("column", "value", "message"), [
    ("player_id", pd.NA, "missing player_id"),
    ("player_id", 0, "Invalid numeric"),
    ("season", 2026.5, "Invalid numeric"),
    ("usage_percent", -1, "between 0 and 100"),
    ("usage_percent", 101, "between 0 and 100"),
    ("usage_percent", float("inf"), "Invalid numeric"),
    ("usage_percent", "bad", "Invalid numeric"),
    ("usage_percent", True, "Invalid numeric"),
    ("usage_percent", 60.01, "one decimal"),
    ("arsenal_pitch_total", 0, "Invalid numeric"),
    ("arsenal_pitch_total", 10.5, "Invalid numeric"),
    ("pitch_type", "FASTBALL", "unknown pitch type or broad pitch family"),
    ("pitch_type", "XX", "unknown pitch type"),
    ("pitch_type", pd.NA, "unknown pitch type"),
])
def test_invalid_units_labels_or_keys_are_not_silently_coerced(column, value, message):
    source = arsenal().astype(object)
    source.loc[0, column] = value
    with pytest.raises(ValueError, match=message):
        build_pitch_baseline(source)


def test_ambiguous_duplicate_keys_and_conflicting_workload_are_rejected():
    source = arsenal()
    with pytest.raises(ValueError, match="duplicate pitcher-season-pitch"):
        build_pitch_baseline(pd.concat([source, source.iloc[:1]]))
    source.loc[1, "arsenal_pitch_total"] = 2000
    with pytest.raises(ValueError, match="inconsistent arsenal_pitch_total"):
        build_pitch_baseline(source)
    cohort = arsenal()[["player_id", "season", "player_name"]]
    with pytest.raises(ValueError, match="duplicate player-season"):
        build_pitch_baseline(arsenal(), pitchers=cohort)
    cohort = cohort.iloc[:1].assign(player_id=999)
    with pytest.raises(ValueError, match="outside the supplied cohort"):
        build_pitch_baseline(arsenal(), pitchers=cohort)


def test_missing_workload_stays_missing_without_inventing_pitch_type_counts():
    source = arsenal()
    source["arsenal_pitch_total"] = pd.NA
    baseline = build_pitch_baseline(source)
    assert baseline.repertoires.arsenal_pitch_total.isna().all()
    assert baseline.repertoire(123, 2026).sampling_probability.sum() == pytest.approx(1)
    assert baseline.probabilities.arsenal_pitch_total.isna().all()


def test_lookup_keeps_pitchers_and_seasons_separate():
    first = arsenal()
    second = arsenal((10, 90)).assign(season=2025)
    third = arsenal((80, 20)).assign(player_id=456)
    baseline = build_pitch_baseline(pd.concat([first, second, third], ignore_index=True))
    assert baseline.repertoire(123, 2026).sampling_probability.tolist() == [.6, .4]
    assert baseline.repertoire(123, 2025).sampling_probability.tolist() == [.1, .9]
    assert baseline.repertoire(456, 2026).sampling_probability.tolist() == [.8, .2]


def test_sampling_is_seeded_order_independent_and_does_not_touch_global_random_state():
    source = arsenal((50, 30, 20), ("FF", "SL", "CH"))
    baseline = build_pitch_baseline(source)
    global_state = random.getstate()
    first = baseline.sample_pitches(123, 2026, n_pitches=200, seed=42)
    second = build_pitch_baseline(source.iloc[::-1]).sample_pitches(123, 2026, n_pitches=200, seed=42)
    pd.testing.assert_frame_equal(first, second)
    assert random.getstate() == global_state
    assert first.pitch_number.tolist() == list(range(1, 201))
    assert set(first.pitch_type) == {"FF", "SL", "CH"}
    assert not first.pitch_type.equals(baseline.sample_pitches(123, 2026, n_pitches=200, seed=43).pitch_type)
    assert len(baseline.sample_pitches(123, 2026)) == 1


def test_zero_usage_is_preserved_but_never_sampled_and_empty_requests_are_defined():
    baseline = build_pitch_baseline(arsenal((100, 0)))
    repertoire = baseline.repertoire(123, 2026)
    samples = baseline.sample_pitches(123, 2026, n_pitches=1000, seed=42)
    assert set(samples.pitch_type) == {"FF"}
    comparison = compare_pitch_frequencies(repertoire, samples)
    assert comparison.sampled_count.tolist() == [1000, 0]
    empty = baseline.sample_pitches(123, 2026, n_pitches=0, seed=42)
    empty_comparison = compare_pitch_frequencies(repertoire, empty)
    assert empty.empty and empty.columns.tolist() == samples.columns.tolist()
    assert empty_comparison.sampled_count.tolist() == [0, 0]
    assert empty_comparison.sampled_percent.isna().all()


@pytest.mark.parametrize("kwargs", [
    {"n_pitches": -1}, {"n_pitches": 1.5}, {"n_pitches": True},
    {"seed": -1}, {"seed": "42"}, {"player_id": True}, {"season": 2026.5},
])
def test_sampling_arguments_are_checked(kwargs):
    arguments = {"player_id": 123, "season": 2026, **kwargs}
    with pytest.raises(ValueError, match="must be an integer"):
        build_pitch_baseline(arsenal()).sample_pitches(**arguments)


def test_large_sample_recovers_distribution_and_comparison_uses_probability_not_raw_percent():
    baseline = build_pitch_baseline(arsenal((60, 30, 9.9), ("FF", "SL", "CH")))
    repertoire = baseline.repertoire(123, 2026)
    samples = baseline.sample_pitches(123, 2026, n_pitches=50000, seed=42)
    comparison = compare_pitch_frequencies(repertoire, samples)
    assert comparison.sampled_count.sum() == 50000
    assert comparison.sampled_percent.sum() == pytest.approx(100)
    assert (comparison.difference_percentage_points.abs() < 1.5).all()
    assert comparison.expected_count.sum() == pytest.approx(50000)
    wrong = samples.iloc[:1].assign(player_id=999)
    with pytest.raises(ValueError, match="different pitcher-season"):
        compare_pitch_frequencies(repertoire, wrong)
    with pytest.raises(ValueError, match="outside this repertoire"):
        compare_pitch_frequencies(repertoire, samples.iloc[:1].assign(pitch_type="KN"))


def test_export_tracks_input_hashes_and_cli_round_trip(tmp_path, capsys):
    source = tmp_path / "inputs"
    source.mkdir()
    arsenal_path, pitchers_path = source / "arsenal.csv", source / "pitchers.csv"
    arsenal().to_csv(arsenal_path, index=False)
    arsenal().iloc[:1][["player_id", "season", "player_name"]].to_csv(pitchers_path, index=False)
    args = ["--arsenal", str(arsenal_path), "--pitchers", str(pitchers_path)]
    output = tmp_path / "baseline"
    assert main(["build-pitch-baseline", *args, "--output", str(output)]) == 0
    report = json.loads((output / "quality_report.json").read_text())
    assert report["sources"]["arsenal"]["sha256"] == hashlib.sha256(arsenal_path.read_bytes()).hexdigest()
    assert report["available_pitcher_seasons"] == 1
    assert pd.read_parquet(output / "pitch_selection_probabilities.parquet").sampling_probability.tolist() == [.6, .4]
    original = (output / "pitch_selection_probabilities.csv").read_bytes()
    assert write_pitch_baseline(arsenal_path, pitchers_path, output) == report
    assert (output / "pitch_selection_probabilities.csv").read_bytes() == original
    sampled_output = tmp_path / "sampled"
    assert main(["sample-pitches", *args, "--player-id", "123", "--season", "2026",
                 "--n-pitches", "100", "--seed", "42", "--output", str(sampled_output)]) == 0
    assert len(pd.read_csv(sampled_output / "sampled_pitches.csv")) == 100
    assert pd.read_csv(sampled_output / "frequency_comparison.csv").sampled_count.sum() == 100
    assert json.loads((sampled_output / "simulation.json").read_text())["seed"] == 42
    assert main(["sample-pitches", *args, "--player-id", "999", "--season", "2026"]) == 1
    assert "not found" in capsys.readouterr().err
    with pytest.raises(ValueError, match="must not contain a source input"):
        write_pitch_baseline(arsenal_path, pitchers_path, source)


def test_all_versioned_pitchers_have_valid_reusable_probabilities(tmp_path):
    source = pd.read_csv(ROOT / "datasets/2026/views/pitch_arsenal_types.csv")
    pitchers = pd.read_csv(ROOT / "datasets/2026/views/pitchers.csv")
    baseline = build_pitch_baseline(source, pitchers=pitchers)
    assert len(baseline.repertoires) == 649
    assert len(baseline.probabilities) == 3174
    assert baseline.repertoires.is_available.all()
    assert baseline.probabilities.groupby(["player_id", "season"]).sampling_probability.sum().tolist() == pytest.approx([1] * 649)
    assert (baseline.probabilities.sampling_probability >= 0).all()
    assert (baseline.probabilities.sampling_probability <= 1).all()
    assert baseline.probabilities.usage_percent.tolist() == source.usage_percent.tolist()
    assert baseline.probabilities.usage_percent.eq(0).sum() == 5
    write_pitch_baseline(
        ROOT / "datasets/2026/views/pitch_arsenal_types.csv",
        ROOT / "datasets/2026/views/pitchers.csv", tmp_path,
    )
    for name in ("pitch_selection_probabilities.csv", "pitcher_repertoires.csv", "quality_report.json"):
        assert (tmp_path / name).read_bytes() == (ROOT / "datasets/2026/baselines" / name).read_bytes()
