# MLB At-Bat Simulator

## Open locally in VS Code

Clone `https://github.com/trajanwomack/MLB-At-Bat-simulator.git`, then open
`mlb-at-bat.code-workspace` in VS Code. Accept the suggested Python, Jupyter,
and CSV extensions if desired.

Use Python **3.12** for the tested dependency versions. From the repository
root, run:

```bash
python scripts/setup_workspace.py
```

On Windows, `py -3.12 scripts/setup_workspace.py` also selects Python 3.12.
On macOS/Linux, use `python3.12 scripts/setup_workspace.py` if `python` does
not select that version. The script creates a local `.venv`, installs pinned
dependencies, regenerates cleaned datasets and both statistical baselines,
and runs the tests. It does not require Savant access or credentials.
Select the `.venv` Python interpreter
and notebook kernel in VS Code afterward.

You can browse the versioned CSVs immediately, before setup:

- `datasets/2026/views/pitchers.csv`: 649 pitcher profiles.
- `datasets/2026/views/hitters.csv`: all 630 hitter profiles.
- `datasets/2026/views/hitters_min_100_pa.csv`: the 465-player review subset.
- `datasets/2026/views/pitch_arsenal_types.csv`: 3,174 pitcher–pitch-type rows.
- `datasets/2026/baselines/`: pitch-selection probabilities and repertoire coverage.
- `datasets/2026/baselines/hitter_outcomes/`: hitter PA outcome probabilities and profiles.
- `datasets/2026/pitchers/` and `datasets/2026/hitters/`: original inputs.
- `notebooks/01_data_review.ipynb`: a guided view of the tables and flags.
- `exploratory analysis.py`: run cells for EDA, pitch sampling, and hitter PA outcomes.

Reference CSVs and baseline review snapshots are versioned. `.venv/`, caches, and regenerated
working outputs under `data/` remain ignored. The older cloud-only paths below
are historical setup instructions; use this section for local VS Code setup.
The setup script builds pitcher and hitter statistical baselines; no ML model is fitted.

The first milestone is a traceable Baseball Savant dataset for next-pitch
prediction and batter–pitcher outcome statistics. This repository currently
provides ingestion, conservative cleaning, quality reports, exploratory analysis,
and reusable pitch-selection and hitter-outcome baselines. It does not fit an ML model or run a
web application yet.

## Pitch-selection baseline

Open `exploratory analysis.py` and use **Run Cell** with the `.venv` interpreter.
The pitch-selection sections let you choose a pitcher, inspect their repertoire,
and compare seeded pitch samples with the baseline probabilities. Original
usage percentages remain unchanged; small rounding differences are normalized
only for sampling. Unavailable or incomplete repertoires are explicitly marked.

With the `.venv` interpreter active, run from the repository root:

```bash
python -m mlb_at_bat.cli build-pitch-baseline
python -m mlb_at_bat.cli sample-pitches --player-id 453286 --season 2026 --n-pitches 10000 --seed 42
```

All 649 pitchers currently have usable distributions across 3,174 reported
pitch-type rows. These are independent pitch selections from season usage;
count, hitter context, and pitch results are later steps. The separate hitter
baseline below is not yet connected to this pitch sampler.
See [the reusable API and data rules](docs/pitch-selection.md) and
[the 2026 baseline review](reports/pitch-selection-2026.md).

## Hitter outcome baseline

The hitter baseline calculates strikeout, walk, HBP, single, double, triple,
home-run, and other-PA-outcome probabilities from counts divided by PA. All
630 hitter profiles remain available, including 165 below the optional 100-PA
comparison threshold. Source counts remain unchanged; inconsistent or missing
counts produce an unavailable status instead of invented probabilities.

Use the hitter-selection cells in `exploratory analysis.py`, or run:

```bash
python -m mlb_at_bat.cli build-hitter-baseline
python -m mlb_at_bat.cli sample-plate-appearances --player-id 592450 --season 2026 --n-pa 100 --seed 42
```

These are independent hypothetical plate appearances based on a hitter's
observed season rates, with no pitcher adjustment. The remaining-outcome
category includes ordinary outs and other events; it is not labeled an out.
See [the API and outcome rules](docs/hitter-outcomes.md) and
[the 2026 hitter baseline review](reports/hitter-outcomes-2026.md).

Use the existing checkout in this isolated cloud task; do not create a Git
worktree for ordinary development.

## Setup

Run from `/workspace/MLB-At-Bat-simulator` with Python 3.12:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

`requirements.lock` pins the tested runtime and test dependencies for this
Python/Linux environment. The package is installed separately in editable mode.
No credentials, database, or background service are required.

## First verified dataset

The historical sample is an eight-game Savant export from September 3, 2020,
published in the public `pybaseball` test fixtures at a pinned Git commit.
It is an explicitly labeled development sample, not a fresh Savant download
or a representative training dataset.

```bash
.venv/bin/mlb-data sample
.venv/bin/mlb-data clean \
  --input data/raw/historical_sample \
  --output data/processed/historical_sample
```

The raw file has a provenance manifest containing its source URL, retrieval
time, and SHA-256. Repeating `sample` verifies and reuses the cache. Existing
raw files are never overwritten. Source provenance is included in the quality
report. Data and virtual environments are ignored by Git; preserve the cloud
snapshot or export these files separately if needed.

See [the sample findings](reports/historical-sample.md) and
[the cleaning rules](docs/data-cleaning.md).

## Download recent data when Savant access works

Start with one day before expanding to the planned 2025 sample week:

```bash
.venv/bin/mlb-data fetch --start 2025-04-01 --end 2025-04-01
.venv/bin/mlb-data clean \
  --input data/raw/savant \
  --output data/processed/savant
```

The downloader requests regular-season pitch details, one day at a time,
checks the CSV shape and returned game dates, and stores immutable raw files
plus manifests. TLS verification remains enabled. It fails explicitly on an
HTTP error and never silently substitutes the historical fixture. Previously
downloaded days can be reused after verifying their manifests and hashes.
Savant detail downloads were successfully validated in November 2026. Earlier
sessions encountered proxy HTTP 403 responses; availability can vary by host
and session. The MLB schedule API remains unavailable from this environment.

For a resumable season acquisition with two workers, bounded resource use,
daily source manifests, and a streamed combined CSV archive, see
[the season acquisition workflow](docs/season-acquisition.md):

```bash
python scripts/acquire_season.py --season 2026 --through 2026-11-03 --workers 2 --raw data/raw/savant_2026_regular --output data/acquisition/savant_2026_regular
```

The pitch records remain separate from the player-summary exports and model
baselines. Large acquired data is preserved separately from Git.
The [2026 acquisition review](reports/2026-season-acquisition.md) records the
completed export: 716,792 pitch/event rows across 2,429 played games, including
the coverage reconciliation for a cancelled Orioles–Yankees game.

You can also import a CSV exported manually from Savant's search interface:

```bash
.venv/bin/mlb-data clean \
  --input /path/to/export.csv \
  --output data/processed/imported
```

Imports without a download manifest are labeled `user_supplied_csv`, not
verified live downloads. Keep output directories separate from raw inputs.

## Outputs

Each cleaning run writes:

- `pitches.parquet`: chronological, typed records with source-record references
  and a candidate pitch-type eligibility flag.
- `plate_appearances.parquet`: reconstructed groups, mapped batting outcomes,
  completion and sequence flags, and conservative matchup eligibility.
- `rejected_rows.parquet`: questionable records and explicit rejection reasons.
- `quality_report.json`: input hashes and provenance, row accounting, null counts,
  duplicate handling, event distributions, and unresolved cases.

The cleaner preserves unknown and deprecated source columns for inspection.
These tables contain information observed after the pitch: they must not be
passed wholesale into a next-pitch model. Feature construction and historical
matchup aggregation are subsequent milestones.

## Uploaded pitcher-season summaries

Season summaries use a separate command; they are not individual pitch rows.
To archive and clean an uploaded Savant-style pitcher summary:

```bash
.venv/bin/mlb-data clean-pitchers \
  --input /path/to/stats.csv \
  --raw-output data/raw/pitcher_seasons_2026/stats.csv \
  --output data/processed/pitcher_seasons_2026 \
  --min-innings 10
```

After the first import, the archived raw CSV can also serve as `--input`.
No network access is needed. Repeating the command verifies and reuses the
raw archive; differing input at an existing raw destination is refused.

Outputs include full `pitcher_profiles.csv` and Parquet files, a compact
`pitcher_summary.csv`, `flagged_pitchers.csv`, rejected rows, and a JSON quality
report. Blank source columns can be omitted from the clean table; all supplied
values remain in the byte-for-byte raw archive. Malformed optional values are
flagged and their columns retained, even if coercion makes the column empty.
Unknown columns remain strings. Conflicting duplicate player-season records
and invalid identities/innings-threshold records are quarantined.

Baseball innings notation is retained as text and decimal innings are computed
from recorded outs. Percentage-point fields retain their source units alongside
explicit fractional variants; slugging and other decimal rates retain their
existing scales. Arithmetic/rate discrepancies are flagged rather than silently
corrected. See [the 2026 upload findings](reports/pitcher-season-2026.md).

Use player IDs for joins, not names. Full-season summaries cannot reconstruct
pitch sequences or direct batter–pitcher history. They also cannot be used as
features for earlier pitches in that same season without leaking future data.

## Related pitcher exports and combined view

Keep separate raw exports for stats, bat tracking, contact quality, location,
and arsenal. The combined master is generated from cleaned category tables;
it is not the only copy of the data. Player ID and season are the join keys.

To import a new batch, replace the five input paths below with your files:

```bash
.venv/bin/mlb-data clean-pitcher-exports \
  --stats /path/to/2026pitcher_statcast_stats.csv \
  --bat-tracking /path/to/2026_pitcher_battracking_10innings.csv \
  --contact /path/to/2026pitcher_quality_contact_10inning.csv \
  --location /path/to/2026pitcher_pitch_location_10inning.csv \
  --arsenal /path/to/2026pitcher_pitch_arsenals_10innng.csv \
  --raw-output data/raw/pitcher_exports_2026_10ip \
  --output data/processed/pitcher_exports_2026_10ip
```

After import, the same command can use `stats.csv`, `bat_tracking.csv`,
`contact.csv`, `location.csv`, and `arsenal.csv` from the raw directory as
inputs. It verifies cached hashes and uses no network. Re-exported files with
different contents need a new raw directory; raw archives are never replaced.
Do not supply both the original stats file and its identical renamed copy as
separate datasets.

The output directory contains category CSV/Parquet tables, a cleaned `stats/`
subdirectory, `pitcher_master.csv` and Parquet, `pitch_arsenal_types.csv` and
Parquet, `source_differences.csv`, `data_dictionary.csv`, and `quality_report.json`.
The last report marks a completed run. See
[the five-export audit](reports/pitcher-exports-2026-10ip.md).

The stats table anchors a one-row-per-pitcher-season left join. Coverage flags
show missing category rows, and no pitcher is dropped merely for missing
advanced statistics. Each join validates one-to-one keys. Equal known shared
metrics use the stats column in the master after comparing values; conflicting
values retain separate source columns. New category fields carry source
prefixes, so the origin remains visible. Category tables retain their source
metrics independently.

Arsenal `n_*_formatted` fields are usage percentages, renamed to
`*_usage_percent`; `n` becomes `arsenal_pitch_total`. The long arsenal table
contains individual pitch types and observed percentages, with separate
fractional values. Fastball/breaking/offspeed family totals remain in the wide
arsenal table and are not added as extra pitch types. No integer per-type pitch
counts are inferred from rounded percentages. Known empty type columns remain
nullable; an unreported type is not automatically converted to zero.

Signed movement and angle values remain signed. Source units are retained;
source metric definitions should be reviewed before modeling. Comparisons of
arsenal, zone, or pitch-family totals are reported as source differences, not
silently corrected. `has_source_total_difference` is separate from the basic
stats quality flags. It does not by itself mean a pitcher should be excluded.

## Hitter profiles and specialist measurements

`clean-hitter-exports` imports nine player-season files. It produces a batting
master and separate catcher, catch-probability, jump, and running tables. See
[the hitter audit and reproduction command](reports/hitter-exports-2026.md).

The full master retains 630 players. A flag and separate view identify the
465 with at least 100 PA, preserving the other 165. No innings threshold is
applied to hitters. File rows without specialist measurements remain nullable;
file coverage and actual measurements are separate flags. Signed OAA,
expected-stat differences, angles, and jump metrics remain signed. The running
export's home-to-first time and bolts are not relabeled as sprint speed.
