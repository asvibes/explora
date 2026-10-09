import csv, collections, pathlib

rows = list(csv.DictReader(open("eval/ground_truth.csv", newline="", encoding="utf-8")))
print(len(rows), "rows | columns:", list(rows[0].keys()) if rows else "none")

for col in ("split", "difficulty", "truth_level", "how_verified", "category"):
    if rows and col in rows[0]:
        print(col, dict(collections.Counter(r[col] or "<blank>" for r in rows)))

files = {p.stem for p in pathlib.Path("eval/photos").glob("*.jpg")}
ids = [r["photo_id"] for r in rows]
print("ids without a photo:", sorted(set(ids) - files))
print("photos without a row:", sorted(files - set(ids)))
print("duplicate ids:", [i for i, n in collections.Counter(ids).items() if n > 1])

for r in rows:
    problems = [c for c in ("category", "difficulty", "split", "ground_truth", "truth_level", "how_verified") if not (r.get(c) or "").strip()]
    if r.get("truth_level", "").lower() == "species" and r.get("how_verified") == "visual_only":
        problems.append("species claimed with visual_only")
    if problems:
        print(r["photo_id"], "->", problems)