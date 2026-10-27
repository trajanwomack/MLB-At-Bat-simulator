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
    "hitters": df_hitters,
    "hitters_min_pa": df_hitters_min_pa,
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
