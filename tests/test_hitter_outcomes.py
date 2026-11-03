"""Count reconciliation, independent sampling, and export integration checks."""

import hashlib
import json
from pathlib import Path
import random

import pandas as pd
import pytest

from mlb_at_bat.cli import main
from mlb_at_bat.hitter_outcomes import (
    HitterBaselineUnavailable,
    OUTCOME_COLUMNS,
    build_hitter_baseline,
    compare_outcome_frequencies,
    load_hitter_baseline,
    write_hitter_baseline,
)

ROOT = Path(__file__).resolve().parents[1]


def hitter(**changes):
    return pd.DataFrame([{
        "player_id": 123, "season": 2026, "player_name": "Example, Hitter",
        "pa": 100, "ab": 88, "hit": 30, "strikeout": 20, "walk": 10,
        "b_hit_by_pitch": 2, "single": 20, "double": 5, "triple": 0,
        "home_run": 5, "b_intent_walk": 3, "batting_avg": .341,
        "b_reached_on_error": 1, "b_sac_fly": 1, **changes,
    }])


def test_counts_use_pa_not_ab_and_do_not_add_ibb_or_reclassify_other_events():
    source = hitter()
    original = source.copy(deep=True)
    baseline = build_hitter_baseline(source)
    distribution = baseline.distribution(123, 2026)
    assert distribution.outcome.tolist() == list(OUTCOME_COLUMNS)
    assert distribution.outcome_count.tolist() == [20, 10, 2, 20, 5, 0, 5, 38]
    assert distribution.probability.tolist() == pytest.approx([.2, .1, .02, .2, .05, 0, .05, .38])
    assert distribution.probability.sum() == pytest.approx(1)
    assert baseline.profiles.hit_components_agree.all()
    assert baseline.profiles.other_pa_outcome.tolist() == [38]
    assert "out" not in distribution.outcome.tolist()
    pd.testing.assert_frame_equal(source, original)
    distribution.loc[0, "probability"] = 0
    assert baseline.distribution(123, 2026).probability.iloc[0] == .2


@pytest.mark.parametrize(("changes", "status"), [
    ({"hit": pd.NA}, "unavailable_missing_counts"),
    ({"single": pd.NA}, "unavailable_missing_counts"),
    ({"b_hit_by_pitch": pd.NA}, "unavailable_missing_counts"),
    ({"pa": pd.NA}, "unavailable_missing_counts"),
    ({"pa": 0}, "unavailable_zero_pa"),
    ({"single": 21}, "unavailable_hit_mismatch"),
    ({"pa": 50}, "unavailable_negative_remainder"),
])
def test_missing_or_inconsistent_rows_remain_visible_and_cannot_be_sampled(changes, status):
    baseline = build_hitter_baseline(hitter(**changes))
    assert len(baseline.probabilities) == 8
    assert baseline.profiles.quality_status.tolist() == [status]
    assert baseline.probabilities.probability.isna().all()
    assert not baseline.profiles.is_available.iloc[0]
    with pytest.raises(HitterBaselineUnavailable, match=status):
        baseline.sample_plate_appearances(123, 2026)
    if "b_hit_by_pitch" in changes:
        assert baseline.profiles.b_hit_by_pitch.isna().all()
        assert baseline.profiles.other_pa_outcome.isna().all()
    if status == "unavailable_negative_remainder":
        assert baseline.profiles.other_pa_outcome.iloc[0] == -12


@pytest.mark.parametrize(("column", "value"), [
    ("walk", -1), ("single", 1.5), ("b_hit_by_pitch", "invalid"),
    ("hit", float("inf")), ("strikeout", True), ("pa", 2**54),
    ("player_id", 0), ("season", pd.NA),
])
def test_malformed_counts_and_keys_raise_instead_of_silently_changing_values(column, value):
    source = hitter().astype(object)
    source.loc[0, column] = value
    with pytest.raises(ValueError):
        build_hitter_baseline(source)


def test_missing_columns_duplicate_keys_and_empty_input_are_explicit_errors():
    with pytest.raises(ValueError, match="requires unique columns"):
        build_hitter_baseline(hitter().drop(columns="walk"))
    with pytest.raises(ValueError, match="duplicate player-season"):
        build_hitter_baseline(pd.concat([hitter(), hitter()], ignore_index=True))
    with pytest.raises(ValueError, match="No hitter-seasons"):
        build_hitter_baseline(hitter().iloc[:0])


def test_sample_size_threshold_is_a_flag_and_keys_isolate_hitters_and_seasons():
    source = pd.concat([hitter(), hitter(season=2025, pa=101), hitter(player_id=456, pa=99)], ignore_index=True)
    baseline = build_hitter_baseline(source)
    assert len(baseline.profiles) == 3
    assert baseline.distribution(123, 2026).meets_min_pa.all()
    assert baseline.distribution(123, 2025).probability.iloc[0] == pytest.approx(20/101)
    assert not baseline.distribution(456, 2026).meets_min_pa.any()
    assert len(baseline.sample_plate_appearances(456, 2026, n_pa=100, seed=42)) == 100
    stricter = build_hitter_baseline(source, min_pa=200)
    assert not stricter.profiles.meets_min_pa.any()
    pd.testing.assert_series_equal(baseline.probabilities.probability, stricter.probabilities.probability)
    with pytest.raises(HitterBaselineUnavailable, match="not found"):
        baseline.distribution(999, 2026)


def test_seeded_sampling_is_reproducible_and_does_not_change_global_random_state():
    baseline = build_hitter_baseline(hitter())
    state = random.getstate()
    first = baseline.sample_plate_appearances(123, 2026, n_pa=100, seed=42)
    second = baseline.sample_plate_appearances(123, 2026, n_pa=100, seed=42)
    pd.testing.assert_frame_equal(first, second)
    assert random.getstate() == state
    assert first.pa_number.tolist() == list(range(1, 101))
    assert not first.outcome.eq("triple").any()
    assert not first.outcome.equals(baseline.sample_plate_appearances(123, 2026, n_pa=100, seed=43).outcome)
    assert len(baseline.sample_plate_appearances(123, 2026)) == 100


def test_zero_counts_stay_zero_and_empty_samples_have_no_observed_percentage():
    baseline = build_hitter_baseline(hitter())
    empty = baseline.sample_plate_appearances(123, 2026, n_pa=0, seed=42)
    comparison = compare_outcome_frequencies(baseline.distribution(123, 2026), empty)
    assert empty.empty
    assert len(comparison) == 8
    assert comparison.sampled_count.eq(0).all()
    assert comparison.expected_count.eq(0).all()
    assert comparison.sampled_percent.isna().all()
    assert comparison.loc[comparison.outcome.eq("triple"), "probability"].iloc[0] == 0


@pytest.mark.parametrize("kwargs", [
    {"n_pa": -1}, {"n_pa": 1.5}, {"n_pa": True}, {"seed": -1},
    {"seed": "42"}, {"player_id": True}, {"season": 2026.5},
])
def test_invalid_sampling_arguments_raise(kwargs):
    with pytest.raises(ValueError, match="must be an integer"):
        build_hitter_baseline(hitter()).sample_plate_appearances(**{
            "player_id": 123, "season": 2026, **kwargs,
        })


def test_large_sample_agrees_with_distribution_and_comparison_rejects_mixed_inputs():
    baseline = build_hitter_baseline(hitter())
    distribution = baseline.distribution(123, 2026)
    samples = baseline.sample_plate_appearances(123, 2026, n_pa=50000, seed=42)
    comparison = compare_outcome_frequencies(distribution, samples)
    assert comparison.sampled_count.sum() == 50000
    assert comparison.expected_count.sum() == pytest.approx(50000)
    assert comparison.sampled_percent.sum() == pytest.approx(100)
    assert comparison.difference_percentage_points.abs().max() < 1.5
    with pytest.raises(ValueError, match="different hitter-season"):
        compare_outcome_frequencies(distribution, samples.iloc[:1].assign(player_id=456))
    with pytest.raises(ValueError, match="outside this distribution"):
        compare_outcome_frequencies(distribution, samples.iloc[:1].assign(outcome="unknown"))


def test_csv_parquet_and_cli_exports_preserve_provenance_and_seed(tmp_path, capsys):
    source = tmp_path / "inputs"
    source.mkdir()
    path = source / "hitters.csv"
    hitter().to_csv(path, index=False)
    original = path.read_bytes()
    output = tmp_path / "baseline"
    assert main(["build-hitter-baseline", "--hitters", str(path), "--output", str(output)]) == 0
    report = json.loads((output / "quality_report.json").read_text())
    assert report["available_hitter_seasons"] == 1
    assert report["sources"]["hitters"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert write_hitter_baseline(path, output) == report
    assert pd.read_parquet(output / "hitter_outcome_profiles.parquet").other_pa_outcome.tolist() == [38]
    parquet = source / "hitters.parquet"
    hitter().to_parquet(parquet, index=False)
    pd.testing.assert_frame_equal(load_hitter_baseline(path).probabilities, load_hitter_baseline(parquet).probabilities)
    samples = tmp_path / "samples"
    arguments = ["sample-plate-appearances", "--hitters", str(path), "--player-id", "123",
                 "--season", "2026", "--n-pa", "100", "--seed", "42", "--output", str(samples)]
    assert main(arguments) == 0
    assert len(pd.read_csv(samples / "sampled_plate_appearances.csv")) == 100
    assert pd.read_csv(samples / "outcome_frequency_comparison.csv").sampled_count.sum() == 100
    assert json.loads((samples / "simulation.json").read_text())["seed"] == 42
    assert main(["sample-plate-appearances", "--hitters", str(path), "--player-id", "999", "--season", "2026"]) == 1
    assert "not found" in capsys.readouterr().err
    with pytest.raises(ValueError, match="must not contain the source input"):
        write_hitter_baseline(path, source)
    assert path.read_bytes() == original


def test_all_versioned_hitters_reconcile_and_snapshots_are_reproducible(tmp_path):
    path = ROOT / "datasets/2026/views/hitters.csv"
    source = pd.read_csv(path)
    baseline = build_hitter_baseline(source)
    assert len(baseline.profiles) == 630
    assert len(baseline.probabilities) == 5040
    assert baseline.profiles.is_available.all()
    assert baseline.profiles.meets_min_pa.sum() == 465
    assert baseline.profiles.hit_components_agree.all()
    assert baseline.profiles.other_pa_outcome.ge(0).all()
    totals = baseline.probabilities.groupby(["player_id", "season"]).outcome_count.sum()
    assert totals.tolist() == baseline.profiles.pa.tolist()
    assert baseline.probabilities.groupby(["player_id", "season"]).probability.sum().tolist() == pytest.approx([1] * 630)
    assert baseline.probabilities.probability.between(0, 1).all()
    write_hitter_baseline(path, tmp_path)
    snapshots = ROOT / "datasets/2026/baselines/hitter_outcomes"
    for name in ["hitter_outcome_probabilities.csv", "hitter_outcome_profiles.csv", "quality_report.json"]:
        assert (tmp_path / name).read_bytes() == (snapshots / name).read_bytes()
