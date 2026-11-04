# Full-season pitch-data acquisition

The collector requests Baseball Savant's regular-season pitch-detail CSVs, one
calendar date per request. It queries January 1 through an explicit cutoff, so
opening dates, postponed games, and off-days do not need to be guessed. Empty
CSV dates are archived as successful zero-row responses, not skipped errors.

With the project's installed `.venv` selected, run from the repository root:

```bash
python scripts/acquire_season.py --season 2026 --through 2026-11-03 --workers 2 --raw data/raw/savant_2026_regular --output data/acquisition/savant_2026_regular
```

The command makes at most two concurrent requests. Each request includes
`all=true`, `type=details`, unrestricted players, and `hfGT=R|`. Spring training
and postseason records are excluded. Dates are inclusive. The cutoff is part
of the recorded provenance, not an inferred statistical cutoff for the older
player-summary exports.

## Resume and resource control

Run the same command to verify and reuse completed days. Original CSV bytes
and their download manifests are immutable. Hashes, source URLs, game dates,
and game types are checked. Unmanifested CSVs or conflicting cached inputs
cause an error instead of an overwrite; inspect such files before retrying.

The collector retries temporary connection failures, HTTP 429, and server
errors up to three attempts. Backoff respects numeric Retry-After values up to
30 seconds. HTTP access denials stop the run. A report records an abort, and
previously archived days remain reusable. One already running request may
finish while the collector shuts down.

Default acquisition limits are 30 minutes, 2 GiB of raw data, and at least
2 GiB of free disk. Adjust the first two with `--max-seconds` and
`--max-raw-gib`; these are stop conditions, not target sizes. Progress is printed
as JSON and saved atomically in `acquisition_report.json`. The timeout applies
to acquisition; final streaming packaging is a separate phase.

## Outputs and audit

- `data/raw/savant_2026_regular/YYYY-MM-DD.csv`: untouched daily source exports.
- Matching `.manifest.json`: source URL, retrieval time, SHA-256, row count,
  returned columns, and expected date.
- `data/acquisition/savant_2026_regular/acquisition_report.json`: progress,
  completion/abort state, source manifests, coverage, and artifact checksum.
- `statcast_2026_regular_season.zip`: one combined CSV plus source and daily
  manifests. Read the CSV directly from the ZIP, or extract it in VS Code.
- `pitch_key_audit.sqlite`: a rebuildable disk-backed uniqueness index.

The collector streams one daily file at a time into the ZIP. It does not load
the full season into a dataframe or create another uncompressed season-sized
copy. The SQLite index checks `(game_pk, at_bat_number, pitch_number)` across
all dates. Schema changes, malformed rows, unexpected dates/game types, hash
mismatches, and duplicate keys prevent publishing a finished archive. The
original daily files remain available for investigation. A `.partial.zip`
is an unfinished artifact, not a complete export.

The combined CSV retains source values, including missing pitch types,
automatic ball/strike events, and missing tracking measurements. Files are
combined by date; within-day source order is retained, so chronological model
sequences will need explicit sorting later. There is no imputation, pitch
eligibility filtering, deduplication, profile join, or model training here.

## Coverage interpretation and preservation

The report lists every queried date, dates with records, unique games, and
game appearances by team. An all-dates download is not by itself independent
proof of coverage. Inspect team totals and source dates before declaring a
dataset complete for modeling. The collector does not automatically reconcile
the export against a full-season schedule.

The direct MLB schedule API was blocked in the acquisition environment, but
[Savant's daily schedule endpoint](https://baseballsavant.mlb.com/schedule?date=2026-09-27)
was accessible and returned schedule JSON with game IDs, team names, statuses,
and cancellation reasons. This resolved the 2026 export's one-game difference
from 2,430: Orioles at Yankees, game 823490, was cancelled for rain on September
27. A manually verified coverage check and the original schedule response are
included with the acquired season artifact. A full-season independent
game-ID-by-game-ID comparison was not performed.

Large raw exports, ZIPs, and SQLite files live under ignored `data/`; they do
not accompany a Git clone. Preserve the downloaded archive separately. The
collector, tests, documentation, and a small acquisition review can be versioned
without putting hundreds of megabytes of pitch data in Git history.

## Reading the export later

With pandas, the combined CSV can be read directly from the ZIP in chunks:

```python
from zipfile import ZipFile
import pandas as pd

with ZipFile("data/acquisition/savant_2026_regular/statcast_2026_regular_season.zip") as archive:
    with archive.open("statcast_2026_regular_season.csv") as stream:
        for chunk in pd.read_csv(stream, chunksize=50000, low_memory=False):
            # Inspect or process this chunk, then release it before reading more.
            print(chunk.shape)
```

Use the stream for explicit inspection/processing; no training is performed by
the acquisition command or by this example.
