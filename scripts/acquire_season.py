"""Acquire daily regular-season Savant CSVs and stream a season CSV into a ZIP.

Run with the project's installed .venv. Raw inputs are immutable and resumable.
No cleaning, feature engineering, or model fitting takes place.
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
import zipfile

from mlb_at_bat.data import fetch_dates


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_report(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def acquire_day(day, output):
    for attempt in range(3):
        try:
            return fetch_dates(day, day, output)[0]
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise
            delay = error.headers.get('Retry-After', '') if error.headers else ''
            delay = min(30, max(5, int(delay))) if delay.isdigit() else 5 * (attempt + 1)
        except (URLError, TimeoutError, ConnectionError):
            delay = 5 * (attempt + 1)
        if attempt == 2:
            raise RuntimeError(f'{day}: download failed after three attempts')
        time.sleep(delay)


def package_season(raw, output, manifests, report):
    """Use a disk-backed key index; never concatenate the season in memory."""
    archive = output / f"statcast_{report['season']}_regular_season.zip"
    temporary = archive.with_suffix('.partial.zip')
    index = output / 'pitch_key_audit.sqlite'
    if index.exists():
        index.unlink()  # Rebuild only this derived index; raw CSVs stay immutable.
    total = duplicates = missing_types = automatic = 0
    game_teams = {}
    with sqlite3.connect(index) as database, zipfile.ZipFile(
        temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True
    ) as bundle:
        database.execute('CREATE TABLE pitch_keys (game TEXT, pa TEXT, pitch TEXT, PRIMARY KEY(game,pa,pitch)) WITHOUT ROWID')
        canonical = None
        with bundle.open(f"statcast_{report['season']}_regular_season.csv", 'w', force_zip64=True) as binary:
            with io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as text:
                writer = csv.writer(text, lineterminator='\n')
                for number, (day, manifest) in enumerate(sorted(manifests.items()), 1):
                    path = raw / f'{day}.csv'
                    if digest(path) != manifest['sha256']:
                        raise ValueError(f'{day}: raw checksum changed')
                    with path.open(encoding='utf-8-sig', newline='') as source:
                        reader = csv.reader(source, strict=True)
                        headers = next(reader)
                        if len(headers) != len(set(headers)):
                            raise ValueError(f'{day}: ambiguous duplicate CSV headers')
                        if canonical is None:
                            canonical = headers
                            writer.writerow(headers)
                        if headers != canonical:
                            raise ValueError(f'{day}: schema differs; raw files retained for review')
                        positions = {name: headers.index(name) for name in (
                            'game_pk', 'at_bat_number', 'pitch_number', 'game_date',
                            'game_type', 'pitch_type', 'description', 'home_team', 'away_team'
                        )}
                        count = 0
                        keys = []
                        for row in reader:
                            if not row:
                                continue
                            if len(row) != len(headers):
                                raise ValueError(f'{day}: malformed CSV row')
                            get = lambda name: row[positions[name]]
                            if get('game_date') != day or get('game_type') != 'R':
                                raise ValueError(f'{day}: unexpected date or game type')
                            key = tuple(get(name) for name in ('game_pk', 'at_bat_number', 'pitch_number'))
                            if not all(part.isdigit() for part in key):
                                raise ValueError(f'{day}: missing/noninteger pitch key')
                            keys.append(tuple(str(int(part)) for part in key))
                            teams = (get('home_team'), get('away_team'))
                            if key[0] in game_teams and game_teams[key[0]] != teams:
                                raise ValueError(f'{day}: inconsistent teams within game')
                            game_teams[key[0]] = teams
                            missing_types += not get('pitch_type')
                            automatic += get('description') in ('automatic_ball', 'automatic_strike')
                            writer.writerow(row)
                            count += 1
                        if count != manifest['rows']:
                            raise ValueError(f'{day}: manifest row count disagrees')
                        before = database.total_changes
                        database.executemany('INSERT OR IGNORE INTO pitch_keys VALUES (?,?,?)', keys)
                        duplicates += count - (database.total_changes - before)
                        database.commit()
                        total += count
                    if number % 20 == 0:
                        print(json.dumps({'phase': 'packaging', 'days': number, 'rows': total}), flush=True)
        if duplicates:
            raise ValueError(f'{duplicates} duplicate pitch keys; partial bundle withheld, raw files retained')
        if total == 0:
            raise ValueError('No records returned; refusing to label an empty export as a season')
        team_counts = {}
        for teams in game_teams.values():
            for team in teams:
                team_counts[team] = team_counts.get(team, 0) + 1
        report.update({
            'rows': total, 'distinct_games': len(game_teams), 'columns': canonical,
            'column_count': len(canonical), 'duplicate_pitch_keys': duplicates,
            'missing_pitch_type_rows': missing_types, 'automatic_event_rows': automatic,
            'team_game_counts': dict(sorted(team_counts.items())),
            'dates_with_records': [day for day, item in sorted(manifests.items()) if item['rows']],
            'empty_dates': [day for day, item in sorted(manifests.items()) if not item['rows']],
            'daily_sources': [item for _, item in sorted(manifests.items())],
            'coverage_note': report.get('coverage_note',
                'All calendar dates in the requested range queried. Full-season independent schedule game-ID reconciliation is not performed by this collector.'),
            'notes': ['Raw values preserved. Combined CSV ordered by date/file; source within-day order retained.',
                      'Pitch/event records include automatic balls and strikes and missing measurements.',
                      'No training, imputation, deduplication, or matchup profile joins performed.'],
        })
        # The embedded source audit describes validated records, not the
        # caller's transient packaging phase or a previous archive's checksum.
        source_audit = {key: value for key, value in report.items()
                        if key not in ('status', 'archive', 'archive_bytes', 'archive_sha256')}
        source_audit['status'] = 'records_validated'
        bundle.writestr('source_manifest.json', json.dumps(source_audit, indent=2))
        for day in sorted(manifests):
            bundle.write(raw / f'{day}.manifest.json', arcname=f'daily_manifests/{day}.manifest.json')
    temporary.replace(archive)
    report.update({'archive': str(archive.resolve()), 'archive_bytes': archive.stat().st_size,
                   'archive_sha256': digest(archive)})
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--through', type=date.fromisoformat, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-raw-gib', type=float, default=2)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args(argv)
    if args.through.year != args.season or args.max_raw_gib <= 0 or args.max_seconds <= 0:
        parser.error('Through date must be in the season; resource limits must be positive')
    raw, output = args.raw.resolve(), args.output.resolve()
    if raw == output or raw in output.parents or output in raw.parents:
        parser.error('Raw and output directories must be separate')
    raw.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    report = {'season': args.season, 'start': f'{args.season}-01-01', 'through': args.through.isoformat(),
              'game_type': 'R', 'status': 'running', 'workers': args.workers,
              'started_at_utc': datetime.now(timezone.utc).isoformat()}
    report_path = output / 'acquisition_report.json'
    save_report(report_path, report)
    first = date(args.season, 1, 1)
    days = iter(first + timedelta(days=i) for i in range((args.through - first).days + 1))
    manifests = {}
    started = time.monotonic()
    raw_bytes = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {}
            def submit():
                day = next(days, None)
                if day is not None:
                    pending[executor.submit(acquire_day, day, raw)] = day
            for _ in range(args.workers):
                submit()
            try:
                while pending:
                    finished, _ = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
                    if time.monotonic() - started > args.max_seconds:
                        raise RuntimeError('Acquisition time budget exceeded; cached days retained')
                    if shutil.disk_usage(raw).free < 2 * 1024**3:
                        raise RuntimeError('Less than 2 GiB free disk; acquisition stopped')
                    for future in finished:
                        day = pending.pop(future)
                        manifest = future.result()
                        manifests[day.isoformat()] = manifest
                        raw_bytes += manifest['bytes']
                        if raw_bytes > args.max_raw_gib * 1024**3:
                            raise RuntimeError('Raw byte budget exceeded; cached days retained')
                        report.update({'completed_dates': len(manifests), 'rows_downloaded': sum(m['rows'] for m in manifests.values()),
                                       'raw_bytes': raw_bytes, 'elapsed_seconds': round(time.monotonic()-started, 1)})
                        save_report(report_path, report)
                        print(json.dumps({'phase': 'download', 'date': str(day), 'day_rows': manifest['rows'],
                                          **{k: report[k] for k in ('completed_dates', 'rows_downloaded', 'raw_bytes', 'elapsed_seconds')}}), flush=True)
                        submit()
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
        report['status'] = 'packaging'
        save_report(report_path, report)
        package_season(raw, output, manifests, report)
        report['status'] = 'complete'
    except (Exception, KeyboardInterrupt) as error:
        report.update({'status': 'aborted', 'error': f'{type(error).__name__}: {error}'})
        save_report(report_path, report)
        print(json.dumps({'status': 'aborted', 'error': report['error']}), file=sys.stderr, flush=True)
        return 1
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        report['elapsed_seconds'] = round(time.monotonic()-started, 1)
        save_report(report_path, report)
    print(json.dumps({key: report[key] for key in ('status', 'rows', 'distinct_games', 'archive', 'archive_bytes')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
