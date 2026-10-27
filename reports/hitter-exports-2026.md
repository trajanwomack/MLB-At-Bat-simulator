# 2026 hitter and specialist exports

All nine user-supplied CSVs contain the same 630 unique player-season keys,
labeled 2026. Raw bytes and SHA-256 manifests are preserved under
`data/raw/hitter_exports_2026/`. Export URLs, game-type filters, cutoff dates,
and the meaning of the filenames' `10innings` suffix are unverified.

| Category | Source rows | Columns | Rows with any measurements |
| --- | ---: | ---: | ---: |
| Standard batting / running totals | 630 | 78 | 630 |
| Statcast / expected outcomes | 630 | 16 | 630 |
| Contact quality | 630 | 16 | 630 |
| Pitch and location | 630 | 40 | 630 |
| Bat tracking | 630 | 15 | 630 |
| Catcher pop time | 630 | 13 | 105 |
| Catch probability | 630 | 19 | 232 |
| Jump | 630 | 8 | 230 |
| Running | 630 | 5 | 530 |

Measurement counts do not classify roster positions or establish adequate
sample sizes. File coverage and measurement coverage are distinct. Blank
specialist measurements remain missing, not zero.

Plate appearances range from 10 to 726. All 630 players are retained; 465 have
at least 100 PA and 165 have fewer. `meets_min_pa` flags the suggested threshold,
with a separate filtered view. No defensive-innings rule is inferred or applied.

The full batting master has 630 rows and 168 columns, combining standard,
Statcast, contact, location, and bat-tracking data. Catcher, fielding, jump,
and running measurements remain in separate player-season tables. Additional
running outcomes already in standard stats remain in the hitter profile.
IDs and counts are nullable integers; reviewed measurements retain their
source scales. Percentages stay in percentage points; AVG and SLG remain
decimal rates. Signed OAA, expected-stat differences, angles, and relative
jump values retain their signs. Source-record references are preserved.

All 630 rows pass implemented checks for hit components, total bases, balls
plus strikes versus pitches, and displayed K%, BB%, AVG, and SLG within rounding
tolerance. This is internal consistency, not independent MLB verification.
Unknown columns would be retained as strings. Invalid IDs, ambiguous keys,
malformed known numeric values, and invalid percentages stop an import.

`r_stolen_base_pct` is entirely blank in the original standard export and
remains missing. Its underlying supplied counts are preserved. The running
file has `n_bolts` and `hp_to_1b`, not sprint speed in feet per second. These
exports also lack batting handedness, individual pitch-type outcome splits,
and direct batter-pitcher history. Broad pitch-family counts are not those
outcome splits. No model training or pitch-log ingestion occurs here.

Outputs under `data/processed/hitter_exports_2026/` include nine CSV/Parquet
category tables, full and minimum-PA hitter masters, a data dictionary, quality
flags, and a complete JSON audit. Data directories are ignored by Git and need
separate preservation when transferring the project.

Reproduce offline from the archived sources:

```bash
.venv/bin/mlb-data clean-hitter-exports \
  --standard data/raw/hitter_exports_2026/standard.csv \
  --statcast data/raw/hitter_exports_2026/statcast.csv \
  --contact data/raw/hitter_exports_2026/contact.csv \
  --location data/raw/hitter_exports_2026/location.csv \
  --bat-tracking data/raw/hitter_exports_2026/bat_tracking.csv \
  --catcher-poptime data/raw/hitter_exports_2026/catcher_poptime.csv \
  --catch-probability data/raw/hitter_exports_2026/catch_probability.csv \
  --jump data/raw/hitter_exports_2026/jump.csv \
  --running data/raw/hitter_exports_2026/running.csv \
  --raw-output data/raw/hitter_exports_2026 \
  --output data/processed/hitter_exports_2026 --min-pa 100
```

Repeating verifies archives and regenerates views. Changed exports require a
different raw directory; original archives are never overwritten.
