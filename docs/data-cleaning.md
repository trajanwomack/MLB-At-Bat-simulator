# Data cleaning contract, version 1

## Preserve evidence

Raw CSV downloads are immutable. Each download has a JSON manifest with the
original and resolved URL, retrieval timestamp, source type, row count, and
SHA-256. The historical fixture URL pins `pybaseball` commit
`e588989359059ba04a510cc12aefa972f0a715d6`.

An altered cached file or mismatched source fails validation. Imported files
without manifests remain explicitly labeled as user-supplied data. Every
accepted or rejected record retains `_source_file` and `_source_record`;
record numbers are logical nonempty CSV records after the header, not physical
text line numbers.

## Normalize without inventing information

- Trim surrounding whitespace and normalize empty strings and case-insensitive
  `null`, `none`, `nan`, `na`, and `n/a` to missing values.
- Inspect headers before pandas can rename them. Coalesce repeated columns
  only when populated values agree; conflicting repeated headers stop the run.
- Use nullable integers for identifiers and counts, nullable floats for known
  measurements, and parsed game dates. Unknown source columns remain strings.
- Report numeric parse failures. Required identity/count failures are
  quarantined; malformed optional measurements become missing, with aggregate
  issue counts and the preserved raw record available for investigation.
- Normalize pitch codes and handedness to uppercase; result codes to lowercase.
- Preserve unknown pitch/event codes and deprecated columns. Do not infer a
  pitch type, fill a missing outcome, or impute contact measurements.

The historical fixture has duplicate `pitcher` and `fielder_2` headers. This
does not establish that the current Savant export has the same schema.

## Account for every input record

Candidate pitch key: `(game_pk, at_bat_number, pitch_number)`.

Exact duplicate records, excluding provenance, are removed. Differing records
with the same parsed pitch key are all quarantined rather than choosing one.
Missing/invalid positive IDs, invalid game dates, and counts outside 0–3 balls
or 0–2 strikes are quarantined. Present numeric innings must be positive;
present numeric outs must be 0–2. Missing optional context remains missing.

The report verifies:

```text
input rows = accepted pitch rows + rejected rows + exact duplicates removed
```

There is no arbitrary velocity or location cutoff. A pitch outside the strike
zone is valid evidence, and unusual physical values need investigation rather
than automatic deletion. Sequence order is game date, game ID, appearance
number, then pitch number.

## Separate pitch events from batting outcomes

Missing `events` on a nonterminal pitch is normal. Missing launch measurements
on a ball or swinging strike is also normal; some fouls do have measurements.

Group records by `(game_pk, at_bat_number)`. A group is marked completed only
when exactly one recognized batting-terminal event occurs on its final row.
The raw event remains available alongside its broader outcome category.

The mapping preserves singles, doubles, triples, home runs, strikeouts, walks,
hit-by-pitches, reached-on-error, catcher interference, and fielder's choices.
`force_out` is categorized with fielder's choices because it need not mean the
batter was retired. Sacrifice and other mapped batted outs are grouped as
`other_out`; the original event permits finer distinctions later.

Caught-stealing events do not resolve the batter's appearance. Unknown codes,
groups without a recognized terminal event, and ambiguous terminal sequences
remain unresolved and are excluded from batting outcome labels. A group with
all rows quarantined is absent from the appearance table and remains visible
in rejected-row accounting.

`sequence_complete` checks that recorded pitch numbers start at one and are
consecutive. It does not prove every play or automatic award is represented.
`outcome_eligible` additionally requires a completed label, stable batter and
pitcher IDs, and no rejected rows from the group. Substitutions are retained
but excluded from direct matchup counts until official attribution is checked.

`pitch_type_eligible` means an accepted row has a nonmissing classification
other than `UN`/`UNKNOWN` and is not an automatic ball/strike. It is a candidate
filter, not a guarantee of tracking accuracy, complete coverage, or a finalized
model label vocabulary. Automatic events remain in the pitch table so count
transitions are not silently lost.

## Prevent future leakage

For a pitch-type model, the observed `pitch_type` is the target. Start with
before-pitch count, handedness, participants, inning, outs, runner state, and
score. Any repertoire or matchup statistics must use only prior appearances.

Current-pitch velocity, spin, movement, location, description, `events`, contact
measurements, expected batting metrics, and post-pitch scores are not available
before that pitch. Prior-pitch values may be used only after an explicit shift
within the appropriate sequence. Unknown fields require review before becoming
features; column availability alone is not evidence that a feature is safe.

Future sample sizes must distinguish completed historical plate appearances,
official at-bats, pitches, and simulation repetitions. A distinct group is not
automatically a completed plate appearance. Aggregating all seasons before a
chronological train/test split would leak future information.

## Remaining validation before training

1. Obtain recent live Savant data and inspect its actual schema and event codes.
2. Reconcile game and completed-appearance counts with the MLB game feed. Raw
   pitch exports can omit non-pitch events, including some intentional walks.
3. Review automatic awards, classification changes, count transitions, and
   substitutions; resolve or explicitly exclude affected examples.
4. Build an explicit before-pitch feature table and dated matchup aggregates.
5. Split by date and compare pitch probabilities against a count/handedness
   frequency baseline. No model has been trained by this cleaning workflow.
