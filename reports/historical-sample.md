# First historical sample audit

Source: [pybaseball's historical Savant fixture](https://github.com/jldbc/pybaseball/blob/e588989359059ba04a510cc12aefa972f0a715d6/tests/pybaseball/data/small_request_raw.csv),
game date **2020-09-03**. This is a development fixture, not a new Savant query
or evidence that all games from that day were captured.

Raw SHA-256: `5bef7b0e41660c36b529577f79a6d943b89a8847ea45b79ffab5430fbeb426e1`.

| Measure | Result |
| --- | ---: |
| Raw and accepted pitch rows | 2,507 |
| Games represented | 8 |
| Recorded appearance groups | 636 |
| Groups with a recognized batting outcome | 634 |
| Groups eligible for conservative direct matchup counts | 633 |
| Unresolved groups | 2 |
| Completed group excluded for a participant change | 1 |
| Rejected pitch rows | 0 |
| Exact duplicate records | 0 |
| Conflicting pitch keys | 0 |
| Missing `events` values | 1,872 |
| Missing launch speed / launch angle values | 1,722 each |
| Missing pitch classification / release speed values | 0 |

The 89 original columns contain 87 unique names. Repeated `pitcher` and
`fielder_2` columns agree and were coalesced. Literal `null` strings became
typed missing values; the expected structural blanks were retained.

The two unresolved groups are game `631112`, appearance `13` (a caught-stealing
event rather than a completed batting result), and game `631146`, appearance
`85` (no recorded terminal batting event). We have not checked the official
game feeds, so the reason for the latter remains unconfirmed.

Game `631112`, appearance `35` has a recorded force-out and a participant
change. Its pitch rows and outcome remain available, but it is excluded from
direct batter–pitcher sample counts until attribution can be verified.

Pitch classifications: FF 890; SL 446; SI 414; CU 243; CH 226; FC 180;
FS 75; KC 23; CS 10. This single historical date does not establish the
classification vocabulary or distribution for recent seasons.

The complete machine-readable report and Parquet tables are generated under
`data/processed/historical_sample/`. Re-run the README commands to reproduce
them. Recent live Savant downloads remain blocked by the environment proxy
with HTTP 403 even though the hostname is present in the saved allowlist.
