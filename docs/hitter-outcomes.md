# Hitter plate-appearance outcome baseline

This baseline estimates **P(outcome | hitter, season)** from observed counts.
It is descriptive: there is no opponent adjustment, pitch sequence, smoothing,
or ML fitting. Every probability uses plate appearances (PA), not official
at-bats (AB). Advanced metrics remain in the full hitter dataframe for EDA and
future models; they are not inputs to this count-based calculation.

## Outcomes and reconciliation

| Outcome | Source count |
| --- | --- |
| `strikeout` | `strikeout` |
| `walk` | `walk`, already including intentional walks |
| `hit_by_pitch` | `b_hit_by_pitch` |
| `single` | `single` |
| `double` | `double` |
| `triple` | `triple` |
| `home_run` | `home_run` |
| `other_pa_outcome` | PA minus strikeouts, walks, HBP, and hits |

The hit components must sum to `hit`. Then each usable probability is its
outcome count divided by PA. No rounding normalization is necessary. The
residual combines ordinary outs and other events, potentially including
sacrifices, reaching on error, and interference. It is not classified as an
out or as a ball-in-play result. Additional export columns are not added to
these categories because that could double-count events.

`outcome_count` is a historical source count (or the explicitly derived
remainder). `expected_count` and `sampled_count` in a simulation comparison
refer to the hypothetical sample and are not additional historical evidence.

## Explore in VS Code

Pull the latest repository, select the `.venv` interpreter, and open
`exploratory analysis.py`. Run cells in order. If the cleaned tables are
missing, run `python scripts/setup_workspace.py` from the repository root.

The hitter baseline sections initialize:

- `hitter_baseline`: the reusable lookup and sampler.
- `df_hitter_outcome_profiles`: counts, reconciliation checks, PA, and quality.
- `df_hitter_outcome_probabilities`: eight outcome rows per hitter-season.
- `df_selected_hitter_distribution`: a selected hitter's complete distribution.
- `df_sampled_plate_appearances`: hypothetical terminal outcomes.
- `df_hitter_outcome_comparison`: expected versus sampled frequencies.

Change `HITTER_ID`, `HITTER_SEASON`, `N_PA`, and `HITTER_RANDOM_SEED` to explore
another hitter. The initial example selects Aaron Judge (`592450`), 2026,
and samples 100 plate appearances with seed 42. This hitter sampler and the
pitcher pitch-selection sampler are independent components at this stage.

## Reusable Python steps

```python
from mlb_at_bat.hitter_outcomes import (
    build_hitter_baseline,
    compare_outcome_frequencies,
)

baseline = build_hitter_baseline(df_hitters, min_pa=100)
distribution = baseline.distribution(592450, 2026)
samples = baseline.sample_plate_appearances(592450, 2026, n_pa=100, seed=42)
comparison = compare_outcome_frequencies(distribution, samples)
```

The cleaned standard table can also be supplied; only the required keys and
counts are used. Source dataframes are never modified. Treat the object's
`profiles` and `probabilities` tables as read-only; `distribution()` returns
a separate copy. Lookup keys are MLB player ID and season, not player name.

## Quality and sample-size rules

| Quality status | Meaning |
| --- | --- |
| `ready` | Required counts reconcile and PA is positive |
| `unavailable_missing_counts` | A required count or PA is missing |
| `unavailable_zero_pa` | PA is zero |
| `unavailable_hit_mismatch` | Singles + doubles + triples + HR disagree with hits |
| `unavailable_negative_remainder` | Strikeouts + walks + HBP + hits exceed PA |

All profiles remain in the outputs, and unavailable profiles retain null
probabilities. The profile audit columns preserve missingness, hit-component
agreement, and the derived remainder, even if more than one issue applies.
Status precedence follows the order of unavailable statuses in this table.
Lookup or sampling for an unavailable or unknown hitter-season raises
`HitterBaselineUnavailable`, a `ValueError` with the reason.

Missing counts are never replaced with zero. A known zero count produces a
zero probability and stays visible in lookup and comparison tables. Fractional,
negative, nonfinite, or malformed counts raise errors; invalid/missing IDs,
duplicate player-season keys, and missing required columns also raise errors.

`min_pa=100` creates a comparison flag, not an eligibility cutoff. Hitters below
the threshold remain available for sampling when their counts are valid. No
league-average shrinkage is applied; small-sample proportions can be unstable.

Sampling is independent and with replacement. A seed reproduces a sequence in
the tested Python environment without changing global random state. Reusing a
seed starts the same sequence again. A zero-length request returns an empty
sample; its comparison has zero counts and missing observed percentages.

## Command-line exports

With the project's `.venv` active, run from the repository root:

```bash
python -m mlb_at_bat.cli build-hitter-baseline
python -m mlb_at_bat.cli sample-plate-appearances --player-id 592450 --season 2026 --n-pa 100 --seed 42 --output data/simulations/judge_2026
```

Both commands use `datasets/2026/views/hitters.csv` by default. Override it
with `--hitters` pointing to a cleaned CSV or Parquet table. `--min-pa` changes
only the review flag. No network access is required.

The build command writes `hitter_outcome_probabilities.csv`/`.parquet`,
`hitter_outcome_profiles.csv`/`.parquet`, and `quality_report.json` to
`data/processed/hitter_outcomes_2026/`. The report contains the input SHA-256,
quality counts, outcome definitions, and PA threshold.

The sampling command prints the frequency comparison. Optional `--output`
writes `sampled_plate_appearances.csv`, `outcome_frequency_comparison.csv`, and
`simulation.json` containing the input hash, hitter, season, sample size, and
seed. Omit `--output` to inspect results without writing files.

Read-only CSV review snapshots and their report are versioned under
`datasets/2026/baselines/hitter_outcomes/`. Generated working outputs under
`data/` remain ignored by Git.

## Future matchup work

The probabilities describe results against the hitter's observed mix of
opponents. They do not establish future ability or predict performance against
a selected pitcher. Pitch type, count, handedness, and previous pitches are
not modeled. Simulating 100 PA produces hypothetical results under these
assumptions, not 100 observed batter-versus-pitcher appearances.

Historical game logs will support matchup adjustments and predictive evaluation.
When using these profiles as ML inputs, calculate them from information
available before the target event. Current exports have no verified as-of
date and cannot be treated as pre-event features for earlier games that season.
