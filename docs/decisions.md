# Design decisions

Last updated: 2026-10-10. Each entry says whether it is implemented in the reviewed code, still waiting on evidence, or open. PRD references are taken from code comments; the PRD itself was not available.

## Index

| ID | Decision | Status |
| --- | --- | --- |
| D1 | The model only suggests; the person decides | Implemented |
| D2 | General category versus specific name | Implemented in prompt; policy waits on evaluation |
| D3 | Confidence wording, no percentages | Implemented |
| D4 | Evaluation taxonomy | Implemented in scripts; definitions incomplete |
| D5 | Frozen prompt before scoring | Implemented in harness |
| D6 | Field test kept separate from the original evaluation | Implemented in harness |
| D7 | Safety is added by the app, not trusted to the model | Implemented |
| D8 | Privacy: local only, no EXIF, no GPS | Implemented |
| D9 | One SQLite file, full schema up front | Implemented |
| D10 | No completeness or progress pressure | Implemented |
| D11 | Model call defaults | Implemented; not tuned |
| D12 | License | **Open** |

---

## D1. The model only suggests; the person decides

Every identification is a suggestion. Photos move `imported -> identified -> confirmed / rejected / saved_unidentified`. A Low or malformed result cannot be confirmed. The person can correct it with their own words (which counts as confirmed by the person), reject it, or keep the photo unidentified. A confirmed photo is not re-identified.

Why: the model can be confidently wrong, and the app should not present a guess as a fact.

## D2. General category versus specific name

The prompt asks for the most specific name the model can honestly give, and says to give a species only if fairly sure, otherwise a group (for example "a swallowtail butterfly" or "a grass"), or null. It also says "A correct group name is better than a wrong species." Separately, the model always returns one of 14 categories.

Where this shows up:

- Ground truth is at group level (`truth_level = visual_group`), so the evaluation currently measures group-level correctness, not species accuracy.
- Collection counts and "firsts" (one per category) work on category, which is the level the app can rely on most.
- `score.py` includes a proposed Group-level gate and, if Usefulness fails, says to lean on group-level wording and Firsts and label species identification experimental.

Status: the prompt and data model are implemented. Whether species-level names ship as normal output, or are labelled experimental per category, depends on the evaluation, which has not produced results yet.

Consequence: a result such as "a bird" can be Exact against group-level ground truth without showing the model can name species. Do not report it as species accuracy.

## D3. Confidence wording

Wording follows confidence (PRD 10.5) and never includes a percentage:

| Confidence | Text |
| --- | --- |
| High | "Looks like a Blue Jay." |
| Medium | "Might be a Blue Jay." |
| Low, unknown, malformed, or no identification | "Not sure what this is." |

Supporting rules in code:

- The prompt reserves High for clearly visible key features with few look-alikes. Blurry, distant, partial, backlit or look-alike subjects should be Medium or Low.
- A result with no identification is forced to Low, because "confident but nothing identified" is contradictory.
- Malformed output is stored as Low with category `unknown`, never dropped and never confident.
- Only High or Medium suggestions can be confirmed.
- Tests assert the exact strings and the absence of `%`.

Why: three coarse words are easier to be honest about than a number the model cannot calibrate. The honesty gate (D4) checks whether the words are earned.

Not yet known: whether High is actually earned. That is what the Honesty gate measures, and no results exist.

## D4. Evaluation taxonomy

- **Correctness:** Exact, Close, Wrong, graded by hand. `auto_match` can suggest Exact from name overlap but never Wrong, and is ignored unless `--use-auto` is passed.
- **Explanation:** Useful, OK, Not useful, plus a `factual_flag` for any false or unverifiable claim.
- **Outcome buckets** for graded rows: confident hit, **confident miss** (High and Wrong), hedged miss, over-cautious.
- **Splits:** `scored`, `tune`, `probe`.
- **Conditions:** `original` and `compressed`.
- **Truth quality:** `truth_level` and `how_verified` recorded per row.
- **Reporting:** counts as "n of N", and a gate with nothing to evaluate shows `n/a`, never PASS.
- **Bar fixed in advance:** thresholds are constants in `score.py`, to be changed only before the first scored run.

Open: the written definition of Close, and of Exact for group-level truth, was not available. Gates also depend on a `clear` difficulty label that the ground-truth file does not use (see evaluation.md, Known issues).

## D5. Frozen prompt before scoring

Scored and probe runs must use `eval/prompts/frozen.txt`. The prompt hash is stored in every row, a results file with a different hash is refused, and `score.py` warns if scored rows mix prompts. Changing the prompt means rerunning everything. Tuning uses the `tune` split only.

Why: otherwise a score cannot be tied to one prompt, and tuning on scored photos inflates results.

Limit: only one photo (`p02`) is in the tune split.

## D6. Field test kept separate

A custom ground-truth file must use its own results folder. The harness refuses otherwise, and tests check that the original `eval/results/` is unchanged. Field results are observations on nine photos, not gate inputs. Details: [field-test.md](field-test.md).

Also decided: ground-truth CSVs are read as UTF-8 with BOM tolerated and header whitespace stripped; a wrong delimiter or missing `photo_id` or `split` column gives an error naming the columns found.

## D7. Safety is added by the app, not trusted to the model

- The prompt forbids safe-to-eat, safe-to-touch, edible, harmless and non-poisonous claims, and any suggestion to touch or approach.
- The app adds warnings (FR-07) for fungi, reptile, unknown, non-`ok` results, and Low-confidence living things, whatever the model wrote.
- Generated fact and explanation text is scanned; unsafe reassurance is removed and flagged. Warnings such as "not safe to eat" are kept.
- Quests and activities are checked against banned phrases with no exceptions.
- The person's private journal is never scanned. That is a deliberate non-goal.

Known weaknesses: the phrase list is only as good as its entries (its contents were not reviewed); the scan is phrase-based, so a rewording can slip through; and the safety rule exists twice (`identify.needs_safety_warning` and `safety.needs_warning`), with the evaluation using the placeholder. Merge them.

## D8. Privacy

Everything stays on the person's machine. Stored photos are resized and re-encoded without EXIF. Walk area is typed, never GPS. Photo hashes are for duplicate detection only, not authenticity. Identification talks to local Ollama.

Limits: the evaluation scripts read original photos, which may contain GPS. A hash cannot detect a re-compressed copy of the same photo.

## D9. One SQLite file, full schema up front

The whole PRD section 12 data model is created at startup so journal, quests and firsts modules need no migration. `PRAGMA user_version` is set to 1, but there is no migration code yet. If the schema changes, a migration path will be needed.

## D10. No completeness or progress pressure

A walk is never incomplete. The collection is a plain record of confirmed discoveries by category with no percentage, target, rank or progress. Quests that were not done are not stored. Tests guard these properties.

## D11. Model call defaults

Thinking off, longest side 768 px, `num_predict` 400, `keep_alive` 10 minutes, JSON format, sampling at the model's defaults. The code comments call "thinking off" a starting point from the model page's tips. These are untuned starting values. The default model `gemma4:e2b` is provisional until the evaluation picks one.

## D12. License (open)

Not chosen. Facts to settle first:

1. Who is the copyright holder, and is there an employer, school or programme that claims rights or imposes terms?
2. Does the PRD, or a competition or course the project is submitted to, require a specific license?
3. Do you want an express patent grant and contribution terms (Apache-2.0), or the shortest permissive text (MIT)?
4. Will you ever publish the evaluation or field photos? They are your own files and would need their own terms. They are not in the repository.
5. Gemma model weights and Ollama are separate software with their own terms. Their licenses should be read before redistributing anything that bundles them. This repository does not bundle them.

Dependencies reviewed so far are Pillow and pytest, both permissively licensed, so they do not by themselves force a choice. My lean is MIT for a simple, permissive project, or Apache-2.0 if you want the patent grant. This is not legal advice; confirm against any rules from items 1 and 2.

---

## Known limitations

Collected from the code and data reviewed.

### Evaluation data

- Ground truth is visual-only and group-level, and many rows are flagged as needing verification.
- 26 scored photos, 1 tune photo, 0 probes. One photo is about 4 points.
- Field test: 9 photos, user-labelled, one photo about 11 points.
- IDs `p10`, `p11`, `p21`, `p22` are absent, and `score.py` mentions 32 photos against 27 rows listed.
- Whether compressed copies exist for all scored photos is unconfirmed.

### Scoring and harness

- `clear` versus `easy`/`medium` mismatch makes Usefulness and Group level `n/a`.
- No probe rows, so the Probes gate is `n/a`.
- Resume skips errored rows.
- The field row `f03` uses a non-model category (`leaves`).
- Gate thresholds were copied from `score.py` and not checked against the PRD.

### Runtime

- A Windows `llama-server` crash (exit status `0xc0000409`) occurred on the first run, undiagnosed.
- `think` retry logic depends on the server's error text mentioning `think`.
- The app's stored copy is resized twice before the model sees it; the evaluation resizes once. Results may differ slightly.

### Product

- Duplicate detection is exact-bytes only.
- No UI, no journal, quest or firsts logic was reviewed.
- Phrase-based safety scanning can miss rewordings.
- No schema migration path.
- Real-world effect of the wording and confirmation steps on people has not been tested.