# 2026 hitter outcome baseline review

The baseline uses the cleaned hitter master supplied for the 2026 season.
Exact export URLs, game-type filters, and statistical cutoff dates remain
unverified. This sprint required no new data download or game logs.

| Check | Result |
| --- | ---: |
| Hitter-season profiles retained | 630 |
| Outcome probability rows, eight per hitter | 5,040 |
| Usable profiles | 630 |
| Missing required counts | 0 |
| Hit-component mismatches | 0 |
| Negative PA remainders | 0 |
| Hitters with at least 100 PA | 465 |
| Hitters below 100 PA, retained | 165 |
| PA range | 10–726 |

Every distribution sums to one, with probabilities between zero and one.
Source hit components reconcile to total hits, and the eight outcome counts
reconcile to PA. Probabilities are count/PA, with no smoothing or rounding
normalization. Source CSVs and their original checksums remain unchanged.

The cohort totals 183,711 PA. Counts are 40,652 strikeouts, 16,329 walks,
2,117 HBP, 26,093 singles, 7,489 doubles, 671 triples, 5,573 home runs, and
84,787 other PA outcomes. The 545 intentional walks are already included in
walks and are not added again. The residual combines ordinary outs and other
events; it does not isolate balls in play, sacrifices, errors, or interference.

## Example: Aaron Judge, player ID 592450

The supplied profile contains 285 PA. Using seed 42 for 100 independent
hypothetical plate appearances in the tested Python 3.12 environment:

| Outcome | Season count | Probability | Sampled count |
| --- | ---: | ---: | ---: |
| Strikeout | 85 | 0.298246 | 36 |
| Walk | 43 | 0.150877 | 13 |
| HBP | 2 | 0.007018 | 0 |
| Single | 29 | 0.101754 | 8 |
| Double | 10 | 0.035088 | 4 |
| Triple | 0 | 0.000000 | 0 |
| Home run | 18 | 0.063158 | 10 |
| Other PA outcome | 98 | 0.343860 | 29 |

The hypothetical counts total 100 and fluctuate around the input probabilities.
For example, a nonzero HBP probability can produce no HBP in a 100-PA sample.
Zero observed triples produce zero triple probability in this unsmoothed
baseline. Neither result establishes the hitter's underlying future ability.

## Reproduce and inspect

Run `python scripts/setup_workspace.py` and use the hitter-selection cells in
`exploratory analysis.py`. With the project virtual environment active:

```bash
python -m mlb_at_bat.cli build-hitter-baseline
python -m mlb_at_bat.cli sample-plate-appearances --player-id 592450 --season 2026 --n-pa 100 --seed 42
```

Working CSV, typed Parquet, and quality-report outputs live in
`data/processed/hitter_outcomes_2026/`. CSV review snapshots and the input-hash
report are versioned under `datasets/2026/baselines/hitter_outcomes/` and
included in `datasets/SHA256SUMS.json`.

Validation covers PA denominators, intentional-walk handling, residual counts,
missing and inconsistent data, sample-size flags, reproducible sampling,
frequency agreement, export provenance, and exact snapshot regeneration.
This is a descriptive hitter baseline with no opponent or pitch context;
predictive evaluation and matchup adjustments remain future work.
