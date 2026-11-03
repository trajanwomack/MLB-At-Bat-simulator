# Reference data for the VS Code workspace

`2026/pitchers/` and `2026/hitters/` contain the 14 canonical user-supplied CSV
inputs and their original provenance manifests. These small reference inputs
are deliberately versioned so a clone contains the data. Their checksums match
the archived cloud inputs. Do not manually edit these CSVs; use a new version
directory for a changed export. Manifests' archive times are not statistical
cutoff dates, and original filenames do not establish verified filter settings.

`2026/views/` contains read-only review snapshots: the pitcher master, full
hitter master, hitter view with at least 100 PA, and individual pitch-type
arsenal table. These are generated from the reference inputs, not extra
independent datasets or training labels. Their CSV columns retain source scales
and documented missingness. They have no new preprocessing or model fitting.

`2026/baselines/` contains derived pitch-selection review snapshots:
`pitch_selection_probabilities.csv`, `pitcher_repertoires.csv`, and
`quality_report.json`. The probability table retains original usage alongside
normalized sampling probabilities; the repertoire table reports coverage and
quality. The report includes hashes of the cleaned inputs. These are statistical
baselines, not observed pitch records or fitted ML outputs. See
`docs/pitch-selection.md` for regeneration and missing-data rules.

`SHA256SUMS.json` verifies the included input, manifest, and snapshot bytes.
Run `python scripts/setup_workspace.py` from the repository root to install
dependencies, verify archives and snapshots, regenerate cleaned tables and
pitch-selection probabilities under ignored `data/`,
and run the regression suite. The reference snapshots remain unchanged.

See the audit reports under `reports/` for scope, sample sizes, unresolved
source-total differences, and missing specialist metrics. The historical
2020 test fixture is not a 2026 training dataset; its optional download remains
separate from these references.
