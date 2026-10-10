# Field test (nine photos)

Last updated: 2026-10-10.

**Status: pending. No field-test results have been collected, and none appear in this document.**

## Purpose and separation from the main evaluation

The field test checks how the pipeline behaves on nine photos taken outside the original evaluation set. It is **not** part of the original 26-row evaluation and must not change it.

Rules:

- Own ground-truth file, own photos folder, own results folder.
- Never write field rows into `eval/results/`. `run_eval.py` enforces this: a custom `--ground-truth` without its own `--results-dir` is refused with exit code 2.
- Never score field rows together with the original rows. `score.py` takes explicit CSV paths, so pass only the field CSVs.
- The field rows use `split = scored` only so that `run_eval.py` will run them. That label does not make them part of the scored set.
- With nine photos one photo is about 11 percentage points. Treat results as observations, not as a pass or fail against the gates.

## Dataset

Ground truth was labelled by the user, at common-name level only (`truth_level = visual_group`, `how_verified = user_labeled`, note "Common name only"). Nothing is independently verified.

| photo_id | category | ground truth | difficulty |
| --- | --- | --- | --- |
| f01 | tree | tree | easy |
| f02 | flower | flower | easy |
| f03 | leaves | leaves | easy |
| f04 | butterfly | butterfly | easy |
| f05 | butterfly | butterfly | easy |
| f06 | bird | bird | easy |
| f07 | bird | bird | easy |
| f08 | rock | rocks | easy |
| f09 | rock | rocks | easy |

Things to settle before running:

- `f03` uses the category `leaves`, which is not a model category (the model uses `plant`, `tree`, `flower` and so on). `score.py` would map `leaves` to the group "other", so a correct `plant` answer would count as the wrong group. Decide whether to relabel it `plant`.
- All rows are `easy`. `score.py`'s clear-photo gates look for `clear`, so they would be `n/a` for this set, as they currently are for the original set.
- Because ground truth is only a common name, define in advance what counts as Exact and Close. For example, does "a bird" versus "a Blue Jay" count? Write the rule down first.

## Proposed layout

These paths are a proposal. Change them to match your repository.

```text
eval/field/ground_truth_field.csv
eval/field/photos/f01.jpg ... f09.jpg
eval/field/photos/compressed/        (only if you also test the compressed condition)
eval/field/results/                  (outputs)
```

## Procedure

1. Back up `eval/results/` and `eval/ground_truth.csv` (see the save instructions delivered with these files).
2. Put the nine photos and the field ground-truth CSV in the folders above. Save the CSV as comma-separated UTF-8. A BOM (Excel "CSV UTF-8") is tolerated, but a semicolon or tab separator is rejected with a message listing the columns found.
3. Confirm the prompt: use `eval/prompts/frozen.txt` and record its hash (command in [evaluation.md](evaluation.md), section 3).
4. Make sure Ollama is running and the model is pulled (`ollama list`).
5. Run, one model and condition at a time:

   ```powershell
   python eval/run_eval.py --model gemma4:e2b --condition original `
     --ground-truth eval/field/ground_truth_field.csv `
     --photos-dir eval/field/photos `
     --results-dir eval/field/results
   ```

   Repeat with `gemma4:e4b`. Add `--limit 1` first as a smoke test, but delete that row (after backing up) before the real run, because resume treats it as done.
6. Fill `correctness`, `explanation_rating` and `factual_flag` by hand in `eval/field/results/*.csv`.
7. Score the field files on their own:

   ```powershell
   python eval/score.py eval/field/results/e2b_original.csv --md eval/field/results/summary.md
   ```

   Read the output as observations. Do not copy it into the original results table.

If any row ends with `status = error`, remember that a rerun will skip it (see Known issues in [evaluation.md](evaluation.md)). Back up the CSV, remove the error row, then rerun.

## What to measure

Per photo and per model, from the results CSV:

- predicted category, identification and confidence
- `status` (`ok`, `malformed`, `error`) and the `error` text
- `time_s`, `load_s` and `cold` (first run after loading is slow)
- `app_warning` (whether the app would add its own warning)
- `correctness`, `explanation_rating`, `factual_flag` (manual)

Also record the environment once, because speed depends on it: Windows version, CPU and GPU, RAM, Ollama version, and whether the model was already loaded. These are not collected by the harness, so write them in the table below.

## Observations (to fill in)

Environment:

| Item | Value |
| --- | --- |
| Windows version | not recorded |
| CPU / GPU / RAM | not recorded |
| Ollama version | not recorded |
| Frozen prompt hash | not recorded |

Results:

| photo_id | model | prediction | confidence | time (s) | cold | correctness | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| f01 | | | | | | | not collected |
| f02 | | | | | | | not collected |
| f03 | | | | | | | not collected |
| f04 | | | | | | | not collected |
| f05 | | | | | | | not collected |
| f06 | | | | | | | not collected |
| f07 | | | | | | | not collected |
| f08 | | | | | | | not collected |
| f09 | | | | | | | not collected |

## What the existing tests do and do not show

The regression tests for this set (nine rows, `f01` to `f09`) replace the model with a fake function. They show that the harness reads the ground truth (plain, UTF-8 BOM and spaced-header variants), writes nine rows to its own results folder, rejects a wrong delimiter with a readable error, reports a missing photo without stopping, and does not touch the original results. They say nothing about how the real model performs on these photos. They were written, not run, for this document.

## Safety considerations

For the person taking the photos:

- Do not touch, pick, taste or approach what you photograph, especially fungi, berries, reptiles and insects that sting or bite. Zoom instead of moving closer.
- Keep to places away from roads and water edges. The app deliberately offers no street or water-edge habitat.
- Do not go anywhere unsafe for a better shot, and keep wildlife undisturbed.
- Keep people, faces and house numbers out of the frame where you can.

For the data:

- The evaluation scripts read original photos directly. Originals may carry EXIF and GPS. Keep `eval/photos/` and `eval/field/photos/` out of git, and out of shared folders.
- Field ground truth is user-labelled. Do not describe a species-level claim from this set as verified.

For reading the results:

- The model must never be treated as a source of edibility or safety advice. The prompt forbids safe-to-eat or safe-to-touch statements and the app drops them, but a field result does not show that this always holds. Check the `explanation` and `fact` columns for any reassurance and set `factual_flag` accordingly.
- Record any High-confidence wrong answer. These "confident misses" matter more than the accuracy percentage.
- Fungi and reptile photos are not in the nine-photo set. The app-side warning path for them is covered by unit tests with fake results, not by this field test.