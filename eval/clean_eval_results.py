"""One-off: remove the 9 fake rows a stale test wrote into eval/results/e2b_original.csv.

Run from the repo root:  python eval/clean_eval_results.py
Makes a backup first (e2b_original.csv.bak). Only rows whose prompt_hash is
'testhash0001' are removed; everything else is kept as is.
"""
import csv
import shutil
from pathlib import Path

p = Path("eval/results/e2b_original.csv")
if not p.exists():
    raise SystemExit("eval/results/e2b_original.csv not found; run this from the repo root.")
shutil.copy(p, p.with_name(p.name + ".bak"))
with p.open(newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)
keep = [r for r in rows if r.get("prompt_hash") != "testhash0001"]
with p.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(keep)
print(f"Removed {len(rows) - len(keep)} test rows; {len(keep)} rows kept. Backup: {p.name}.bak")
