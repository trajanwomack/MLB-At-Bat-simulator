# %% Getting started
"""Explore the cleaned 2026 player summaries in VS Code.

Select the project's .venv interpreter, then use Run Cell above each # %%
section (or Shift+Enter). Dataframes are available in the Interactive Window's
Variables/Data Viewer. Run cells in order, then edit the exploration cells.

First-time setup, from the repository root:
    python scripts/setup_workspace.py

These are season profiles, not pitch-by-pitch records or labeled matchups.
All available cleaned columns are retained; model features are not selected.
No files are changed and no model is trained by this script.
"""

from pathlib import Path

import pandas as pd

from mlb_at_bat.pitch_selection import build_pitch_baseline, compare_pitch_frequencies
from mlb_at_bat.hitter_outcomes import build_hitter_baseline, compare_outcome_frequencies

# __file__ is available when running the script; the working directory also
# supports running individual cells in the VS Code Interactive Window.
START = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ROOT = next(
    (folder for folder in (START, *START.parents)
     if (folder / "mlb-at-bat.code-workspace").is_file()),
    None,
)
if ROOT is None:
    raise FileNotFoundError("Open the MLB-At-Bat-simulator workspace first.")

PITCHER_DATA = ROOT / "data/processed/pitcher_exports_2026_10ip"
HITTER_DATA = ROOT / "data/processed/hitter_exports_2026"


def load_cleaned(folder, name):
    """Read generated Parquet so nullable numeric/boolean types are preserved."""
    path = folder / f"{name}.parquet"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing cleaned table: {path}\n"
            "From the repository root, run: python scripts/setup_workspace.py"
        )
    return pd.read_parquet(path)


pd.set_option("display.max_columns", 12)
pd.set_option("display.width", 140)

# %% Pitchers: one row per player_id + season; all profile columns retained
df_pitchers = load_cleaned(PITCHER_DATA, "pitcher_master")

# One row per pitcher + season + individual pitch type. Usage is a rounded
# percentage, not a pitch count. Keep this separate from the player master.
df_pitcher_arsenal = load_cleaned(PITCHER_DATA, "pitch_arsenal_types")

# Smaller subject tables are useful when exploring one category at a time.
# Example: pitcher_tables["bat_tracking"].head()
pitcher_tables = {
    name: load_cleaned(PITCHER_DATA, name)
    for name in ("bat_tracking", "contact", "location", "arsenal")
}

# %% Reusable pitch-selection baseline: lookup tables, no random sampling yet
pitch_baseline = build_pitch_baseline(df_pitcher_arsenal, pitchers=df_pitchers)
df_pitch_probabilities = pitch_baseline.probabilities
df_pitch_repertoires = pitch_baseline.repertoires
# Source usage_percent is unchanged. sampling_probability sums to one for each
# available pitcher-season. Unavailable repertoires retain null probabilities.

# %% Hitters: regular stats through Statcast and bat tracking in one dataframe
df_hitters = load_cleaned(HITTER_DATA, "hitter_master")

# Optional comparison subset; the full table still retains low-PA hitters.
# PA means plate appearances, distinct from official at-bats (ab).
MIN_PA = 100
df_hitters_min_pa = df_hitters.loc[df_hitters["pa"].ge(MIN_PA)].copy()

hitter_tables = {
    name: load_cleaned(HITTER_DATA, name)
    for name in ("standard", "statcast", "contact", "location", "bat_tracking")
}

# %% Hitter outcome baseline: counts divided by PA, with all hitters retained
hitter_baseline = build_hitter_baseline(df_hitters, min_pa=MIN_PA)
df_hitter_outcome_probabilities = hitter_baseline.probabilities
df_hitter_outcome_profiles = hitter_baseline.profiles
# Other PA outcome is a remainder, not necessarily an out. Walks already include
# intentional walks. Missing/inconsistent counts produce unavailable profiles.

# %% Catcher, running, and fielding measurements remain separate
# Each export includes rows with no measurements. Retain those rows, using
# has_metrics to distinguish measured coverage from mere file membership.
df_catchers = load_cleaned(HITTER_DATA, "catcher_poptime")
df_running = load_cleaned(HITTER_DATA, "running")
df_catch_probability = load_cleaned(HITTER_DATA, "catch_probability")
df_fielding_jump = load_cleaned(HITTER_DATA, "jump")

# Measured subset, not a complete catcher roster or a sample-size guarantee.
df_catchers_measured = df_catchers.loc[df_catchers["has_metrics"]].copy()

# The running export has bolts and home-to-first time, not sprint speed in ft/s.
# Missing measurements stay missing; signed metrics retain negative values.

# %% Table overview and column reference
analysis_tables = {
    "pitchers": df_pitchers,
    "pitcher_arsenal": df_pitcher_arsenal,
    "pitch_probabilities": df_pitch_probabilities,
    "pitch_repertoires": df_pitch_repertoires,
    "hitters": df_hitters,
    "hitters_min_pa": df_hitters_min_pa,
    "hitter_outcome_probabilities": df_hitter_outcome_probabilities,
    "hitter_outcome_profiles": df_hitter_outcome_profiles,
    "catchers": df_catchers,
    "running": df_running,
    "catch_probability": df_catch_probability,
    "fielding_jump": df_fielding_jump,
}

df_table_summary = pd.DataFrame([
    {
        "table": name,
        "rows": len(frame),
        "columns": len(frame.columns),
        "rows_with_measurements": (
            int(frame["has_metrics"].sum()) if "has_metrics" in frame else pd.NA
        ),
    }
    for name, frame in analysis_tables.items()
])

df_column_summary = pd.DataFrame([
    {
        "table": name,
        "column": column,
        "dtype": str(frame[column].dtype),
        "missing_count": int(frame[column].isna().sum()),
        "missing_percent": round(100 * frame[column].isna().mean(), 2),
    }
    for name, frame in analysis_tables.items()
    for column in frame.columns
])

# Dictionaries explain column names and how source names map to cleaned names.
df_pitcher_dictionary = pd.read_csv(PITCHER_DATA / "data_dictionary.csv")
df_hitter_dictionary = pd.read_csv(HITTER_DATA / "data_dictionary.csv")
print(df_table_summary.to_string(index=False))

# %% Start your hitter exploration here
df_hitters.head()

# %% Inspect data types and non-null counts, like the Adult dataset workflow
df_hitters.info()

# %% Numerical summary; IDs, season, and counts are not automatically features
df_hitters.describe()

# %% Explore pitcher profiles
df_pitchers.head()

# %% Explore the measured catcher subset
df_catchers_measured.head()

# %% Choose a pitcher and inspect the baseline probabilities
# Change the ID to any player_id in df_pitchers. Names are labels, not join keys.
PITCHER_ID = 453286  # Max Scherzer; try 445276 for Kenley Jansen
SEASON = 2026
df_selected_repertoire = pitch_baseline.repertoire(PITCHER_ID, SEASON)
df_selected_repertoire[[
    "player_name", "pitch_type", "usage_percent", "sampling_probability",
    "arsenal_pitch_total", "quality_status",
]]

# %% Sample pitch selections, then compare the mix with the baseline
# These are independent PITCHES, not completed at-bats or observed game events.
N_PITCHES = 10000
RANDOM_SEED = 42
df_sampled_pitches = pitch_baseline.sample_pitches(
    PITCHER_ID, SEASON, n_pitches=N_PITCHES, seed=RANDOM_SEED
)
df_pitch_frequency_comparison = compare_pitch_frequencies(
    df_selected_repertoire, df_sampled_pitches
)
df_pitch_frequency_comparison[[
    "pitch_type", "usage_percent", "sampling_probability", "sampled_count",
    "sampled_percent", "difference_percentage_points",
]]

# %% Inspect quality and sample sizes across all repertoires
df_pitch_repertoires.groupby("quality_status").size()

# %% Choose a hitter and inspect their observed plate-appearance distribution
HITTER_ID = 592450  # Aaron Judge; try 660271 for Shohei Ohtani
HITTER_SEASON = 2026
df_selected_hitter_distribution = hitter_baseline.distribution(HITTER_ID, HITTER_SEASON)
df_selected_hitter_distribution[[
    "player_name", "outcome", "pa", "outcome_count", "probability", "meets_min_pa",
]]

# %% Simulate 100 independent plate appearances from that hitter's season rates
# There is no pitcher adjustment or connection to the pitch sampler yet.
N_PA = 100
HITTER_RANDOM_SEED = 42
df_sampled_plate_appearances = hitter_baseline.sample_plate_appearances(
    HITTER_ID, HITTER_SEASON, n_pa=N_PA, seed=HITTER_RANDOM_SEED
)
df_hitter_outcome_comparison = compare_outcome_frequencies(
    df_selected_hitter_distribution, df_sampled_plate_appearances
)
df_hitter_outcome_comparison[[
    "outcome", "probability", "expected_count", "sampled_count", "sampled_percent",
    "difference_percentage_points",
]]

# %% Review baseline coverage and sample-size flags
df_hitter_outcome_profiles.groupby(["quality_status", "meets_min_pa"]).size()

# %% Game-log tables: a week of the season, or the small versioned review sample
GAME_TABLE_DATA = ROOT / 'data/processed/game_logs_2026'
GAME_START = '2026-06-01'
GAME_END = '2026-06-07'
if (GAME_TABLE_DATA / 'quality_report.json').is_file():
    game_filters = [
        ('game_date', '>=', pd.Timestamp(GAME_START)),
        ('game_date', '<=', pd.Timestamp(GAME_END)),
    ]
    df_pitch_events = pd.read_parquet(GAME_TABLE_DATA / 'pitch_events.parquet', filters=game_filters)
    df_plate_appearances = pd.read_parquet(GAME_TABLE_DATA / 'plate_appearances.parquet', filters=game_filters)
    df_game_dictionary = pd.read_csv(GAME_TABLE_DATA / 'data_dictionary.csv')
    game_table_scope = f'{GAME_START} through {GAME_END}'
else:
    game_examples = ROOT / 'datasets/2026/game_table_examples'
    df_pitch_events = pd.read_csv(game_examples / 'pitch_events.csv', parse_dates=['game_date'])
    df_plate_appearances = pd.read_csv(game_examples / 'plate_appearances.csv', parse_dates=['game_date'])
    df_game_dictionary = pd.read_csv(game_examples / 'data_dictionary.csv')
    game_table_scope = 'small versioned review sample; install the full tables to use the date filters'
print(f'Game tables: {game_table_scope}; {len(df_pitch_events):,} events, {len(df_plate_appearances):,} completed PA')

# %% Pitch events retain count, pitch type, pitch result, and source provenance
df_pitch_events[[
    'game_date', 'game_pk', 'at_bat_number', 'pitch_number', 'batter', 'pitcher',
    'balls', 'strikes', 'pitch_type', 'description', 'pitch_type_eligible',
]].head(20)

# %% One completed PA per row; matchup and count flags are explicit
df_plate_appearances[[
    'game_date', 'game_pk', 'at_bat_number', 'batter', 'pitcher', 'ending_pitcher',
    'terminal_event', 'pa_outcome', 'outcome_eligible', 'exclusion_reason',
]].head(20)

# %% Audit first, then choose eligibility filters before any future model work
df_plate_appearances.groupby(['pa_outcome', 'outcome_eligible']).size()
# Same-pitch velocity/location/results and future-game fields are not inputs
# for selecting that pitch. The dictionary documents field availability.
# Full-season player profiles are not joined to these historical event rows.

# %% Your analysis
# Example expressions to uncomment, run, and modify:
# df_hitters_min_pa[["player_name", "pa", "k_percent", "bb_percent"]].head(20)
# df_column_summary.query("table == 'hitters'").sort_values("missing_percent", ascending=False)
# df_pitcher_arsenal.groupby("pitch_type")["usage_percent"].describe()
# df_catchers_measured.describe()
# hitter_tables["bat_tracking"].describe()
# df_pitchers["has_source_total_difference"].value_counts(dropna=False)
#
# Keep source scales (e.g. percent versus proportion) and sample sizes in mind.
# See reports/ for coverage and quality notes. Full-season summaries cannot be
# used as pre-event features for earlier events in that season without leakage.
