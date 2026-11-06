# 2026 game-log tables v1.0.0

The original, unchanged `game_logs_2026_tables.zip` built at commit
`04ca878d326218a1687ea7e22b3c43ddc7f65a7f` contains:

- 716,792 pitch/event records, 130 columns.
- 183,849 completed plate appearances, 48 columns.
- Rejected-row and PA-exclusion audits, column dictionary, quality report, and README.

Archive size: **108,916,547 bytes**. Archive SHA-256:

```text
99d0f6164c350f0fb52cd88d84e50d390647b5ef72196a7e3ad647bc7fe5bcc3
```

The archive and its included table hashes were verified against
`reports/game-tables-2026.json`. All seven archive members are preserved;
`game_logs_2026_tables.files.sha256` records every included file's checksum.
No tables were regenerated or modified for this release.

Download the ZIP and `game-tables-2026.json`. From a checkout of the repository,
follow the verification and installation instructions in the main-branch README.
Extract to a new `data/processed/game_logs_2026/` directory. The analysis file
then loads June 1–7 by default. Existing output directories should be preserved
or moved aside before installation rather than overwritten.

This release is a data snapshot. Eligibility and source quality flags remain
part of the tables; it is not a fitted model or an automatically selected
training feature matrix.
