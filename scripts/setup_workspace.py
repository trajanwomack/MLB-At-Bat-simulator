"""Set up a local checkout and regenerate datasets on Windows, macOS, or Linux."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-install', action='store_true', help='Reuse an already prepared .venv')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    datasets = root / 'datasets'
    for name, expected in json.loads((datasets / 'SHA256SUMS.json').read_text()).items():
        path = (datasets / name).resolve()
        if not path.is_relative_to(datasets.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f'Reference data checksum mismatch: {name}')
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        if args.skip_install:
            raise SystemExit('No .venv found. Run without --skip-install first.')
        venv.create(environment, with_pip=True)

    def run(*command):
        subprocess.run([str(c) for c in command], cwd=root, check=True)

    if not args.skip_install:
        run(python, '-m', 'pip', 'install', '-r', root / 'requirements.lock',
            '-r', root / 'requirements-vscode.txt')
        run(python, '-m', 'pip', 'install', '--no-deps', '-e', root)
    run(python, '-m', 'pip', 'check')
    reference = root / 'datasets/2026'
    command = [python, '-m', 'mlb_at_bat.cli', 'clean-pitcher-exports']
    for name in ['stats', 'bat_tracking', 'contact', 'location', 'arsenal']:
        command += ['--'+name.replace('_','-'), reference / 'pitchers' / f'{name}.csv']
    run(*command, '--raw-output', root/'data/raw/pitcher_exports_2026_10ip',
        '--output', root/'data/processed/pitcher_exports_2026_10ip')
    command = [python, '-m', 'mlb_at_bat.cli', 'clean-hitter-exports']
    for name in ['standard', 'statcast', 'contact', 'location', 'bat_tracking',
                 'catcher_poptime', 'catch_probability', 'jump', 'running']:
        command += ['--'+name.replace('_','-'), reference / 'hitters' / f'{name}.csv']
    run(*command, '--raw-output', root/'data/raw/hitter_exports_2026',
        '--output', root/'data/processed/hitter_exports_2026', '--min-pa', '100')
    run(python, '-m', 'pytest', '-q')
    print('Workspace prepared. Open notebooks/01_data_review.ipynb with the .venv kernel.')


if __name__ == '__main__':
    main()
