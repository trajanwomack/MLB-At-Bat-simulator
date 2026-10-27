# Uploaded 2026 pitcher-season summary

The user supplied `stats.csv` as 2026 pitcher statistics with a 10-inning
minimum and confirmed the season. The export URL is unavailable. Game-type
filters and the statistical cutoff date are not encoded in the file; they
have not been independently verified. Import time is not the statistics'
as-of date.

The file is a pitcher-season summary, with one row per `(player_id, year)`.
It contains no pitch sequence, pitch classification, individual game date,
batter ID, or direct batter–pitcher matchup history.

Raw SHA-256: `51c587b5b08ee0b7864f1c380bb0ccefeb967d2e279bf03bc7e95d3bd53e5679`.

| Finding | Result |
| --- | ---: |
| Source records / distinct pitchers | 649 / 649 |
| Source columns | 113 |
| Season labels | 2026 only |
| Innings range | 10–214 |
| Batters faced per pitcher (`pa`) | 44–894 |
| Source pitches totaled across the supplied pitchers | 697,083 |
| Duplicate pitcher-season records | 0 |
| Rejected records | 0 |
| Records retained with quality flags | 2 |
| Fully empty source columns | 2 |
| Numeric parse failures | 0 |

`p_opp_batting_avg` and `p_opp_on_base_avg` are blank for all 649 pitchers.
They were omitted from the cleaned profile and preserved in the raw archive.
No values were imputed. All other source fields were preserved, with canonical
name/year/innings-display column names and reviewed numeric types.

All rows match the 10-inning minimum. Baseball innings notation reconciles
exactly with recorded outs: `145.2` means 145 innings plus two outs, so decimal
innings are derived as `p_out / 3`, never by parsing the display as a decimal.

All rows pass the implemented reconciliations for hit components, total bases,
balls plus strikes versus total pitches, earned plus unearned runs, and the
displayed ERA, strikeout percentage, walk percentage, batting average, and
slugging percentage within their displayed rounding precision. Each check's
evaluated count appears in the JSON report; passing these checks does not
establish external accuracy or complete MLB coverage.

Two game-appearance discrepancies remain visible without changing the source:

| Pitcher | Player ID | Games | Starts | Relief appearances |
| --- | ---: | ---: | ---: | ---: |
| Kyle Freeland | 607536 | 26 | 26 | 1 |
| Orion Kerkering | 689147 | 62 | 0 | 63 |

These receive `games_starts_relief_mismatch`. Whether this reflects an export
scope/counting difference or an error is unconfirmed. Neither record is
discarded, and the supplied numbers are unchanged.

Although described as basic/regular statistics, the export also contains
wOBA, hard-hit percentage, whiff percentage, swing percentage, and additional
speed metrics. The speed-metric definitions need review before model use.
These fields remain available. Strikeout, walk, hard-hit, whiff, and swing
percentage-point fields retain their source units, with separately named
fraction columns. `slg_percent` is a decimal slugging rate and is not divided
by 100. Additional per-PA rates are recomputed from the corresponding counts.

## Generated files

- `data/raw/pitcher_seasons_2026/stats.csv`: byte-for-byte source archive.
- `data/raw/pitcher_seasons_2026/stats.manifest.json`: origin metadata and hash.
- `data/processed/pitcher_seasons_2026/pitcher_profiles.parquet`: typed profiles.
- `data/processed/pitcher_seasons_2026/pitcher_profiles.csv`: all cleaned profile
  columns, including preserved source statistics, derived fields and flags.
- `data/processed/pitcher_seasons_2026/pitcher_summary.csv`: compact review table.
- `data/processed/pitcher_seasons_2026/flagged_pitchers.csv`: the two flagged rows.
- `data/processed/pitcher_seasons_2026/rejected_rows.parquet`: no rejected rows.
- `data/processed/pitcher_seasons_2026/quality_report.json`: full audit and schema.

These ignored data artifacts are not automatically committed to Git. The
cleaner, tests, and this report are repository files. Retain the raw CSV when
backing up or publishing the prepared filesystem.

## Role in the simulator

Join `player_id` to the pitch dataset's pitcher ID, after confirming the ID
namespace. The table can support pitcher profiles, population comparisons,
and prior-season baseline rates. It cannot train next-pitch selection or
provide direct batter-versus-pitcher sample sizes on its own.

Do not attach full-season 2026 totals to earlier 2026 pitches as if those totals
were known then. Use dated/as-of summaries or earlier-season statistics for
historical evaluation. The 10-inning filter excludes lower-workload pitchers,
so this file alone does not define an unfiltered league population.
