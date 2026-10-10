#!/usr/bin/env python3
"""Run the Explora Gemma evaluation (PRD section 15).

One invocation = one model x one condition. Run it four times for the full grid:

    python eval/run_eval.py --model gemma4:e2b --condition original
    python eval/run_eval.py --model gemma4:e2b --condition compressed
    python eval/run_eval.py --model gemma4:e4b --condition original
    python eval/run_eval.py --model gemma4:e4b --condition compressed

Photo layout (kept out of git; add eval/photos/ to .gitignore):

    eval/photos/<photo_id>.jpg               original quality
    eval/photos/compressed/<photo_id>.jpg    copy sent through a messaging app as a photo

Results go to eval/results/<e2b|e4b>_<condition>.csv, one row per run, written
as it goes. Re-running resumes where it stopped. The manual columns
(correctness, explanation_rating, factual_flag) are filled in by you afterwards.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from server.identify import OLLAMA_URL, identify, load_prompt, needs_safety_warning  # noqa: E402

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
COLD_LOAD_SECONDS = 2.0  # Ollama-reported model load above this = cold start

LOG_FIELDS = [
    "photo_id", "category", "difficulty", "split", "ground_truth", "truth_level",
    "how_verified", "condition", "model", "settings", "date", "repeat_no",
    "prompt_hash", "status", "predicted_category", "predicted_identification",
    "confidence", "time_s", "load_s", "cold", "app_warning", "explanation", "fact",
    "auto_match",
    # filled in by hand after the run:
    "correctness", "explanation_rating", "factual_flag", "notes", "error",
]


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def auto_match(ground_truth: str, predicted: str | None) -> str:
    """Cheap suggestion only: 'Exact' if the names overlap, else blank. Never 'Wrong'."""
    g, p = norm(ground_truth), norm(predicted)
    if g and p and (g in p or p in g):
        return "Exact"
    return ""


def find_photo(photos_dir: Path, condition: str, photo_id: str) -> Path | None:
    base = photos_dir if condition == "original" else photos_dir / "compressed"
    if not base.is_dir():
        return None
    for p in base.iterdir():
        if p.is_file() and p.stem == photo_id and p.suffix.lower() in IMG_EXTS:
            return p
    return None


def read_existing(out: Path) -> list[dict]:
    if not out.exists():
        return []
    with out.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="e.g. gemma4:e2b or gemma4:e4b")
    ap.add_argument("--condition", choices=["original", "compressed"], default="original")
    ap.add_argument("--splits", nargs="+", default=["scored", "probe"],
                    choices=["scored", "probe", "tune"])
    ap.add_argument("--prompt", type=Path, default=EVAL_DIR / "prompts" / "frozen.txt")
    ap.add_argument("--ground-truth", type=Path, default=EVAL_DIR / "ground_truth.csv")
    ap.add_argument("--photos-dir", type=Path, default=EVAL_DIR / "photos")
    ap.add_argument("--results-dir", type=Path, default=EVAL_DIR / "results")
    ap.add_argument("--host", default=OLLAMA_URL)
    ap.add_argument("--repeat-ids", default="",
                    help="comma-separated photo_ids to run several times (about 10 scored photos)")
    ap.add_argument("--repeats", type=int, default=3, help="runs per repeat id (default 3)")
    ap.add_argument("--max-side", type=int, default=768, help="longest image side sent to the model")
    ap.add_argument("--think", action="store_true", help="enable thinking (default: off)")
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--top-p", type=float)
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--limit", type=int, help="only run the first N jobs (smoke testing)")
    ap.add_argument(
        "--allow-unfrozen",
        action="store_true",
        help="allow scored/probe runs with a prompt that is not frozen.txt",
    )
    ap.add_argument(
        "--allow-default-results",
        action="store_true",
        help="allow custom ground truth to write to the default results directory",
    )
    args = ap.parse_args()

    default_gt = (EVAL_DIR / "ground_truth.csv").resolve()
    default_results = (EVAL_DIR / "results").resolve()

    custom_gt = args.ground_truth.resolve() != default_gt
    using_default_results = args.results_dir.resolve() == default_results

    if custom_gt and using_default_results and not args.allow_default_results:
        print(
            "Refusing: custom ground truth requires its own results directory. "
            "Use --results-dir to specify one, or --allow-default-results to override."
        )
        return 2

    scoring = bool({"scored", "probe"} & set(args.splits))
    if scoring and args.prompt.name != "frozen.txt" and not args.allow_unfrozen:
        print("Refusing: scored/probe runs must use eval/prompts/frozen.txt "
              "(freeze v1.txt first: copy it to frozen.txt). Use --allow-unfrozen to override.")
        return 2
    if not args.prompt.exists():
        print(f"Prompt not found: {args.prompt}")
        return 2
    prompt, phash = load_prompt(args.prompt)

    if not args.ground_truth.exists():
        print(f"Ground truth not found: {args.ground_truth}")
        return 2
        
    with args.ground_truth.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("Invalid ground-truth CSV: missing header; expected photo_id and other fields.")
            return 2

        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        required = {"photo_id", "split"}
        missing_columns = required - set(reader.fieldnames)
        if missing_columns:
            print(
                f"Invalid ground-truth CSV: missing columns {sorted(missing_columns)}; "
                f"found {reader.fieldnames}"
            )
            return 2

        gt_rows = [
            {key.strip(): value for key, value in row.items() if key is not None}
            for row in reader
            if (row.get("split") or "").strip() in args.splits
        ]
    if not gt_rows:
        print("No ground-truth rows match the requested splits.")
        return 2

    # --- job list: every photo once, plus extra runs for the repeat ids
    repeat_ids = {x.strip() for x in args.repeat_ids.split(",") if x.strip()}
    jobs = [(r, 1) for r in gt_rows]
    for r in gt_rows:
        if r["photo_id"] in repeat_ids and r["split"] == "scored":
            jobs += [(r, n) for n in range(2, args.repeats + 1)]
    if args.limit:
        jobs = jobs[: args.limit]

    # --- output file, resume support, prompt-consistency guard
    tag = args.model.split(":")[-1].replace("/", "_")
    args.results_dir.mkdir(parents=True, exist_ok=True)
    out = args.results_dir / f"{tag}_{args.condition}.csv"
    existing = read_existing(out)
    other_prompts = {r["prompt_hash"] for r in existing if r.get("prompt_hash")} - {phash}
    if other_prompts and scoring:
        print(f"{out.name} already holds rows from a different prompt ({', '.join(sorted(other_prompts))}). "
              "If the prompt changed, everything must be rerun: delete or move the file.")
        return 2
    done = {
        (r["photo_id"], r["repeat_no"])
        for r in existing
        if r.get("prompt_hash") == phash and r.get("status") == "ok"
}

    settings: dict = {"max_side": args.max_side, "think": True if args.think else False, "options": {}}
    for key, val in (("temperature", args.temperature), ("top_p", args.top_p), ("top_k", args.top_k)):
        if val is not None:
            settings["options"][key] = val

    new_file = not out.exists()
    missing, ran = [], 0
    print(f"model={args.model}  condition={args.condition}  prompt={args.prompt.name} ({phash})  "
          f"jobs={len(jobs)}  -> {out}")
    with out.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_file:
            writer.writeheader()
        for i, (gt, rep) in enumerate(jobs, 1):
            pid = gt["photo_id"]
            if (pid, str(rep)) in done:
                continue
            photo = find_photo(args.photos_dir, args.condition, pid)
            if photo is None:
                missing.append(pid)
                print(f"[{i}/{len(jobs)}] {pid}: photo missing for condition '{args.condition}', skipped")
                continue
            res = identify(photo, args.model, prompt, phash, settings, args.host, args.timeout)
            row = {
                "photo_id": pid,
                "category": gt.get("category", ""),
                "difficulty": gt.get("difficulty", ""),
                "split": gt.get("split", ""),
                "ground_truth": gt.get("ground_truth", ""),
                "truth_level": gt.get("truth_level", ""),
                "how_verified": gt.get("how_verified", ""),
                "condition": args.condition,
                "model": args.model,
                "settings": json.dumps(res.settings, sort_keys=True),
                "date": dt.datetime.now().isoformat(timespec="seconds"),
                "repeat_no": rep,
                "prompt_hash": phash,
                "status": res.status,
                "predicted_category": res.category,
                "predicted_identification": res.identification or "",
                "confidence": res.confidence,
                "time_s": res.elapsed_s,
                "load_s": res.load_s,
                "cold": res.load_s > COLD_LOAD_SECONDS,
                "app_warning": needs_safety_warning(res),
                "explanation": res.explanation,
                "fact": res.fact,
                "auto_match": auto_match(gt.get("ground_truth", ""), res.identification),
                "correctness": "", "explanation_rating": "", "factual_flag": "",
                "notes": gt.get("notes", ""),
                "error": res.error or "",
            }
            writer.writerow(row)
            f.flush()
            ran += 1
            shown = res.identification or "(no identification)"
            print(f"[{i}/{len(jobs)}] {pid} #{rep}: {shown} | {res.category} | {res.confidence} | "
                  f"{res.elapsed_s}s{' (cold)' if row['cold'] else ''}"
                  f"{' | ' + res.status if res.status != 'ok' else ''}")

    print(f"\nDone: {ran} new runs written to {out}")
    if missing:
        print(f"Missing photos ({len(set(missing))}): {', '.join(sorted(set(missing)))}")
    print("Next: fill the correctness / explanation_rating / factual_flag columns, then run eval/score.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())