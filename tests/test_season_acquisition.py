"""Offline acquisition checks; no network requests or season-sized fixtures."""

import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/acquire_season.py'
spec = importlib.util.spec_from_file_location('season_acquisition', SCRIPT)
acquisition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquisition)


def day_file(raw, day, *, duplicate=False, empty=False):
    headers = ['game_pk', 'at_bat_number', 'pitch_number', 'game_date', 'game_type',
               'pitch_type', 'description', 'home_team', 'away_team']
    rows = [] if empty else [
        ['123', '1', '1', day, 'R', 'FF', 'called_strike', 'NYY', 'BOS'],
        ['123', '1', '1' if duplicate else '2', day, 'R', '', 'automatic_ball', 'NYY', 'BOS'],
    ]
    path = raw / f'{day}.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    manifest = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'rows': len(rows),
                'bytes': path.stat().st_size, 'columns': headers}
    path.with_suffix('.manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_streamed_bundle_preserves_raw_rows_and_audits_empty_dates(tmp_path):
    raw, output = tmp_path / 'raw', tmp_path / 'output'
    raw.mkdir(); output.mkdir()
    manifests = {'2026-06-01': day_file(raw, '2026-06-01'),
                 '2026-06-02': day_file(raw, '2026-06-02', empty=True)}
    original = {path.name: path.read_bytes() for path in raw.iterdir()}
    report = {'season': 2026, 'status': 'packaging'}
    archive = acquisition.package_season(raw, output, manifests, report)
    assert report['rows'] == 2
    assert report['distinct_games'] == 1
    assert report['duplicate_pitch_keys'] == 0
    assert report['automatic_event_rows'] == report['missing_pitch_type_rows'] == 1
    assert report['team_game_counts'] == {'BOS': 1, 'NYY': 1}
    assert report['empty_dates'] == ['2026-06-02']
    assert acquisition.digest(archive) == report['archive_sha256']
    with zipfile.ZipFile(archive) as bundle:
        rows = list(csv.DictReader(io.StringIO(bundle.read('statcast_2026_regular_season.csv').decode('utf-8-sig'))))
        assert [row['description'] for row in rows] == ['called_strike', 'automatic_ball']
        assert 'daily_manifests/2026-06-02.manifest.json' in bundle.namelist()
        assert json.loads(bundle.read('source_manifest.json'))['status'] == 'records_validated'
    assert {path.name: path.read_bytes() for path in raw.iterdir()} == original


@pytest.mark.parametrize('problem', ['duplicate', 'checksum', 'schema'])
def test_corrupted_or_ambiguous_data_never_publish_a_finished_bundle(tmp_path, problem):
    raw, output = tmp_path / 'raw', tmp_path / 'output'
    raw.mkdir(); output.mkdir()
    manifests = {'2026-06-01': day_file(raw, '2026-06-01', duplicate=problem == 'duplicate')}
    if problem == 'checksum':
        manifests['2026-06-01']['sha256'] = 'wrong'
    if problem == 'schema':
        manifests['2026-06-02'] = day_file(raw, '2026-06-02', empty=True)
        path = raw / '2026-06-02.csv'
        path.write_text(path.read_text(encoding='utf-8-sig').replace('away_team', 'different_column'))
        manifests['2026-06-02']['sha256'] = acquisition.digest(path)
    with pytest.raises(ValueError):
        acquisition.package_season(raw, output, manifests, {'season': 2026})
    assert not (output / 'statcast_2026_regular_season.zip').exists()


def test_rate_limit_is_retried_but_access_denial_stops(monkeypatch, tmp_path):
    attempts = []
    waits = []
    def request(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise HTTPError('https://example.test', 429, 'Rate limited', {'Retry-After': '2'}, None)
        return [{'rows': 1}]
    monkeypatch.setattr(acquisition, 'fetch_dates', request)
    monkeypatch.setattr(acquisition.time, 'sleep', waits.append)
    assert acquisition.acquire_day('2026-06-01', tmp_path) == {'rows': 1}
    assert len(attempts) == 2 and waits == [5]
    def denied(*args):
        raise HTTPError('https://example.test', 403, 'Forbidden', {}, None)
    monkeypatch.setattr(acquisition, 'fetch_dates', denied)
    with pytest.raises(HTTPError):
        acquisition.acquire_day('2026-06-01', tmp_path)
    assert waits == [5]


def test_disk_guard_reports_abort_without_publishing_an_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(acquisition, 'acquire_day', lambda *args: {'rows': 0, 'bytes': 0})
    monkeypatch.setattr(acquisition.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    output = tmp_path / 'output'
    status = acquisition.main(['--season', '2026', '--through', '2026-01-01', '--workers', '1',
                               '--raw', str(tmp_path / 'raw'), '--output', str(output)])
    report = json.loads((output / 'acquisition_report.json').read_text())
    assert status == 1 and report['status'] == 'aborted'
    assert 'free disk' in report['error']
    assert not list(output.glob('*.zip'))
