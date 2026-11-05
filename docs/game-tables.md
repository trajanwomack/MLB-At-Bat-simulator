# Game-log tables before modeling

There are two main derived datasets:

| Table | One row | Key |
| --- | --- | --- |
| `pitch_events.parquet` | One accepted recorded pitch or automatic event | `game_pk`, `at_bat_number`, `pitch_number` |
| `plate_appearances.parquet` | One unambiguous completed plate appearance | `game_pk`, `at_bat_number` |

The untouched Savant CSVs/season ZIP remain the source. Audit files explain
quarantined records and appearances that require review. They are not extra
training datasets. This step does not select model features, calculate rolling
profiles, create train/test splits, or fit models.

## Build or install locally

Activate the project's `.venv`. With the acquired raw ZIP in your project:

```bash
python -m mlb_at_bat.cli build-game-tables --input data/raw/statcast_2026_regular_season.zip --output data/processed/game_logs_2026
```

Alternatively, use the existing daily source directory:

```bash
python -m mlb_at_bat.cli build-game-tables --input data/raw/savant_2026_regular --output data/processed/game_logs_2026
```

Combined plain CSV input is supported too. It must be grouped by date in
ascending order, as in our acquired season CSV. Complete date groups are
processed together so appearances cannot be split at an arbitrary chunk size.
The season ZIP is streamed without extracting its 448-MB CSV. Daily input
manifests and hashes are checked; the ZIP/container hash and logical source
record offsets are recorded. No source file is overwritten.

The output must be a new directory. For a revised run, choose a new version
directory. A successful build is published only after all batches pass; an
error removes the staged partial build and leaves existing source data intact.
Games split across batches/dates require source reconciliation rather than
silently receiving partial PA labels.

The separately supplied derived-data ZIP can instead be extracted directly to
`data/processed/game_logs_2026/`. Keep the files together with `quality_report.json`.
Large data files remain outside Git. Small review examples are versioned in
`datasets/2026/game_table_examples/` for immediate inspection after cloning.

## Clean pitch events

All available source columns are retained with typed IDs, nullable numeric
measurements, dates, and normalized categorical/null values. The existing
cleaner supplies validation for keys, counts, dates, duplicate source columns,
exact duplicate rows, and conflicting pitch keys. Invalid required context is
quarantined. Optional malformed measurements become missing and are counted
in the report; raw provenance is retained. Missing contact or tracking values
are not filled with zero, and signed measurements retain their signs.

Automatic balls and strikes remain in the table because they affect count
context. `is_automatic_event` identifies them. `pitch_type_eligible` is only a
candidate flag: the pitch has a populated type other than UN/UNKNOWN and is not
an automatic event. It does not establish complete sequences, a final pitch
taxonomy, or complete model features. For example, pitchouts remain visible;
future modeling can define their treatment explicitly.

Source pitch codes are not collapsed to the existing repertoire-baseline
taxonomy. The logs also expose codes such as KC, EP, FA, and CS; reconcile
these deliberately before using repertoire probabilities in a pitch classifier.

Rows are sorted by game date, game ID, PA number, and pitch number. Source
file/container and logical record references point back to the original
record even when the source was in reverse order. Additional fields show PA
status, sequence completeness, stable matchup, and PA outcome eligibility.

`count_transition_status` compares the current pre-pitch count with the prior
record's description within the same PA. Balls increment balls, strikes
increment strikes, and ordinary two-strike fouls retain two strikes. Foul
tips and foul bunts remain distinct source descriptions and can produce a
third strike. Unknown transition descriptions are marked unverified. No count
is repaired or inferred over a missing record.

## Completed plate appearances

Completion requires exactly one recognized batting-terminal event on the
group's final record. Interrupted (`truncated_pa`), unknown, and ambiguous
terminal groups are preserved in the audit rather than assigned invented
outcomes. The table keeps `terminal_event` and the detailed mapped outcome.

`pa_outcome` uses the same eight categories as the descriptive hitter baseline:
strikeout, walk, HBP, single, double, triple, home run, and other PA outcome.
Intentional walks map to walk. Errors, choices, sacrifices, interference,
ordinary outs, and other mapped terminal results remain distinguishable via
`terminal_event`/`outcome_detail`; they are grouped as `other_pa_outcome` only
for that broad label.

IDs `batter`, `pitcher`, and `fielder_2`, handedness, inning, outs, runners,
and scores refer to the **first recorded event**. Missing starting context is
preserved, not taken from a later populated row. Ending batter, pitcher, and
catcher IDs are separate. No final pitch velocity, contact quality, or
post-pitch score is copied into the PA-start context.

A completed PA may still have `outcome_eligible=False`. This conservative flag
requires a sequence starting at pitch 1 and 0–0, contiguous pitch numbers,
consistent count transitions, a stable batter/pitcher matchup, no rejected
source records, and no unmapped source event. Substitutions and incomplete
sequences remain inspectable completed outcomes, without attributing the whole
appearance to one unchanged matchup. Catcher changes are retained but do not
alone change the batter/pitcher stability flag.

## Audit files and feature boundaries

- `rejected_rows.parquet`: invalid/conflicting pitch records and rejection reasons.
- `pa_exclusions.parquet`: unresolved or ineligible PA groups and explicit reasons.
- `quality_report.json`: accounting totals, source hashes, statuses, labels,
  numeric issues, and hashes of all generated files.
- `data_dictionary.csv`: each column's type and its role/availability.

Every source record is accounted for as an accepted pitch event, a quarantined
record, or an explicitly reported exact duplicate. A disk-backed index rejects
cross-batch key duplication. Parquet uses Zstandard compression and daily row
groups, supporting column selection and date filtering without loading a full
season dataframe.

The pitch table retains actual velocity, movement, location, contact results,
post-pitch scores, and days until the next game. These are useful for analysis
but cannot all be supplied to a model predicting pitch selection before the
pitch. The dictionary marks targets, pre-pitch/PA-start context, future-game
fields, and source fields requiring review. Audit flags can also depend on
later events. Do not use the entire dataframe as a feature matrix.

No full-season 2026 player percentages are joined to earlier 2026 events.
Historical profiles and lagged features will be a separate step, using only
information available before each prediction.

## VS Code exploration

`exploratory analysis.py` initializes `df_pitch_events` and
`df_plate_appearances`. With the full tables installed, it loads June 1–7 by
default; change `GAME_START` and `GAME_END` to explore another period. If the
full tables are absent, the cell explicitly loads the small versioned review
sample instead. The review sample is not a training dataset.

For a narrower view or a smaller memory footprint:

```python
import pandas as pd

events = pd.read_parquet(
    "data/processed/game_logs_2026/pitch_events.parquet",
    columns=["game_date", "game_pk", "pitcher", "batter", "balls", "strikes", "pitch_type"],
    filters=[("game_date", ">=", pd.Timestamp("2026-06-01")),
             ("game_date", "<=", pd.Timestamp("2026-06-07"))],
)
```
