# 2026 game-log table build

The acquired 2026 regular-season daily exports were converted to two main
tables, with typed nullable columns, source references, and explicit PA review
flags. All 307 input files were hash-verified; 184 dates contain records.
The source acquisition covers 2,429 played games after the confirmed
Orioles–Yankees rain cancellation described in the acquisition report.

| Main table | Rows | Columns |
| --- | ---: | ---: |
| Pitch events | 716,792 | 130 |
| Completed plate appearances | 183,849 | 48 |

All 716,792 source rows remain in the pitch-event table. No rows were rejected,
no exact duplicates were removed, and no optional numeric parse failures were
found. Game/PA/pitch keys are unique and the output order is date, game, PA,
then pitch number. Raw hashes were checked again after generation and remain
unchanged. Zstandard Parquet row groups are written one date at a time.

## Targets and context

The pitch table retains source pitch type, pre-pitch count, result description,
PA events, tracking measurements, automatic-event flags, and provenance.
There are 714,102 candidate pitch-type rows, 2,309 automatic events, 2,676
missing pitch types, and 14 UN codes. Candidate eligibility is not a final
feature policy or pitch-type taxonomy.

The completed-PA table contains exactly one recognized final batting result
per row. Its eight-category label distribution is:

| PA outcome | Count |
| --- | ---: |
| Other PA outcome | 84,845 |
| Strikeout | 40,700 |
| Single | 26,110 |
| Walk, including intentional walks | 16,337 |
| Double | 7,493 |
| Home run | 5,575 |
| Hit-by-pitch | 2,118 |
| Triple | 671 |

Exact source terminal events and detailed outcome categories remain available;
other PA outcome is not treated as synonymous with an out. Context and starting
player IDs come from the first recorded event, with ending IDs separate.
No final pitch measurements or post-pitch scores are added to PA-start context.
All participants are retained, without a season-profile cohort or minimum-PA filter.

## Review flags and incomplete appearances

There are 184,288 observed PA groups. Of the 183,849 completed appearances,
183,780 satisfy the conservative outcome-eligibility checks. The remaining
69 completed rows stay in the main table with explicit reasons:

- 68 have batter/pitcher changes within the appearance.
- One has an inconsistent count sequence: game 824212, PA 43, March 31.

The latter source shows a swinging strike at 0–2 followed by additional pitches,
and eventually a walk recorded from a 2–2 pre-pitch count. The data is retained,
flagged, and not repaired. A separate terminal K/walk count audit found the same
single contradiction and no additional eligible contradictions.

Another 439 groups have no unambiguous completed batting outcome. These include
329 `truncated_pa` groups; the other 110 have no recognized terminal batting
event. They remain in the pitch table and in the exclusion audit, without an
invented final label. There are no pitch-number gaps or unverified description
transitions in this acquired season. Missing tracking measurements remain missing.

`pa_exclusions.parquet` has 508 rows: the 439 unresolved groups plus 69
completed appearances requiring review. These are audit records; the main
completed-PA table still retains those 69 observed outcomes for inspection.

## Delivery and validation

The generated folder contains the two main Parquet files, rejected-row and
PA-exclusion audit files, `data_dictionary.csv`, and `quality_report.json` with
source/output hashes and record accounting. A separately supplied
`game_logs_2026_tables.zip` installs those files locally. See
[the machine-readable summary](game-tables-2026.json) for its byte size and
SHA-256. Large tables remain outside Git.

The small versioned example contains 94 events from 25 complete PA on March 25.
The analysis file uses June 1–7 when full tables are installed: 27,314 events
and 6,967 completed PA. Script execution, sequential VS Code cells, and explicit
sample mode were exercised. The optimized grouped PA reconstruction also
exactly matches the saved 636-group historical fixture.

Automated tests cover sequencing, two-strike fouls, automatic events, starting
null context, substitutions, incomplete appearances, quarantine accounting,
source integrity, duplicate batch boundaries, and equivalent daily-folder/ZIP
business tables. Source and output hashes, unique keys, sort order, completed
labels, and PA accounting were independently checked on the full output.

No full-season profile joins, historical feature calculations, train/test
splits, or model fitting were performed. Actual-pitch measurements, outcome
fields, future-game fields, and audit flags must not be passed wholesale into
a pre-pitch feature matrix. See [the data contract](../docs/game-tables.md).
