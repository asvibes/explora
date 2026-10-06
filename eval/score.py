#!/usr/bin/env python3
"""Score the evaluation against the pass bar fixed in PRD section 15.4.

    python eval/score.py                       # reads eval/results/*.csv
    python eval/score.py --md eval/results/summary.md

Before scoring, fill these columns in each results CSV by hand:
    correctness         Exact | Close | Wrong
    explanation_rating  Useful | OK | Not useful
    factual_flag        yes if the explanation or fact has a false/unverifiable claim

Counts are reported as "n of N" because with 32 photos one photo is about 3 points.
Gates that cannot be evaluated yet (no graded rows, no probes) show n/a, not PASS.
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# --- the bar, fixed in advance (change only before the first scored run) ---
HONESTY_HIGH_EXACT_MIN = 0.80
HONESTY_WRONG_HEDGED_MIN = 0.70
USEFUL_CLEAR_MIN = 0.70
EXPL_USEFUL_MIN = 0.80
EXPL_FLAG_MAX = 0.10
COMPRESSION_DROP_MAX = 10.0   # percentage points
SPEED_MEDIAN_MAX = 30.0
SPEED_WORST_MAX = 90.0
GROUP_LEVEL_MIN = 0.85


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "t"}


def gt_group(cat: str) -> str:
    c = norm(cat)
    if c.startswith(("plant", "tree", "flower")):
        return "plants"
    if c.startswith(("butter", "insect", "bug")):
        return "insects"
    if c.startswith("bird"):
        return "birds"
    if c.startswith("fung"):
        return "fungi"
    return "other"


PRED_GROUP = {
    "bird": "birds", "butterfly": "insects", "insect": "insects",
    "plant": "plants", "tree": "plants", "flower": "plants", "fungi": "fungi",
}


def pred_group(cat: str) -> str:
    return PRED_GROUP.get(norm(cat), "other")


def ratio(n: int, d: int) -> str:
    return f"{n} of {d}" + (f" ({100 * n / d:.0f}%)" if d else "")


def grade(row: dict, use_auto: bool) -> str | None:
    c = (row.get("correctness") or "").strip().capitalize()
    if c in {"Exact", "Close", "Wrong"}:
        return c
    if use_auto and row.get("auto_match") == "Exact":
        return "Exact"
    return None


GATE_ACTIONS = {
    "Honesty": "Tighten the prompt and wording (use 'Might be' more), then rerun everything.",
    "Usefulness": "Lean on group-level wording and Firsts; label species identification experimental.",
    "Group level": "Species naming and group naming both weak: label the failing categories experimental.",
    "Explanations": "Review the explanation/fact prompt; consider dropping 'Did you know?' facts.",
    "Compression": "Advise sending photos as files; report the numbers.",
    "Speed": "Prefer the smaller model, lower max-side, and keep identification a batch step.",
    "Probes": "Fix the safety path (app warning) and the 'not sure' wording before anything else.",
}


def load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with p.open(newline="", encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    return rows


def evaluate_model(model: str, rows: list[dict], use_auto: bool, out: list[str]) -> dict:
    """Print the per-model report; return {gate_name: True/False/None}."""
    mine = [r for r in rows if r["model"] == model]
    first = [r for r in mine if r["split"] == "scored" and r.get("repeat_no") in ("1", "", None)]
    orig = [r for r in first if r["condition"] == "original"]
    comp = [r for r in first if r["condition"] == "compressed"]
    gates: dict[str, bool | None] = {}
    details: dict[str, str] = {}

    out.append(f"\n## {model}\n")
    ungraded = [r for r in first if grade(r, use_auto) is None]
    if ungraded:
        out.append(f"Note: {len(ungraded)} scored rows are still ungraded (correctness blank) and are left out of the gates.\n")

    g_orig = [(r, grade(r, use_auto)) for r in orig if grade(r, use_auto)]

    # Honesty
    high = [(r, g) for r, g in g_orig if r["confidence"] == "High"]
    wrong = [(r, g) for r, g in g_orig if g == "Wrong"]
    if g_orig and (high or wrong):
        parts, ok = [], True
        if high:
            n = sum(1 for _, g in high if g == "Exact")
            ok &= n / len(high) >= HONESTY_HIGH_EXACT_MIN
            parts.append(f"High answers that were Exact: {ratio(n, len(high))} (need >= 80%)")
        if wrong:
            n = sum(1 for r, _ in wrong if r["confidence"] in ("Low", "Medium"))
            ok &= n / len(wrong) >= HONESTY_WRONG_HEDGED_MIN
            parts.append(f"Wrong answers with Low/Medium confidence: {ratio(n, len(wrong))} (need >= 70%)")
        gates["Honesty"], details["Honesty"] = ok, "; ".join(parts)
    else:
        gates["Honesty"], details["Honesty"] = None, "no graded High answers or wrong answers yet"

    # Usefulness (clear photos)
    clear = [(r, g) for r, g in g_orig if norm(r["difficulty"]) == "clear"]
    if clear:
        n = sum(1 for _, g in clear if g in ("Exact", "Close"))
        gates["Usefulness"] = n / len(clear) >= USEFUL_CLEAR_MIN
        details["Usefulness"] = f"clear photos Exact or Close: {ratio(n, len(clear))} (need >= 70%)"
    else:
        gates["Usefulness"], details["Usefulness"] = None, "no graded clear photos"

    # Explanations
    exact = [r for r, g in g_orig if g == "Exact"]
    rated = [r for r in exact if (r.get("explanation_rating") or "").strip()]
    flagged_pool = [r for r in orig if (r.get("explanation_rating") or "").strip()]
    if exact and rated:
        useful = sum(1 for r in rated if norm(r["explanation_rating"]) == "useful")
        flags = sum(1 for r in flagged_pool if truthy(r.get("factual_flag")))
        ok = useful / len(rated) >= EXPL_USEFUL_MIN and flags / len(flagged_pool) <= EXPL_FLAG_MAX
        gates["Explanations"] = ok
        details["Explanations"] = (f"Exact answers rated Useful: {ratio(useful, len(rated))} (need >= 80%); "
                                   f"factual flags: {ratio(flags, len(flagged_pool))} (need <= 10%)")
    else:
        gates["Explanations"], details["Explanations"] = None, "no rated explanations yet"

    # Compression
    g_comp = [(r, grade(r, use_auto)) for r in comp if grade(r, use_auto)]
    if g_orig and g_comp:
        ec_o = sum(1 for _, g in g_orig if g in ("Exact", "Close")) / len(g_orig) * 100
        ec_c = sum(1 for _, g in g_comp if g in ("Exact", "Close")) / len(g_comp) * 100
        drop = ec_o - ec_c
        gates["Compression"] = drop <= COMPRESSION_DROP_MAX
        details["Compression"] = (f"Exact-or-Close original {ec_o:.0f}% vs compressed {ec_c:.0f}% "
                                  f"(drop {drop:+.0f} points, need <= 10)")
    else:
        gates["Compression"], details["Compression"] = None, "need graded rows for both conditions"

    # Speed (warm runs only; cold start reported separately)
    timed = [r for r in mine if r["split"] != "tune" and r.get("status") != "error" and r.get("time_s")]
    warm = [float(r["time_s"]) for r in timed if not truthy(r.get("cold"))]
    cold = [float(r["time_s"]) for r in timed if truthy(r.get("cold"))]
    if warm:
        med, worst = statistics.median(warm), max(warm)
        gates["Speed"] = med <= SPEED_MEDIAN_MAX and worst <= SPEED_WORST_MAX
        details["Speed"] = (f"warm median {med:.1f}s (need <= 30), worst {worst:.1f}s (need <= 90), "
                            f"{len(warm)} warm runs; cold runs: {', '.join(f'{c:.0f}s' for c in cold) or 'none'}")
        details["_median"] = f"{med:.4f}"
    else:
        gates["Speed"], details["Speed"] = None, "no timed runs"

    # Probes
    probes = [r for r in mine if r["split"] == "probe"]
    if probes:
        bad_high = [r for r in probes
                    if re.search(r"blur|non nature|\bfar\b", norm(r["category"])) and r["confidence"] == "High"]
        fungi = [r for r in probes if norm(r["category"]).startswith("fung")]
        no_warn = [r for r in fungi if not truthy(r.get("app_warning"))]
        gates["Probes"] = not bad_high and not no_warn
        details["Probes"] = (f"High-confidence answers on blurry/non-nature probes: {len(bad_high)} (need 0); "
                             f"fungi without app warning: {len(no_warn)} of {len(fungi)} (need 0)")
    else:
        gates["Probes"], details["Probes"] = None, "no probe runs"

    # Group level (proposed gate)
    if clear:
        n = sum(1 for r, _ in clear if pred_group(r["predicted_category"]) == gt_group(r["category"]))
        gates["Group level"] = n / len(clear) >= GROUP_LEVEL_MIN
        details["Group level"] = f"clear photos with the right group: {ratio(n, len(clear))} (need >= 85%)"
    else:
        gates["Group level"], details["Group level"] = None, "no graded clear photos"

    out.append("| Gate | Result | Detail |")
    out.append("|---|---|---|")
    for name in ("Honesty", "Usefulness", "Explanations", "Compression", "Speed", "Probes", "Group level"):
        res = {True: "PASS", False: "FAIL", None: "n/a"}[gates[name]]
        out.append(f"| {name} | {res} | {details[name]} |")

    # Outcome breakdown
    if g_orig:
        hit = sum(1 for r, g in g_orig if r["confidence"] == "High" and g != "Wrong")
        miss = sum(1 for r, g in g_orig if r["confidence"] == "High" and g == "Wrong")
        hedged = sum(1 for r, g in g_orig if r["confidence"] != "High" and g == "Wrong")
        over = sum(1 for r, g in g_orig if r["confidence"] != "High" and g != "Wrong")
        out.append(f"\nOutcomes (original, graded): confident hit {hit}, **confident miss {miss}**, "
                   f"hedged miss {hedged}, over-cautious {over}")
    malformed = sum(1 for r in mine if r.get("status") == "malformed")
    errors = sum(1 for r in mine if r.get("status") == "error")
    if malformed or errors:
        out.append(f"Malformed outputs: {malformed}; errors: {errors}")

    # Repeat consistency
    by_photo: dict[tuple, set] = defaultdict(set)
    reps: dict[tuple, int] = defaultdict(int)
    for r in mine:
        key = (r["condition"], r["photo_id"])
        by_photo[key].add(norm(r["predicted_identification"]))
        reps[key] += 1
    multi = [k for k, n in reps.items() if n > 1]
    if multi:
        same = sum(1 for k in multi if len(by_photo[k]) == 1)
        out.append(f"Repeat runs: {ratio(same, len(multi))} photos gave the same answer every time")

    gates["_median"] = float(details["_median"]) if "_median" in details else None  # type: ignore
    return gates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="results CSVs (default: eval/results/*.csv)")
    ap.add_argument("--use-auto", action="store_true",
                    help="count auto_match=Exact as graded when correctness is blank (unreviewed!)")
    ap.add_argument("--md", type=Path, help="also write the report to this markdown file")
    args = ap.parse_args()

    paths = args.files or sorted(p for p in RESULTS_DIR.glob("*.csv"))
    if not paths:
        print("No results CSVs found.")
        return 1
    rows = load_rows(paths)
    if not rows:
        print("Results files are empty.")
        return 1

    out = ["# Explora evaluation summary"]
    hashes = {r["prompt_hash"] for r in rows if r["split"] in ("scored", "probe") and r.get("prompt_hash")}
    if len(hashes) > 1:
        out.append(f"\n**WARNING: scored rows use {len(hashes)} different prompts ({', '.join(sorted(hashes))}). "
                   "Rerun everything with the frozen prompt.**")
    else:
        out.append(f"\nPrompt hash (all scored rows): {', '.join(hashes) or 'n/a'}")

    verdicts: dict[str, dict] = {}
    for model in sorted({r["model"] for r in rows}):
        verdicts[model] = evaluate_model(model, rows, args.use_auto, out)

    # Decision from PRD 15.5
    out.append("\n## Decision (PRD 15.5)\n")
    passing, failing = [], {}
    for m, g in verdicts.items():
        results = {k: v for k, v in g.items() if not k.startswith("_")}
        evaluated = {k: v for k, v in results.items() if v is not None}
        if not evaluated:
            continue
        failed = [k for k, v in evaluated.items() if v is False]
        pending = [k for k, v in results.items() if v is None]
        if not failed and not pending:
            passing.append(m)
        elif failed:
            failing[m] = failed
        else:
            out.append(f"- {m}: no failures yet, but gates still n/a: {', '.join(pending)}")
    if len(passing) > 1:
        fastest = min(passing, key=lambda m: verdicts[m].get("_median") or 1e9)
        out.append(f"- Both models pass: choose the faster one, **{fastest}**.")
    elif len(passing) == 1:
        out.append(f"- Only {passing[0]} passes: use it and accept its batch speed.")
    for m, failed in failing.items():
        for gate in failed:
            out.append(f"- {m} fails {gate}: {GATE_ACTIONS[gate]}")
    if not passing and not failing:
        out.append("- Nothing to decide yet: grade more rows first.")

    text = "\n".join(out)
    print(text)
    if args.md:
        args.md.write_text(text + "\n", encoding="utf-8")
        print(f"\nWritten to {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())