import csv
from pathlib import Path

path = Path("eval/ground_truth.csv")
backup = Path("eval/ground_truth.backup.csv")

if not backup.exists():
    raise SystemExit("Backup missing. Stop; no changes made.")

with path.open(newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

required = {"photo_id", "split", "how_verified"}
if not fields or not required.issubset(fields):
    raise SystemExit("Unexpected CSV columns. Stop; no changes made.")

for row in rows:
    # Keep the previously tested photo out of scored evaluation.
    if row["photo_id"] == "p02":
        row["split"] = "tune"
    elif row["category"].strip().lower() in {"fungi", "not_nature"}:
        row["split"] = "probe"
    else:
        row["split"] = "scored"

    # Record the method honestly without claiming independent verification.
    if row["how_verified"].strip().lower() in {"thumbnail_review", "visual_only"}:
        row["how_verified"] = "visual_only"

with path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print(f"Updated {len(rows)} rows. Backup preserved at {backup}.")
print("Split counts:")
for split in ("scored", "probe", "tune"):
    print(f"  {split}: {sum(r['split'] == split for r in rows)}")
