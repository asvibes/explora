import csv, datetime, pathlib

for p in sorted(pathlib.Path("eval/results").glob("*.csv")):
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
    print(f"\n=== {p.name} | {len(rows)} rows | modified {mtime:%Y-%m-%d %H:%M}")
    if not rows:
        print("(header only)")
        continue
    print("columns:", list(rows[0].keys()))
    for r in rows[:3]:
        print(r)
    for col in ("model", "predicted", "identification", "confidence"):
        if col in rows[0]:
            vals = sorted({r[col] for r in rows})
            print(f"distinct {col}: {len(vals)}", vals[:8])