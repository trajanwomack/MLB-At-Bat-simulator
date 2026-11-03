# Pitch-selection baseline

The baseline estimates **P(pitch type | player_id, season)** from the cleaned
pitcher repertoire. It supplies reusable probabilities and independent random
pitch selections. There is no ML fitting and no plate-appearance outcome yet.

## Explore in VS Code

After pulling the repository, select the `.venv` interpreter and open
`exploratory analysis.py`. Use **Run Cell** in order. If the cleaned working
tables are missing, run `python scripts/setup_workspace.py` from the root.

The new cells initialize:

- `pitch_baseline`: the reusable baseline object.
- `df_pitch_probabilities`: every reported individual pitch type, original
  usage, sampling probability, workload, and quality status.
- `df_pitch_repertoires`: one coverage/quality row per pitcher-season.
- `df_selected_repertoire`: the selected pitcher's probability lookup.
- `df_sampled_pitches`: one row per hypothetical pitch selection.
- `df_pitch_frequency_comparison`: expected versus sampled pitch frequencies.

Change `PITCHER_ID`, `SEASON`, `N_PITCHES`, and `RANDOM_SEED` to explore another
pitcher or sample size. The starting example is Max Scherzer, ID `453286`, 2026.
Use the dataframe viewer to inspect all columns. Read-only CSV snapshots in
`datasets/2026/baselines/` are also available immediately after cloning.

## Reusable Python steps

```python
from mlb_at_bat.pitch_selection import (
    build_pitch_baseline,
    compare_pitch_frequencies,
)

# Both inputs are the existing cleaned dataframes, not raw Savant exports.
baseline = build_pitch_baseline(df_pitcher_arsenal, pitchers=df_pitchers)
repertoire = baseline.repertoire(player_id=453286, season=2026)
samples = baseline.sample_pitches(453286, 2026, n_pitches=10000, seed=42)
comparison = compare_pitch_frequencies(repertoire, samples)
```

The cohort argument ensures pitchers without repertoire rows remain visible.
Omitting it builds coverage only for pitcher-seasons present in the arsenal.
Treat `baseline.probabilities` and `baseline.repertoires` as read-only;
`repertoire()` returns a copy. Keys are MLB player ID and season, not name.

Lookup and sampling are separate: later ML features can consume the
probability table without generating any random pitches. An unavailable
lookup raises `RepertoireUnavailable`, a `ValueError` with the reason; inspect
`baseline.repertoires` to see all availability statuses first.

## Rounding and missing data

Source usage is a percentage from 0 to 100, reported to one decimal place.
Each reported type can contribute at most 0.05 percentage points of rounding
error. For `k` reported types, the allowed sum deviation is `k * 0.05`, plus
negligible floating-point tolerance. A larger deviation is never silently
normalized. Fitting this bound does not prove the original export is complete;
it only establishes numerical compatibility with rounding.

For a usable repertoire, `sampling_probability = usage_percent / sum(usage_percent)`.
The original `usage_percent` and any `usage_fraction` column remain unchanged.
For example, Scherzer's 47.2% four-seam usage and 99.9% total become a sampling
probability of approximately 0.472472.

| Quality status | Meaning | Can sample? |
| --- | --- | --- |
| `ready` | Reported percentages total 100 | Yes |
| `normalized_rounding` | Sum differs from 100 within the rounding bound | Yes |
| `unavailable_no_repertoire` | Cohort player has no individual pitch rows | No |
| `unavailable_missing_usage` | At least one reported pitch has missing usage | No |
| `unavailable_zero_usage` | All reported usages are zero | No |
| `unavailable_incomplete_usage` | Total is outside the rounding bound, above or below 100 | No |

Unavailable groups retain null sampling probabilities. Missing usage is never
replaced with zero or an estimated share. Reported 0.0% rows stay visible with
zero sampling probability; a rounded zero is not proof the pitch never occurred.
No smoothing or inferred rare-pitch counts are added.

Malformed IDs, duplicate keys, unknown pitch labels/broad families, invalid
percentages, excess decimal precision, or inconsistent pitch totals raise an
error. A missing total pitch count is retained as missing and reported, but
does not prevent sampling otherwise usable percentages. Nonmissing totals
must be positive integers and agree across the pitcher's rows. No observed
per-type counts are reconstructed from rounded shares.

## Sampling and comparison

Selections use a local random generator, independently and with replacement.
Sorting by pitch code makes the result independent of input row order. A fixed
seed reproduces a sequence in the tested Python environment without modifying
global random state. Calling again with the same seed restarts the sequence;
use one larger request when you want one continuous reproducible sequence.

Zero requested pitches return an empty sample. The comparison then shows zero
counts and missing observed percentages. Otherwise, expected counts equal
`n_pitches * sampling_probability`, and differences are measured in percentage
points against the normalized probability. These expected counts describe the
simulation, not observed historical counts. Large-sample agreement checks that
the sampler works; it is not a measure of predictive accuracy.

## Command-line exports

Run from the repository root with the `.venv` interpreter active:

```bash
python -m mlb_at_bat.cli build-pitch-baseline
python -m mlb_at_bat.cli sample-pitches --player-id 453286 --season 2026 --n-pitches 10000 --seed 42 --output data/simulations/scherzer_2026
```

Both commands default to the versioned, cleaned arsenal and pitcher CSVs in
`datasets/2026/views/`. Override them with `--arsenal` and `--pitchers`; cleaned
CSV and Parquet inputs are supported. These commands make no network requests.

The build command writes to `data/processed/pitch_selection_2026/` by default:

- `pitch_selection_probabilities.csv` and `.parquet`.
- `pitcher_repertoires.csv` and `.parquet`.
- `quality_report.json`, including availability counts and input SHA-256 hashes.

The sample command prints the selected pitcher's frequency comparison. Optional
`--output` writes `sampled_pitches.csv`, `frequency_comparison.csv`, and
`simulation.json` containing the seed, requested sample size, and input hashes.
Omit `--output` to inspect results without writing files. Generated working
outputs under `data/` are ignored by Git.

## Next ML stage

Pitch count, batter handedness, and previous pitches currently do not affect
the distribution. The same probabilities apply before each selection.
Pitch-result prediction, count updates, balls in play, and completed plate
appearances belong to later components.

Once game logs arrive, compare a contextual pitch classifier against this
baseline on chronological holdout data. Repertoire probabilities can become
features and fallback estimates, but must be calculated from information
available before the target pitch. The current season exports have no verified
as-of date and cannot be treated as pre-pitch features for earlier events in
that season. No historical predictive performance is claimed in this sprint.
