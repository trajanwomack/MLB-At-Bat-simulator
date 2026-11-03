# 2026 pitch-selection baseline review

The baseline uses the existing cleaned 2026 pitcher master and individual
pitch-type arsenal snapshots. Source exports are labeled 2026; exact export
filters and statistical cutoff dates remain unverified. No new Savant download
or historical game-log ingestion was needed.

| Check | Result |
| --- | ---: |
| Pitcher-seasons in the cohort | 649 |
| Reported individual pitch-type rows | 3,174 |
| Available repertoire distributions | 649 |
| Totals equal to 100% | 400 |
| Totals normalized within the rounding bound | 249 |
| Unavailable repertoires | 0 |
| Reported 0.0% rows, preserved with zero probability | 5 |
| Missing reported total pitch counts | 0 |

Reported totals range from 99.8% to 100.2%. Every repertoire fits its own
rounding bound of 0.05 percentage points per reported pitch type. Each available
distribution sums to one after normalization, with no negative probabilities.
The source percentages, pitch measurements, and original source hashes remain
unchanged. Broad pitch families are excluded from the individual-type table.

The 72 previously identified differences between arsenal and location total
pitches remain unresolved source-definition/coverage differences. This baseline
uses arsenal usage directly and retains its own total count; it does not
substitute another table's denominator. See `pitcher-exports-2026-10ip.md`.

## Example: Max Scherzer, player ID 453286

Reported arsenal total: 1,461 pitches. Usage sums to 99.9%; the quality status
is `normalized_rounding`. With 10,000 independent selections and seed 42 in
the tested Python 3.12 environment:

| Pitch | Reported usage % | Sampling probability | Sampled count | Sampled % |
| --- | ---: | ---: | ---: | ---: |
| CH | 15.5 | 0.155155 | 1,584 | 15.84 |
| CU | 14.6 | 0.146146 | 1,398 | 13.98 |
| FC | 5.7 | 0.057057 | 594 | 5.94 |
| FF | 47.2 | 0.472472 | 4,778 | 47.78 |
| SL | 16.9 | 0.169169 | 1,646 | 16.46 |

Differences from the baseline are ordinary sampling variation. The 10,000
selections are hypothetical pitches, not 10,000 plate appearances or additional
observations. This comparison validates sampler behavior, not predictive skill.

## Reproduce and inspect

Run `python scripts/setup_workspace.py` after cloning, then open the
pitch-selection cells in `exploratory analysis.py`. Alternatively, with the
project's virtual environment active, run:

```bash
python -m mlb_at_bat.cli build-pitch-baseline
python -m mlb_at_bat.cli sample-pitches --player-id 453286 --season 2026 --n-pitches 10000 --seed 42
```

Generated probability and coverage tables, in CSV and typed Parquet, live in
`data/processed/pitch_selection_2026/`. Read-only CSV review snapshots and their
input-hash report are versioned under `datasets/2026/baselines/` and included in
`datasets/SHA256SUMS.json`. The automated suite covers missing/incomplete data,
invalid units and keys, rounding bounds, reproducible sampling, zero usage,
large-sample behavior, export provenance, and all 649 source repertoires.

This sprint supplies a reusable probability lookup and a statistical sampler.
Contextual pitch prediction and predictive evaluation await game logs and
features calculated from information available before each target pitch.
