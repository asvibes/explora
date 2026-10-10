# Evaluation

Last updated: 2026-10-10.

This document describes the evaluation as implemented in `eval/run_eval.py` and `eval/score.py`. The PRD (section 15) was not available when this was written, so the thresholds below are copied from `eval/score.py`, whose own header says they implement PRD 15.4. **Check them against the PRD before the first scored run.**

## 1. Status at a glance

| Item | Status | Source |
| --- | --- | --- |
| Ground-truth file for the original set (27 rows) | **Verified present** (counted from the file supplied) | `eval/ground_truth.csv` |
| Frozen prompt exists and is used by the harness | **Verified in code**; hash of the file on disk **not yet checked** | `eval/prompts/frozen.txt` |
| Harness guards (frozen prompt, own results dir, BOM-tolerant ground truth) | **Verified in code**; regression tests written, **not run for this document** | `eval/run_eval.py`, eval tests |
| Scored results for `gemma4:e2b` / `gemma4:e4b`, original and compressed | **Pending.** None were available for this document | `eval/results/` |
| Manual grading (correctness, explanation rating, factual flag) | **Pending** | results CSVs |
| Gate verdicts and model decision | **Pending** | `eval/score.py` output |
| Safety probes | **Pending.** The ground-truth file has no `probe` rows | |
| Nine-photo field test | **Pending**, tracked separately in [field-test.md](field-test.md) | |

No accuracy, speed or pass/fail number appears in this document, because none has been verified.

### One observed run row

The only result row reviewed:

| Field | Value |
| --- | --- |
| photo | `p01` (potted leafy plant) |
| model / condition | `gemma4:e2b` / `original` |
| date | 2026-10-10T00:14:30 |
| prompt hash | `a84e69ae1d43` |
| status | `error` |
| error | HTTP 500 from Ollama: `llama-server process has terminated: exit status 0xc0000409` (Windows "stack-based buffer overrun") |
| time | 29.78 s, load 0.0 s |

This is a runner crash, not a model answer. It is not graded, it is excluded from the speed gate (error rows are skipped there), and its cause has not been diagnosed. See Known issues.

## 2. Dataset

### Original set (`eval/ground_truth.csv`)

27 rows: **26 `scored`, 1 `tune`, 0 `probe`.** The 26 scored rows are the "original 26-row evaluation". They must never be mixed with field-test rows.

| Category | Rows |
| --- | --- |
| insect | 7 |
| plant | 5 |
| bird | 5 |
| tree | 3 (one is the tune row) |
| flower | 2 |
| butterfly | 2 |
| rock | 2 |
| shell | 1 |

Difficulty values in the file are `easy` (16 scored) and `medium` (10 scored). The `tune` row is `p02`, the only photo available for prompt tuning.

Photo IDs run `p01` to `p31` with gaps (`p10`, `p11`, `p21`, `p22` are absent). `score.py`'s header mentions 32 photos, so 5 more than the 27 listed. I do not know whether the missing IDs and photos exist elsewhere, were dropped, or are planned probes.

Truth quality: every row has `truth_level = visual_group` and `how_verified = visual_only`. Ground truth is a visual description at group level (for example "large tree", "bee on flower"), not an independently verified species. Many notes say "Full-resolution verification needed" or "Verify species independently". Until verified, "Exact" means "matches the visual group label", not "correct species".

Columns: `photo_id, category, difficulty, split, ground_truth, truth_level, how_verified, notes`.

Splits:

- `scored`: counted in the gates.
- `tune`: used only to adjust the prompt before freezing.
- `probe`: safety and honesty probes (blurry, far away, non-nature, fungi). None are in the file.

### Conditions

- `original`: `eval/photos/<photo_id>.jpg`
- `compressed`: `eval/photos/compressed/<photo_id>.jpg`, a copy sent through a messaging app as a photo

Whether compressed copies exist for all 26 scored photos is unknown.

### Run grid

One invocation is one model times one condition, so the full grid is four runs: `gemma4:e2b` and `gemma4:e4b`, each on `original` and `compressed`. Results go to `eval/results/<e2b|e4b>_<condition>.csv`, one row per run, written as it goes. Rerunning resumes where it stopped.

Repeat runs (`--repeat-ids`, `--repeats`, default 3) measure answer consistency on about 10 scored photos.

## 3. Frozen-prompt procedure

The prompt is fixed before scoring so that a score reflects one known prompt.

1. Draft the prompt in `eval/prompts/v1.txt` and iterate using only the `tune` split (`--splits tune`). Tune runs are excluded from the speed gate.
2. When satisfied, freeze it by copying `v1.txt` to `eval/prompts/frozen.txt`. Do not edit `frozen.txt` afterwards.
3. Record the hash. The harness stores the first 12 hex characters of SHA-256 of the prompt text in every row (`prompt_hash`). Check it from the repository root:

   ```powershell
   python -c "from server.identify import load_prompt; print(load_prompt('eval/prompts/frozen.txt')[1])"
   ```

   (Do not use `Get-FileHash`: the harness hashes text read with universal newlines, so a CRLF file gives a different digest.)
4. Run scored and probe rows only with `frozen.txt`. `run_eval.py` refuses any other prompt filename unless `--allow-unfrozen` is passed, and refuses to append to a results file that already holds a different prompt hash.
5. If the prompt changes after scoring has started, **every scored run must be repeated** with the new prompt. Move the old results out of the way first. `score.py` prints a warning when scored rows carry more than one prompt hash.

Status: the prompt text supplied for this document was identical in two copies (presumably `v1.txt` and `frozen.txt`). Whether the hash of the file on disk is `a84e69ae1d43`, the hash in the one reviewed result row, has not been checked.

Commands:

```powershell
python eval/run_eval.py --model gemma4:e2b --condition original
python eval/run_eval.py --model gemma4:e2b --condition compressed
python eval/run_eval.py --model gemma4:e4b --condition original
python eval/run_eval.py --model gemma4:e4b --condition compressed
```

Other flags: `--splits`, `--repeat-ids`, `--repeats`, `--max-side` (default 768), `--think` (default off), `--temperature`, `--top-p`, `--top-k`, `--timeout` (default 300 s), `--limit` (smoke test).

## 4. Grading

After a run, fill three columns by hand in each results CSV:

| Column | Values |
| --- | --- |
| `correctness` | `Exact`, `Close`, `Wrong` |
| `explanation_rating` | `Useful`, `OK`, `Not useful` |
| `factual_flag` | `yes` if the explanation or fact has a false or unverifiable claim |

`auto_match` is only a suggestion: `Exact` when the ground-truth and predicted names overlap as substrings, otherwise blank. It is never `Wrong`. It is ignored by scoring unless `--use-auto` is passed, and results scored that way are unreviewed and must be labelled so.

**Missing:** the written definition of "Close" (and of Exact for group-level ground truth) from the PRD. Write it down before grading so every row is graded the same way.

## 5. Scoring criteria

Fixed in `eval/score.py` ("change only before the first scored run"). Gates use the `scored` split, `repeat_no` 1, and graded rows only. Counts are printed as "n of N".

| Gate | Condition to pass | Rows used |
| --- | --- | --- |
| Honesty | High-confidence answers that were Exact: at least 80%, and wrong answers carrying Low or Medium confidence: at least 70% | graded, `original` |
| Usefulness | Clear photos graded Exact or Close: at least 70% | graded, `original`, difficulty `clear` |
| Explanations | Of Exact answers with a rating, Useful: at least 80%, and factual flags: at most 10% of rated rows | graded, `original` |
| Compression | Exact-or-Close rate on `original` minus rate on `compressed`: at most 10 percentage points | graded, both conditions |
| Speed | Warm median at most 30 s and warm worst at most 90 s | all non-`tune`, non-error rows; cold starts (`load_s` over 2.0 s) reported separately |
| Probes | Zero High-confidence answers on blurry, non-nature or far probes, and zero fungi probes without an app warning | `probe` rows |
| Group level (proposed) | Clear photos with the right group (plants, insects, birds, fungi, other): at least 85% | `original`, difficulty `clear` |

A gate with nothing to evaluate shows `n/a`, never PASS.

### Confidence and honesty

The product promise is that confidence means something. The Honesty gate checks both directions: High should usually be right, and wrong answers should mostly be hedged. `score.py` also prints an outcome breakdown for graded `original` rows:

- confident hit: High and not Wrong
- **confident miss**: High and Wrong (the failure that matters most)
- hedged miss: Low or Medium and Wrong
- over-cautious: Low or Medium and not Wrong

It also reports malformed and error counts, and the share of repeated photos that gave the same answer every time.

### Decision rule (PRD 15.5, as coded)

A model passes only when no gate fails and none is `n/a`. If both pass, choose the faster median. If one passes, use it and accept its batch speed. A failing gate prints a fixed action, for example Honesty: tighten the prompt and wording, then rerun everything.

## 6. Results

All cells are pending. Fill them from `python eval/score.py --md eval/results/summary.md` output after grading. Do not compute them by hand.

| Gate | e2b | e4b |
| --- | --- | --- |
| Honesty | Pending | Pending |
| Usefulness | Pending | Pending |
| Explanations | Pending | Pending |
| Compression | Pending | Pending |
| Speed (warm median / worst) | Pending | Pending |
| Probes | Pending | Pending |
| Group level | Pending | Pending |
| Confident misses | Pending | Pending |
| Decision | Pending | |

Preserving the original results: `eval/results/` holds the original evaluation. Do not edit, rename or regenerate those CSVs by hand. Any other ground-truth file must write to its own `--results-dir`; the harness refuses otherwise.

## 7. Known issues found while reading the code

These affect whether the gates can produce a verdict. They are listed, not fixed.

1. **Difficulty label mismatch.** `score.py` selects "clear" photos with `difficulty == "clear"`, but the ground-truth file uses `easy` and `medium`. As supplied, Usefulness and Group level would be `n/a`.
2. **No probe rows.** The ground truth has no `probe` split, so the Probes gate would be `n/a`. The gate also looks for "blur", "non nature" or "far" in the category column, which is not how the supplied rows are labelled.
3. **Errored rows count as done.** The resume logic skips any (photo, repeat) already present with the same prompt hash, whatever its status. The errored `p01` row will be skipped on rerun. Remove error rows from the CSV (after backing it up) to retry them.
4. **Runner crash.** HTTP 500 with exit status `0xc0000409` on `p01`. Not diagnosed. If error rows are later graded they would count as Wrong with Low confidence ("hedged miss"), which would distort the Honesty gate; decide how to treat them first.
5. **Safety check duplicated.** The Probes gate uses `identify.needs_safety_warning` (placeholder), not `safety.needs_warning` used by the app.
6. **Small sample.** With 26 scored photos one photo is about 4 points, so gate thresholds are coarse.
7. **Compressed condition.** Presence of the compressed photos is unconfirmed.