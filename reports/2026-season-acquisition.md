# 2026 regular-season pitch acquisition

The complete requested calendar range was acquired from Baseball Savant:
January 1 through November 3, 2026, regular-season games only. Returned game
dates run from March 25 through September 27. This sprint acquired and audited
the records; it did not clean, impute, engineer features, or train models.

| Audit | Result |
| --- | ---: |
| Calendar dates successfully queried | 307 |
| Dates with records / empty dates | 184 / 123 |
| Pitch/event records | 716,792 |
| Played games represented | 2,429 |
| Columns | 119 |
| Duplicate game/PA/pitch keys | 0 |
| Maximum records in one daily request | 5,052 |
| Automatic ball/strike records, retained | 2,309 |
| Records without a pitch type, retained | 2,676 |
| Raw daily CSV bytes | 492,023,663 |
| Combined CSV bytes | 447,729,036 |
| Download ZIP bytes | 177,211,422 |

## Coverage reconciliation

Twenty-eight teams have 162 games; Baltimore and the Yankees have 161 each.
The missing scheduled game was **Baltimore at New York, September 27, game
823490**, cancelled for rain. This was confirmed in
[Savant's schedule response](https://baseballsavant.mlb.com/schedule?date=2026-09-27).
That response is preserved inside the ZIP under `coverage_evidence/`, with its
SHA-256 and the cancellation details in the manifest and summary.

Thus the 2,429 played-game total and every team's game count reconcile after
the confirmed cancellation. A full-season independent game-ID-by-game-ID
schedule comparison was not performed: direct MLB and ESPN schedule endpoints
were blocked by the environment's proxy. No missing source game was inferred
or fabricated to reach 2,430.

## Integrity and preservation

All daily source hashes, record counts, returned dates, regular-season types,
CSV field widths, and common schema were checked. A disk-backed SQLite index
confirmed pitch-key uniqueness across the season. The completed ZIP passed a
CRC integrity check of every member. Original daily CSV bytes are unchanged;
the combined CSV preserves field values while using consistent CSV serialization.

The archive contains one combined CSV, the aggregate source manifest, 307
daily download manifests, and the schedule evidence. The archive SHA-256 is:

```text
dec1b9ccdd3d3e59b173d6d4062e7a0a32802a27438cbc17e85c17084103a01b
```

See [the machine-readable acquisition summary](2026-season-acquisition.json)
for dates, checksums, game counts, and provenance. The ZIP is named
`statcast_2026_regular_season.zip` and was supplied separately as a download.
It is intentionally outside Git; a clone contains the collector and reports,
not the large acquired data.

## Resource use and reproduction

Two workers downloaded one date per request and reused verified cached files.
The collector completed acquisition and its initial streaming export in about
10 minutes, well within its 30-minute/2-GiB raw-data budget. Final coverage
investigation and archive verification were performed afterward. No season-wide
dataframe or additional uncompressed combined copy was needed. Resource limits
were not exceeded and no volume-related abort was necessary.

```bash
python scripts/acquire_season.py --season 2026 --through 2026-11-03 --workers 2 --raw data/raw/savant_2026_regular --output data/acquisition/savant_2026_regular
```

This recreates the raw-data collection and archive. The cancellation check was
a separate audit captured with this export; the collector does not automatically
query and reconcile full-season schedules. Existing cached days are hash-checked
and reused. Later fresh downloads may reflect source corrections and produce
different hashes. See [the acquisition workflow](../docs/season-acquisition.md)
for resume rules, resource limits, and reading the CSV in chunks.
