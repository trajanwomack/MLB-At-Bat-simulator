"""Sequence, PA-start context, provenance, and bounded-batch table generation."""

import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from mlb_at_bat.data import read_export
from mlb_at_bat.game_tables import build_game_tables, column_role, derive_game_tables


FIELDS = ['game_pk', 'at_bat_number', 'pitch_number', 'game_date', 'game_type',
    'batter', 'pitcher', 'fielder_2', 'stand', 'p_throws', 'balls', 'strikes',
    'pitch_type', 'description', 'events', 'inning', 'inning_topbot', 'outs_when_up',
    'on_1b', 'bat_score', 'post_bat_score', 'release_speed', 'bat_speed',
    'pitcher_days_until_next_game']


def pitch(number, **changes):
    return {'game_pk': 100, 'at_bat_number': 1, 'pitch_number': number,
        'game_date': '2026-06-01', 'game_type': 'R', 'batter': 20, 'pitcher': 30,
        'fielder_2': 40, 'stand': 'L', 'p_throws': 'R', 'balls': 0, 'strikes': 0,
        'pitch_type': 'FF', 'description': 'ball', 'events': '', 'inning': 1,
        'inning_topbot': 'Top', 'outs_when_up': 0, 'on_1b': '', 'bat_score': 0,
        'post_bat_score': 0, 'release_speed': 95, 'bat_speed': '',
        'pitcher_days_until_next_game': 4, **changes}


def single():
    return [pitch(1), pitch(2, balls=1, description='called_strike', pitch_type='SL'),
        pitch(3, balls=1, strikes=1, description='hit_into_play', events='single',
              on_1b=999, bat_speed=72.5, post_bat_score=2)]


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(rows)
    return path


def derive(tmp_path, rows):
    source = write_csv(tmp_path / 'source.csv', rows)
    frame, _ = read_export(source)
    return derive_game_tables(frame)


def test_completed_pa_uses_first_context_keeps_terminal_outcome_and_source_references(tmp_path):
    rows = single()
    events, pas, rejected, excluded, report = derive(tmp_path, [rows[2], rows[0], rows[1]])
    assert events.pitch_number.tolist() == [1, 2, 3]
    assert events['_source_record'].tolist() == [2, 3, 1]
    assert events.count_transition_status.tolist() == ['start', 'consistent', 'consistent']
    assert len(pas) == 1 and rejected.empty and excluded.empty
    row = pas.iloc[0]
    assert row.pa_outcome == row.terminal_event == 'single'
    assert pd.isna(row.on_1b)  # groupby.first() would incorrectly take a later runner.
    assert row.bat_score == 0 and 'post_bat_score' not in pas
    assert 'bat_speed' not in pas and 'release_speed' not in pas
    assert row._first_source_record == 2 and row._terminal_source_record == 1
    assert row.recorded_events == 3 and row.outcome_eligible
    assert str(events.bat_speed.dtype) == 'Float64'
    assert str(events.pitcher_days_until_next_game.dtype) == 'Int64'
    assert report['completed_pa_rows'] == report['eligible_pa_rows'] == 1


def test_automatic_events_and_two_strike_fouls_preserve_count_context(tmp_path):
    rows = [pitch(1, description='automatic_ball', pitch_type=''),
        pitch(2, balls=1, description='called_strike'),
        pitch(3, balls=1, strikes=1, description='called_strike'),
        pitch(4, balls=1, strikes=2, description='foul'),
        pitch(5, balls=1, strikes=2, description='foul_tip', events='strikeout')]
    events, pas, _, _, _ = derive(tmp_path, rows)
    assert events.is_automatic_event.tolist() == [True, False, False, False, False]
    assert events.pitch_type_eligible.tolist() == [False, True, True, True, True]
    assert pas.automatic_event_count.tolist() == [1]
    assert pas.count_sequence_consistent.all() and pas.outcome_eligible.all()


@pytest.mark.parametrize(('changes', 'reason'), [
    ({'balls': 0}, 'count_sequence_mismatch'),
    ({'description': 'new_description'}, 'unverified_count_transition'),
])
def test_bad_or_unverifiable_counts_flag_completed_pas_without_inventing_repairs(tmp_path, changes, reason):
    rows = single()
    if 'balls' in changes:
        rows[1].update(changes)
    else:
        rows[0].update(changes)
    _, pas, _, excluded, _ = derive(tmp_path, rows)
    assert len(pas) == len(excluded) == 1
    assert pas.pa_outcome.tolist() == ['single']
    assert not pas.outcome_eligible.any()
    assert reason in pas.exclusion_reason.iloc[0]


def test_substitutions_retain_starting_and_ending_ids_but_exclude_simple_matchup_training(tmp_path):
    rows = single()
    rows[-1].update(pitcher=31, batter=21, fielder_2=41)
    _, pas, _, excluded, _ = derive(tmp_path, rows)
    row = pas.iloc[0]
    assert (row.pitcher, row.ending_pitcher, row.batter, row.ending_batter) == (30, 31, 20, 21)
    assert (row.fielder_2, row.ending_catcher) == (40, 41)
    assert not row.stable_matchup and not row.outcome_eligible
    assert 'mid_pa_matchup_change' in excluded.exclusion_reason.iloc[0]


def test_rejected_pitches_gaps_and_missing_pa_start_are_audited(tmp_path):
    rows = single(); rows[1]['balls'] = 4
    events, pas, rejected, excluded, _ = derive(tmp_path, rows)
    assert len(events) == 2 and len(rejected) == 1
    assert pas.has_rejected_rows.all() and not pas.outcome_eligible.any()
    assert 'rejected_source_rows' in excluded.exclusion_reason.iloc[0]
    _, pas, _, excluded, _ = derive(tmp_path, single()[1:])
    assert not pas.sequence_complete.any() and not pas.starts_at_zero_count.any()
    assert 'missing_pa_start_count' in excluded.exclusion_reason.iloc[0]


def test_unresolved_and_multiple_terminal_groups_do_not_become_completed_labels(tmp_path):
    rows = [pitch(1, events='truncated_pa'),
        pitch(1, at_bat_number=2, events='walk'),
        pitch(2, at_bat_number=2, balls=1, description='hit_into_play', events='single')]
    _, pas, _, excluded, report = derive(tmp_path, rows)
    assert pas.empty and len(excluded) == 2
    assert excluded.pa_outcome.isna().all()
    assert report['plate_appearance_statuses'] == {'interrupted_or_unresolved': 1, 'ambiguous_terminal_events': 1}


def test_baseline_outcome_categories_and_unknown_pitch_types_are_retained(tmp_path):
    rows = [pitch(1, events='intent_walk', pitch_type=''),
        pitch(1, at_bat_number=2, description='hit_into_play', events='field_error', pitch_type='UN')]
    events, pas, _, _, _ = derive(tmp_path, rows)
    assert pas.pa_outcome.tolist() == ['walk', 'other_pa_outcome']
    assert pas.terminal_event.tolist() == ['intent_walk', 'field_error']
    assert not events.pitch_type_eligible.any()
    assert len(events) == 2
    assert column_role('pitch_events', 'pitcher_days_until_next_game') == 'future_information_do_not_use'
    assert column_role('pitch_events', 'release_speed').startswith('post_pitch')


def test_optional_bad_measurements_are_reported_and_remain_missing(tmp_path):
    rows = single(); rows[-1]['bat_speed'] = 'bad'
    events, pas, _, _, report = derive(tmp_path, rows)
    assert events.bat_speed.isna().all() and len(pas) == 1
    assert report['numeric_parse_issues'] == {'bat_speed': 1}


def test_daily_directory_and_season_zip_produce_equivalent_business_tables(tmp_path):
    raw = tmp_path / 'raw'; raw.mkdir()
    first = single()
    second = [dict(row, game_pk=101, game_date='2026-06-02') for row in single()]
    write_csv(raw / '2026-06-01.csv', first)
    write_csv(raw / '2026-06-02.csv', second)
    write_csv(raw / '2026-06-03.csv', [])
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()}
    text = io.StringIO(); writer = csv.DictWriter(text, fieldnames=FIELDS)
    writer.writeheader(); writer.writerows(first + second)
    archive = tmp_path / 'season.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('season.csv', text.getvalue())
        bundle.writestr('source_manifest.json', json.dumps({'rows': 6}))
    directory_report = build_game_tables(raw, tmp_path / 'daily')
    zip_report = build_game_tables(archive, tmp_path / 'combined')
    assert directory_report['counts'] == zip_report['counts']
    assert directory_report['games'] == 2
    for name in ['pitch_events', 'plate_appearances']:
        a = pd.read_parquet(tmp_path / 'daily' / f'{name}.parquet')
        b = pd.read_parquet(tmp_path / 'combined' / f'{name}.parquet')
        columns = [c for c in a if not c.startswith('_')]
        pd.testing.assert_frame_equal(a[columns], b[columns])
    combined = pd.read_parquet(tmp_path / 'combined/pitch_events.parquet')
    assert combined['_source_record'].tolist() == [1, 2, 3, 4, 5, 6]
    assert (tmp_path / 'daily/quality_report.json').is_file()
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()}
    with pytest.raises(FileExistsError):
        build_game_tables(raw, tmp_path / 'daily')


@pytest.mark.parametrize('problem', ['split_game', 'manifest', 'unsorted_zip', 'zip_count'])
def test_invalid_batch_boundaries_or_source_integrity_fail_without_publishing(tmp_path, problem):
    raw = tmp_path / 'raw'; raw.mkdir()
    source = raw
    write_csv(raw / 'a.csv', single())
    if problem == 'split_game':
        write_csv(raw / 'b.csv', [dict(row, game_date='2026-06-02') for row in single()])
    elif problem == 'manifest':
        (raw / 'a.manifest.json').write_text(json.dumps({'sha256': 'wrong'}))
    else:
        source = tmp_path / 'bad.zip'
        text = io.StringIO(); writer = csv.DictWriter(text, fieldnames=FIELDS); writer.writeheader()
        rows = single()
        if problem == 'unsorted_zip':
            rows = [dict(row, game_date='2026-06-02') for row in rows] + single()
        writer.writerows(rows)
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('season.csv', text.getvalue())
            archive.writestr('source_manifest.json', json.dumps({'rows': 999 if problem == 'zip_count' else 6}))
    with pytest.raises(ValueError):
        build_game_tables(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_empty_completed_pa_table_is_typed_and_unresolved_evidence_is_retained(tmp_path):
    raw = tmp_path / 'raw'; raw.mkdir()
    write_csv(raw / 'day.csv', [pitch(1, events='truncated_pa')])
    report = build_game_tables(raw, tmp_path / 'output')
    assert report['counts']['pitch_events'] == 1
    assert pd.read_parquet(tmp_path / 'output/plate_appearances.parquet').empty
    audit = pd.read_parquet(tmp_path / 'output/pa_exclusions.parquet')
    assert audit.pa_outcome.isna().all() and len(audit) == 1
