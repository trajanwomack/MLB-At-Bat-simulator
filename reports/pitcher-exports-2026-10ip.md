# Cleaned 2026 pitcher exports, 10-inning cohort

Four replacement exports supplied by the user now match the original stats
cohort exactly. The raw files remain byte-for-byte archives with provenance
manifests. The user identifies these as 2026, minimum-10-IP exports; year labels
and cohort membership are verified below. Exact query URLs, game-type filters,
and statistical cutoff dates are unavailable.

| Source | Pitcher rows | Source columns | Missing / extra relative to stats |
| --- | ---: | ---: | ---: |
| Stats | 649 | 113 | 0 / 0 |
| Bat tracking | 649 | 15 | 0 / 0 |
| Contact quality | 649 | 16 | 0 / 0 |
| Location | 649 | 40 | 0 / 0 |
| Arsenal | 649 | 126 | 0 / 0 |

All sources have unique, populated `(player_id, year)` keys and 2026 labels.
All stats rows have at least 30 recorded outs, equivalent to 10 innings.
The original `stats.csv` and the uploaded `2026pitcher_statcast_stats.csv` have
the same SHA-256; they are the same dataset and are included once. Earlier
46-pitcher qualified-only exports are not included in this batch.

## Storage and output

Raw archives: `data/raw/pitcher_exports_2026_10ip/`.
Generated tables: `data/processed/pitcher_exports_2026_10ip/`.

Keep separate cleaned category tables as the authoritative inputs, with a
generated combined view for browsing. This avoids manual edits to one huge
sheet and allows an individual export to be refreshed independently.

The master has **649 rows and 313 columns**, including provenance, derived
stats, category coverage, and source-total-difference flags. All four advanced
category coverage flags are true for all 649 pitchers. Names also agree by ID.
CSV and typed Parquet versions are supplied. A data dictionary records source
column names, cleaned names, numeric types, and missingness for category tables.

The long arsenal table has **3,174 reported pitcher–pitch-type rows**, keyed by
player ID, season, and pitch type. Broad fastball/breaking/offspeed families
remain available in the wide table, not as additional individual pitch types.

## Cleaning and reconciliation

- Normalize whitespace, null tokens, headers, join keys, and reviewed numeric
  types. Refuse invalid keys, ambiguous duplicates, malformed known numeric
  values, or percentages outside 0–100 in category imports.
- Preserve all source values, including valid negative angles and pitch
  movement. Keep empty arsenal measurements nullable, including the fully
  blank screwball columns; do not invent zeros or pitch measurements.
- Rename arsenal `n` to `arsenal_pitch_total`. Rename `n_ff_formatted` and its
  counterparts to explicit usage-percentage columns. These are percentages,
  not per-type pitch counts. Exported usage sums range from **99.8% to 100.2%**;
  all 649 sums fit the tolerance implied by rounding to one decimal place.
  No usage totals are forcibly normalized and no exact counts are invented.
- Five repeated metrics agree for every matched pitcher: contact hard-hit
  percentage, `avg_best_speed`, `avg_hyper_speed`, and location swing/whiff
  percentages. Keep the stats version in the master and retain all metrics
  in their category tables. A future disagreement preserves both versions.
- Retain the two previously reported stats flags for Freeland and Kerkering;
  no basic stats rows are rejected.

## Source-total differences needing interpretation

| Comparison | Pitchers with different totals | Largest shortfall |
| --- | ---: | ---: |
| Location pitches versus stats total pitches | 0 | 0 |
| Arsenal pitches versus location pitches | 72 | 36 |
| In-zone plus out-of-zone versus location total | 26 | 35 |
| Pitch-family counts versus location total | 71 | 36 |

These are numeric differences, not confirmed source errors. Tracking coverage,
unclassified pitches, automatic events, or metric definitions could explain
some differences; their causes have not been verified. Keep each supplied
total and inspect the player-level records in `source_differences.csv`.
Do not use one source's denominator to reinterpret another source's percentages.

## Reproduction

From the repository root, no network is needed after the raw files exist:

```bash
.venv/bin/mlb-data clean-pitcher-exports \
  --stats data/raw/pitcher_exports_2026_10ip/stats.csv \
  --bat-tracking data/raw/pitcher_exports_2026_10ip/bat_tracking.csv \
  --contact data/raw/pitcher_exports_2026_10ip/contact.csv \
  --location data/raw/pitcher_exports_2026_10ip/location.csv \
  --arsenal data/raw/pitcher_exports_2026_10ip/arsenal.csv \
  --raw-output data/raw/pitcher_exports_2026_10ip \
  --output data/processed/pitcher_exports_2026_10ip
```

The same command verifies the archives and regenerates the derived views.
Files under `data/` remain ignored by Git and need separate preservation when
moving the project. No model training or pitch-by-pitch log ingestion occurs
in this cleaning step.
