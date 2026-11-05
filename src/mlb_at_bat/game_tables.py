"""Build bounded-memory pitch-event and completed-PA tables before modeling."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import io
from itertools import groupby
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .data import (AUTOMATIC, FLOAT_COLUMNS, INTEGER_COLUMNS, KEY, NULLS, OUTCOMES,
                   PA_KEY, REQUIRED, clean_pitch_records, read_export)

EXTRA_INTEGERS = set("home_score_diff bat_score_diff n_thruorder_pitcher "
    "n_priorpa_thisgame_player_at_bat pitcher_days_since_prev_game batter_days_since_prev_game "
    "pitcher_days_until_next_game batter_days_until_next_game".split())
EXTRA_FLOATS = set("bat_speed swing_length miss_distance estimated_slg_using_speedangle "
    "delta_pitcher_run_exp hyper_speed home_win_exp bat_win_exp age_pit_legacy age_bat_legacy "
    "age_pit age_bat api_break_z_with_gravity api_break_x_arm api_break_x_batter_in arm_angle "
    "attack_angle attack_direction swing_path_tilt intercept_ball_minus_batter_pos_x_inches "
    "intercept_ball_minus_batter_pos_y_inches".split())
START_CONTEXT = ("game_type stand p_throws home_team away_team inning inning_topbot "
    "outs_when_up on_1b on_2b on_3b home_score away_score bat_score fld_score fielder_2".split())
CORE_OUTCOMES = {value: value for value in
                 ("strikeout", "walk", "hit_by_pitch", "single", "double", "triple", "home_run")}
BALLS = {"ball", "blocked_ball", "pitchout", "automatic_ball", "intent_ball"}
STRIKES = {"called_strike", "swinging_strike", "swinging_strike_blocked", "missed_bunt",
           "foul_tip", "bunt_foul_tip", "foul_bunt", "automatic_strike", "swinging_pitchout"}


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _text_objects(frame):
    frame = frame.copy()
    for column in frame.select_dtypes(include='object'):
        frame[column] = frame[column].astype('string')
    return frame


def iter_game_batches(source: Path):
    """Yield whole-date batches from immutable daily CSVs or our season CSV/ZIP.

    A combined CSV must have contiguous ascending game_date groups. No ZIP paths
    are extracted, and source records are numbered in the original combined CSV.
    """
    source = Path(source)
    if source.is_dir():
        paths = sorted(source.glob('*.csv'))
        if not paths:
            raise ValueError('No raw CSVs in input directory')
        for path in paths:
            yield read_export(path)
        return
    source_hash = _digest(source)
    container = None
    try:
        if source.suffix.lower() == '.zip':
            container = zipfile.ZipFile(source)
            members = [name for name in container.namelist() if name.endswith('.csv')]
            if len(members) != 1:
                raise ValueError('Season ZIP must contain exactly one combined CSV')
            member = members[0]
            binary = container.open(member)
            label = f'{source}!{member}'
            expected_rows = None
            if 'source_manifest.json' in container.namelist():
                expected_rows = json.loads(container.read('source_manifest.json')).get('rows')
        elif source.suffix.lower() == '.csv':
            binary = source.open('rb')
            member, label, expected_rows = None, str(source), None
        else:
            raise ValueError('Input must be a daily CSV directory, combined .csv, or season .zip')
        offset, previous_day = 0, ''
        with binary, io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            headers = [value.strip() for value in next(reader)]
            if (len(set(headers)) != len(headers) or not REQUIRED.issubset(headers)
                    or any(not value or value.startswith('_') for value in headers)):
                raise ValueError('Combined CSV has missing, duplicate, or reserved headers')
            date_index = headers.index('game_date')
            def records():
                for record in reader:
                    if not record:
                        continue
                    if len(record) != len(headers):
                        raise ValueError('Malformed combined CSV record')
                    yield record
            for day, group in groupby(records(), key=lambda row: row[date_index]):
                if day < previous_day:
                    raise ValueError('Combined CSV must be grouped in ascending game_date order')
                previous_day = day
                rows = list(group)
                frame = pd.DataFrame(rows, columns=headers, dtype='string')
                for column in frame:
                    values = frame[column].str.strip()
                    frame[column] = values.mask(values.str.lower().isin(NULLS))
                frame['_source_file'] = label
                frame['_source_record'] = pd.Series(range(offset+1, offset+len(frame)+1), dtype='Int64')
                offset += len(frame)
                yield frame, {'file': str(source), 'member': member, 'sha256': source_hash,
                    'source_type': 'combined_pitch_export', 'game_date_batch': day,
                    'rows': len(frame), 'last_source_record': offset,
                    'container_expected_rows': expected_rows}
            if expected_rows is not None and offset != expected_rows:
                raise ValueError('Combined CSV row count disagrees with embedded source manifest')
    finally:
        if container is not None:
            container.close()


def derive_game_tables(frame):
    """Reuse conservative source cleaning, then add PA-start context and audit flags."""
    pitches, old_pas, rejected, report = clean_pitch_records(frame)
    for column in sorted((EXTRA_INTEGERS | EXTRA_FLOATS) & set(pitches)):
        values = pd.to_numeric(pitches[column], errors='coerce')
        invalid = pitches[column].notna() & (values.isna() | ~np.isfinite(values.astype(float)))
        if column in EXTRA_INTEGERS:
            invalid |= values.notna() & ((values % 1 != 0) | values.abs().gt(2**53-1))
        if invalid.any():
            report['numeric_parse_issues'][column] = int(invalid.sum())
        pitches[column] = values.mask(invalid).astype('Int64' if column in EXTRA_INTEGERS else 'Float64')
    groups = pitches.groupby(PA_KEY, sort=False)
    previous = groups[['balls', 'strikes', 'description']].shift(1)
    first = groups.cumcount().eq(0)
    ball = previous.description.isin(BALLS)
    strike = previous.description.isin(STRIKES)
    foul = previous.description.eq('foul').fillna(False)
    known = ball | strike | foul
    expected_balls = previous.balls + ball.astype('Int64')
    expected_strikes = previous.strikes + strike.astype('Int64')
    expected_strikes = expected_strikes.where(~foul, (previous.strikes+1).clip(upper=2))
    matches = pitches.balls.eq(expected_balls) & pitches.strikes.eq(expected_strikes)
    status = pd.Series('unverified', index=pitches.index, dtype='string')
    status.loc[known & matches.fillna(False)] = 'consistent'
    status.loc[known & ~matches.fillna(False)] = 'inconsistent'
    status.loc[first] = 'start'
    pitches['count_transition_status'] = status
    pitches['is_start_of_recorded_pa'] = first.astype('boolean')
    pitches['is_terminal_pa_event'] = pitches.events.isin(OUTCOMES).astype('boolean')

    starting = pitches.drop_duplicates(PA_KEY, keep='first').set_index(PA_KEY)
    ending = pitches.drop_duplicates(PA_KEY, keep='last').set_index(PA_KEY)
    audit = old_pas.set_index(PA_KEY).rename(columns={
        'batter': 'ending_batter', 'pitcher': 'ending_pitcher',
        'recorded_pitches': 'recorded_events', 'outcome': 'outcome_detail',
    })
    for column in ['batter', 'pitcher', *START_CONTEXT]:
        if column in pitches:
            audit[column] = starting[column]
    audit['starting_pitch_number'] = starting.pitch_number
    audit['terminal_pitch_number'] = ending.pitch_number
    audit['start_balls'], audit['start_strikes'] = starting.balls, starting.strikes
    audit['starts_at_zero_count'] = (starting.balls.eq(0) & starting.strikes.eq(0)).astype('boolean')
    for prefix, selected in [('first', starting), ('terminal', ending)]:
        audit[f'_{prefix}_source_file'] = selected['_source_file']
        audit[f'_{prefix}_source_record'] = selected['_source_record']
    if 'fielder_2' in pitches:
        audit['ending_catcher'] = ending.fielder_2
    audit['automatic_event_count'] = groups.is_automatic_event.sum().astype('Int64')
    audit['count_mismatch_count'] = pitches.assign(_bad=status.eq('inconsistent')).groupby(PA_KEY)._bad.sum().astype('Int64')
    audit['unverified_count_transitions'] = pitches.assign(_bad=status.eq('unverified')).groupby(PA_KEY)._bad.sum().astype('Int64')
    audit['count_sequence_consistent'] = (audit.count_mismatch_count.eq(0) & audit.unverified_count_transitions.eq(0)).astype('boolean')
    audit['unknown_event_count'] = pitches.assign(
        _unknown=pitches.events.notna() & ~pitches.events.isin(OUTCOMES)
    ).groupby(PA_KEY)._unknown.sum().astype('Int64')
    audit['pa_outcome'] = audit.outcome_detail.map(CORE_OUTCOMES).fillna('other_pa_outcome').astype('string')
    audit.loc[audit.status.ne('completed'), 'pa_outcome'] = pd.NA
    audit['outcome_eligible'] = (audit.outcome_eligible & audit.starts_at_zero_count
        & audit.count_sequence_consistent & audit.unknown_event_count.eq(0)).astype('boolean')
    reasons = pd.Series('', index=audit.index, dtype='string')
    for mask, name in [
        (audit.status.ne('completed'), 'no_unambiguous_terminal_outcome'),
        (~audit.sequence_complete, 'incomplete_pitch_sequence'),
        (~audit.stable_matchup, 'mid_pa_matchup_change'),
        (audit.has_rejected_rows, 'rejected_source_rows'),
        (~audit.starts_at_zero_count, 'missing_pa_start_count'),
        (audit.count_mismatch_count.gt(0), 'count_sequence_mismatch'),
        (audit.unverified_count_transitions.gt(0), 'unverified_count_transition'),
        (audit.unknown_event_count.gt(0), 'unmapped_source_event'),
    ]:
        reasons.loc[mask.fillna(True)] += name + ';'
    audit['exclusion_reason'] = reasons.str.rstrip(';').replace('', pd.NA)
    audit = _text_objects(audit.reset_index())
    flags = audit[PA_KEY + ['status', 'sequence_complete', 'stable_matchup', 'outcome_eligible']].rename(columns={
        'status': 'pa_status', 'sequence_complete': 'pa_sequence_complete',
        'stable_matchup': 'pa_stable_matchup', 'outcome_eligible': 'pa_outcome_eligible',
    })
    pitches = _text_objects(pitches.merge(flags, on=PA_KEY, how='left', validate='many_to_one'))
    complete = audit.loc[audit.status.eq('completed')].reset_index(drop=True)
    excluded = audit.loc[~audit.outcome_eligible].reset_index(drop=True)
    report.update({'completed_pa_rows': len(complete), 'eligible_pa_rows': int(audit.outcome_eligible.sum()),
        'excluded_pa_rows': len(excluded), 'count_sequence_mismatch_groups': int(audit.count_mismatch_count.gt(0).sum()),
        'unverified_count_groups': int(audit.unverified_count_transitions.gt(0).sum()),
        'mid_pa_matchup_changes': int((~audit.stable_matchup).sum()),
        'nonzero_start_counts': int((~audit.starts_at_zero_count).sum()),
        'pa_outcomes': {str(k): int(v) for k,v in complete.pa_outcome.value_counts().items()}})
    return pitches, complete, _text_objects(rejected), excluded, report


def column_role(table, column):
    """Conservative documentation, not an automatically selected feature matrix."""
    if column.startswith('_') or column in KEY or column in ('game_date', 'game_type'):
        return 'identity_or_provenance'
    if column in ('pitch_type', 'description', 'events', 'pa_outcome', 'terminal_event', 'outcome_detail'):
        return 'target_or_outcome'
    if column in ('batter', 'pitcher', 'balls', 'strikes', *START_CONTEXT, 'start_balls', 'start_strikes'):
        return 'pre_pitch_context' if table == 'pitch_events' else 'pa_start_context'
    if column in ('pitcher_days_until_next_game', 'batter_days_until_next_game'):
        return 'future_information_do_not_use'
    if (column in FLOAT_COLUMNS | EXTRA_FLOATS
            or column.startswith(('post_', 'release_', 'launch_', 'estimated_'))):
        return 'post_pitch_or_derived_measurement_review_before_use'
    return 'audit_or_other_source_field_review_before_use'


def build_game_tables(source: Path, output: Path, *, progress=None):
    """Publish two typed Parquet tables and audit sidecars using one date per batch."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Raw input and generated output must be separate')
    if output.exists():
        raise FileExistsError('Output already exists; choose a new version directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    totals = Counter()
    numeric, rejections, outcomes, statuses, exclusion_reasons = (Counter() for _ in range(5))
    inputs, dictionary, schema_columns = [], [], {}
    game_dates, writers, templates = {}, {}, {}
    names = ('pitch_events', 'plate_appearances', 'rejected_rows', 'pa_exclusions')
    with tempfile.TemporaryDirectory(prefix='game-tables-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'result'; stage.mkdir()
        with sqlite3.connect(Path(temporary) / 'keys.sqlite') as keys:
            keys.execute('CREATE TABLE pitches (game INTEGER, pa INTEGER, pitch INTEGER, PRIMARY KEY(game,pa,pitch)) WITHOUT ROWID')
            try:
                for frame, metadata in iter_game_batches(source):
                    inputs.append(metadata)
                    if frame.empty:
                        continue
                    tables = derive_game_tables(frame)
                    pitches, complete, rejected, excluded, report = tables
                    for game, dates in pitches.groupby('game_pk').game_date:
                        values = set(dates.dt.strftime('%Y-%m-%d'))
                        if len(values) != 1 or (game in game_dates and game_dates[game] not in values):
                            raise ValueError('Game spans date batches; reconcile its source before publishing')
                        if game in game_dates:
                            raise ValueError('Game split across input batches; provide complete games per batch')
                        game_dates[int(game)] = next(iter(values))
                    before = keys.total_changes
                    keys.executemany('INSERT OR IGNORE INTO pitches VALUES (?,?,?)',
                        (tuple(int(value) for value in row) for row in pitches[KEY].itertuples(index=False, name=None)))
                    if keys.total_changes - before != len(pitches):
                        raise ValueError('Duplicate pitch key across batches; output not published')
                    keys.commit()
                    for name, table in zip(names, tables[:4]):
                        table = _text_objects(table)
                        templates.setdefault(name, table.iloc[:0])
                        if name in schema_columns and list(table) != schema_columns[name]:
                            raise ValueError(f'{name}: inconsistent source schema between batches')
                        schema_columns[name] = list(table)
                        arrow = pa.Table.from_pandas(table, preserve_index=False)
                        if len(table):
                            if name not in writers:
                                writers[name] = pq.ParquetWriter(stage / f'{name}.parquet', arrow.schema, compression='zstd')
                                dictionary.extend({'table': name, 'column': col, 'dtype': str(table[col].dtype),
                                    'role': column_role(name, col)} for col in table)
                            writers[name].write_table(arrow)
                        totals[name] += len(table)
                    totals['input_rows'] += report['input_rows']
                    totals['exact_duplicates_removed'] += report['exact_duplicate_rows_removed']
                    totals['pa_groups'] += report['plate_appearance_groups']
                    totals['eligible_pitch_type_rows'] += report['eligible_pitch_type_rows']
                    for key in ('eligible_pa_rows', 'count_sequence_mismatch_groups', 'unverified_count_groups',
                                'mid_pa_matchup_changes', 'nonzero_start_counts'):
                        totals[key] += report[key]
                    numeric.update(report['numeric_parse_issues']); rejections.update(report['rejection_reasons'])
                    outcomes.update(report['pa_outcomes']); statuses.update(report['plate_appearance_statuses'])
                    exclusion_reasons.update(reason for text in excluded.exclusion_reason.dropna() for reason in text.split(';'))
                    if progress is not None:
                        progress({'batch': len(inputs), 'source': metadata['file'], **dict(totals)})
            finally:
                for writer in writers.values():
                    writer.close()
        if not totals['pitch_events']:
            raise ValueError('No valid pitch events; no dataset published')
        for name in names:
            if name not in writers:
                templates[name].to_parquet(stage / f'{name}.parquet', index=False, compression='zstd')
                dictionary.extend({'table': name, 'column': col, 'dtype': str(templates[name][col].dtype),
                    'role': column_role(name, col)} for col in templates[name])
        assert totals['input_rows'] == totals['pitch_events'] + totals['rejected_rows'] + totals['exact_duplicates_removed']
        pd.DataFrame(dictionary).to_csv(stage / 'data_dictionary.csv', index=False, lineterminator='\n')
        report = {'schema_version': 1, 'inputs': inputs, 'counts': dict(totals), 'games': len(game_dates),
            'first_game_date': min(game_dates.values()), 'last_game_date': max(game_dates.values()),
            'numeric_parse_issues': dict(numeric), 'rejection_reasons': dict(rejections),
            'pa_statuses': dict(statuses), 'pa_outcomes': dict(outcomes), 'pa_exclusion_reasons': dict(exclusion_reasons),
            'notes': ['Two main tables: pitch_events and completed plate_appearances; audit sidecars explain exclusions.',
                'PA context uses the first recorded event. Ending matchup IDs are retained separately.',
                'Completed PA rows are retained even when outcome_eligible is false; filter explicitly for future training.',
                'pitch_type_eligible is a candidate flag, not a guarantee that all model features are available.',
                'Same-pitch measurements, outcome/audit fields, and future-game fields are not pitch-selection features.',
                'No full-season player profiles are joined, no training split is made, and no model is fitted.']}
        report['output_sha256'] = {path.name: _digest(path) for path in sorted(stage.iterdir())}
        (stage / 'quality_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        stage.replace(output)
    return report
