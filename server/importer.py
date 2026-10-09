"""Photo import (FR-01 to FR-03).

- Duplicates are detected by SHA-256 of the original bytes and skipped. The hash
  is for duplicates only, not for proving a photo is genuine.
- Capture date: EXIF DateTimeOriginal, else EXIF DateTime, else the file's
  modified time, else now. The source is recorded so the UI can say where the
  date came from and let the user edit it (many transfer apps strip metadata).
- Only a resized JPEG copy is stored, and it is re-encoded without EXIF, so GPS
  coordinates in the original never reach Explora's data folder.
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .config import Config

EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306


@dataclass
class ImportResult:
    status: str                  # "added" | "duplicate" | "error"
    name: str
    discovery_id: int | None = None
    captured_at: str | None = None
    captured_source: str | None = None
    reason: str | None = None


def _parse_exif_dt(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip().replace("\x00", ""), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def read_capture_time(im: Image.Image, file_mtime: float | None) -> tuple[datetime, str]:
    exif = im.getexif()
    dt = None
    try:
        dt = _parse_exif_dt(exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL))
    except Exception:
        pass
    dt = dt or _parse_exif_dt(exif.get(TAG_DATETIME))
    if dt:
        return dt, "exif"
    if file_mtime:
        return datetime.fromtimestamp(file_mtime).replace(microsecond=0), "file"
    return datetime.now().replace(microsecond=0), "unknown"


def import_bytes(
    conn: sqlite3.Connection,
    cfg: Config,
    walk_id: int,
    data: bytes,
    filename: str = "photo",
    file_mtime: float | None = None,
) -> ImportResult:
    """Import one photo's bytes into a walk. Never raises for bad input; returns status 'error'."""
    digest = hashlib.sha256(data).hexdigest()
    if conn.execute("SELECT 1 FROM discoveries WHERE content_hash = ?", (digest,)).fetchone():
        return ImportResult("duplicate", filename, reason="already imported")

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            captured, source = read_capture_time(im, file_mtime)
            display = ImageOps.exif_transpose(im).convert("RGB")
            display.thumbnail((cfg.display_max_side, cfg.display_max_side), Image.LANCZOS)
    except Exception as e:
        return ImportResult("error", filename, reason=f"not a readable image ({type(e).__name__})")

    rel = Path("photos") / str(walk_id) / f"{digest[:16]}.jpg"
    dest = cfg.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    display.save(dest, format="JPEG", quality=88)          # no exif= argument: metadata dropped

    try:
        cur = conn.execute(
            "INSERT INTO discoveries(walk_id, photo_path, content_hash, original_name, captured_at, "
            "captured_source, created_at) VALUES(?,?,?,?,?,?,?)",
            (walk_id, rel.as_posix(), digest, filename, captured.isoformat(timespec="seconds"),
             source, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        dest.unlink(missing_ok=True)
        if "content_hash" in str(e):
            return ImportResult("duplicate", filename, reason="already imported")
        return ImportResult("error", filename, reason=str(e))

    return ImportResult("added", filename, cur.lastrowid, captured.isoformat(timespec="seconds"), source)


def import_path(conn: sqlite3.Connection, cfg: Config, walk_id: int, path: str | Path) -> ImportResult:
    p = Path(path)
    try:
        return import_bytes(conn, cfg, walk_id, p.read_bytes(), p.name, p.stat().st_mtime)
    except OSError as e:
        return ImportResult("error", p.name, reason=str(e))


def import_paths(conn: sqlite3.Connection, cfg: Config, walk_id: int, paths) -> list[ImportResult]:
    return [import_path(conn, cfg, walk_id, p) for p in paths]


def summarize(results: list[ImportResult]) -> dict:
    return {
        "added": sum(r.status == "added" for r in results),
        "duplicates": sum(r.status == "duplicate" for r in results),
        "errors": [(r.name, r.reason) for r in results if r.status == "error"],
    }