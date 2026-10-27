import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd
import pytest

from mlb_at_bat import data
from mlb_at_bat.cli import main


FIELDS = ["game_pk", "at_bat_number", "pitch_number", "game_date", "batter", "pitcher",
          "balls", "strikes", "pitch_type", "description", "events", "release_speed",
          "launch_speed", "inning", "outs_when_up"]


def pitch(number=1, **changes):
    row = dict(zip(FIELDS, [100, 1, number, "2025-04-01", 10, 20,
                           0, number - 1, "SL", "swinging_strike", "null", 85,
                           "null", 1, 0]))
    row.update(changes)
    return row


def write_csv(path, rows, fields=FIELDS):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_clean(tmp_path, rows):
    source = write_csv(tmp_path / "raw.csv", rows)
    report = data.clean_exports([source], tmp_path / "processed")
    pitches = pd.read_parquet(tmp_path / "processed/pitches.parquet")
    pas = pd.read_parquet(tmp_path / "processed/plate_appearances.parquet")
    return report, pitches, pas


def test_reconstructs_sequence_without_dropping_structural_nulls(tmp_path):
    rows = [pitch(3, events="strikeout"), pitch(1), pitch(2)]
    source = write_csv(tmp_path / "raw.csv", rows)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report = data.clean_exports([source], tmp_path / "processed")
    pitches = pd.read_parquet(tmp_path / "processed/pitches.parquet")
    pas = pd.read_parquet(tmp_path / "processed/plate_appearances.parquet")
    assert pitches.pitch_number.tolist() == [1, 2, 3]
    assert str(pitches.pitcher.dtype) == "Int64"
    assert pitches.events.isna().sum() == 2
    assert pitches.launch_speed.isna().sum() == 3
    assert pas.outcome.tolist() == ["strikeout"]
    assert pas.outcome_eligible.tolist() == [True]
    assert report["input_rows"] == report["clean_pitch_rows"] == 3
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    # Re-running produces the same data and report and leaves the source untouched.
    again = data.clean_exports([source], tmp_path / "processed")
    assert again == report
    pd.testing.assert_frame_equal(pitches, pd.read_parquet(tmp_path / "processed/pitches.parquet"))


def test_invalid_context_quarantines_row_and_excludes_incomplete_pa(tmp_path):
    report, pitches, pas = run_clean(tmp_path, [pitch(1), pitch(2, balls=4), pitch(3, events="strikeout")])
    assert pitches.pitch_number.tolist() == [1, 3]
    assert report["rejection_reasons"] == {"invalid_balls": 1}
    assert pas.has_rejected_rows.tolist() == [True]
    assert pas.sequence_complete.tolist() == [False]
    assert pas.outcome_eligible.tolist() == [False]
    rejected = pd.read_parquet(tmp_path / "processed/rejected_rows.parquet")
    assert rejected._source_record.tolist() == [2]


def test_exact_duplicates_removed_but_conflicts_never_arbitrarily_resolved(tmp_path):
    report, pitches, _ = run_clean(tmp_path, [pitch(1), pitch(1),
        pitch(2, release_speed=80), pitch(2, release_speed=90)])
    assert report["exact_duplicate_rows_removed"] == 1
    assert report["conflicting_key_rows_quarantined"] == 2
    assert report["rejected_rows"] == 2
    assert pitches.pitch_number.tolist() == [1]
    assert report["input_rows"] == 4


def test_duplicate_headers_only_coalesced_when_consistent(tmp_path):
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(FIELDS + ["pitcher"])
    writer.writerow(list(pitch().values()) + ["null"])
    source = tmp_path / "duplicate.csv"
    source.write_text(content.getvalue())
    frame, metadata = data.read_export(source)
    assert frame.pitcher.tolist() == ["20"]
    assert metadata["duplicate_headers"] == ["pitcher"]
    source.write_text(content.getvalue().replace(',null\r\n', ',999\r\n'))
    with pytest.raises(ValueError, match="conflicting duplicate header"):
        data.read_export(source)


def test_baserunning_unknown_events_and_automatic_strikes_are_not_invented_labels(tmp_path):
    report, pitches, pas = run_clean(tmp_path, [
        pitch(1, at_bat_number=1, events="caught_stealing_2b"),
        pitch(1, at_bat_number=2, events="new_event_code"),
        pitch(1, at_bat_number=3, pitch_type="null", description="automatic_strike", events="strikeout"),
        pitch(1, at_bat_number=4, events="fielders_choice"),
        pitch(1, at_bat_number=5, events="field_error"),
        pitch(1, at_bat_number=6, events="force_out"),
    ])
    assert pas.status.tolist()[:2] == ["interrupted_or_unresolved"] * 2
    assert pas.outcome.isna().sum() == 2
    assert pas.outcome.tolist()[2:] == ["strikeout", "fielders_choice", "reached_on_error", "fielders_choice"]
    assert pitches.pitch_type_eligible.tolist() == [True, True, False, True, True, True]
    assert report["events_without_batting_outcome_mapping"] == ["caught_stealing_2b", "new_event_code"]


def test_substitution_and_multiple_terminal_events_excluded_from_matchup_counts(tmp_path):
    _, _, pas = run_clean(tmp_path, [
        pitch(1), pitch(2, pitcher=21, events="strikeout"),
        pitch(1, at_bat_number=2, events="walk"),
        pitch(2, at_bat_number=2, events="strikeout"),
    ])
    assert pas.stable_matchup.tolist() == [False, True]
    assert pas.status.tolist() == ["completed", "ambiguous_terminal_events"]
    assert not pas.outcome_eligible.any()


def test_bad_numeric_values_cannot_turn_into_valid_ids(tmp_path):
    report, pitches, _ = run_clean(tmp_path, [
        pitch(1, game_pk="invalid"), pitch(1, at_bat_number=2, batter="10.5"),
        pitch(1, at_bat_number=3, release_speed="bad-measurement"),
        pitch(1, at_bat_number=4, balls="null"),
    ])
    assert report["rejected_rows"] == 3
    assert pitches.at_bat_number.tolist() == [3]
    assert pitches.release_speed.isna().all()
    assert report["numeric_parse_issues"] == {"batter": 1, "game_pk": 1, "release_speed": 1}


def test_manifest_integrity_and_no_silent_network_substitution(tmp_path, monkeypatch):
    body = io.StringIO()
    writer = csv.DictWriter(body, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerow(pitch())

    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.com/pitches.csv"

    calls = []

    def open_response(*args, **kwargs):
        calls.append(1)
        return Response(body.getvalue().encode())

    monkeypatch.setattr(data, "urlopen", open_response)
    target = tmp_path / "raw/pitches.csv"
    first = data.fetch_csv("https://example.com/pitches.csv", target, source_type="test", expected_date="2025-04-01")
    assert data.fetch_csv("https://example.com/pitches.csv", target, source_type="test") == first
    assert len(calls) == 1  # verified cached files do not require network
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        data.read_export(target)
    with pytest.raises(ValueError, match="provenance mismatch"):
        data.fetch_csv("https://example.com/pitches.csv", target, source_type="test")

    def denied(*args, **kwargs):
        raise HTTPError("https://baseballsavant.mlb.com", 403, "Forbidden", {}, None)

    monkeypatch.setattr(data, "urlopen", denied)
    assert main(["fetch", "--start", "2025-04-01", "--end", "2025-04-01",
                 "--output", str(tmp_path / "denied")]) == 1
    assert not (tmp_path / "denied").exists()


def test_malformed_csv_and_raw_output_overlap_fail_before_writing(tmp_path):
    invalid = tmp_path / "error.csv"
    invalid.write_text("<html>Forbidden</html>")
    with pytest.raises(ValueError, match="Not a supported pitch CSV"):
        data.clean_exports([invalid], tmp_path / "output")
    assert not (tmp_path / "output").exists()
    source = write_csv(tmp_path / "raw.csv", [pitch()])
    with pytest.raises(ValueError, match="separate"):
        data.clean_exports([source], tmp_path)
    source.write_text(source.read_text() + "too,few,fields\n")
    with pytest.raises(ValueError, match="expected 15 fields"):
        data.read_export(source)


def test_empty_export_is_reported_as_zero_rows_not_successful_validation(tmp_path):
    report, pitches, pas = run_clean(tmp_path, [])
    assert report["input_rows"] == 0
    assert pitches.empty and pas.empty
    assert report["eligible_completed_plate_appearances"] == 0


def test_wrong_date_export_is_never_persisted(tmp_path, monkeypatch):
    body = io.StringIO()
    writer = csv.DictWriter(body, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerow(pitch())

    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.com/pitches.csv"

    monkeypatch.setattr(data, "urlopen", lambda *a, **k: Response(body.getvalue().encode()))
    target = tmp_path / "raw/pitches.csv"
    with pytest.raises(ValueError, match="Unexpected game dates"):
        data.fetch_csv("https://example.com/pitches.csv", target, source_type="test", expected_date="2025-04-02")
    assert not target.exists()
