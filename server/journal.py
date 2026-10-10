"""The journal spread for one walk (PRD 10.9, FR-09, FR-10).

Left page:   one photo of the person's own handwritten page. Upload, replace, rotate,
             remove. Explora never reads, OCRs, analyses or scans it.
Right page:  two Polaroid slots. Each slot holds one of *this walk's* photos and a caption,
             or has been removed (two, one or none are all fine; nothing is ever forced).

Stored in the existing tables: journal_pages (left_image, left_rotation) and polaroids
(walk_id, slot, discovery_id, caption, removed). Those tables have no timestamp columns,
so none are invented here: dates come from the walk and from each discovery
(captured_at), and a Polaroid reads its photo, label and note live from the discovery.

Choices worth knowing:
- The page photo is re-encoded as a JPEG without EXIF (like importer.py), so GPS in the
  original never reaches the data folder. The file is shrunk to PAGE_MAX_SIDE (a bit larger
  than a normal photo so handwriting stays legible). Rotation is only a stored number: the
  file is never rewritten when the person turns the page.
- Polaroid tilt is not stored. It is derived from a stable id (the photo's discovery id, or
  walk and slot for an empty slot), so the page looks the same after every reload.
- Removing a slot clears its photo and caption. Restoring gives back an empty slot.
- Nothing here counts, scores or scans the journal. It is the person's own.

Errors follow the other modules: KeyError for a missing walk or discovery (the HTTP layer
turns it into 404), ValueError for bad input (400).
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps

from . import walks
from .config import Config

SLOTS = (1, 2)
MAX_CAPTION = 200
PAGE_MAX_SIDE = 2000
TILT_MAX = 3.0            # degrees either way: a little off-square, never silly


# ------------------------------------------------------------------ helpers

def _check_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):   # True would silently mean 1
        raise ValueError(f"{name} must be a whole number")
    return value


def _walk(conn: sqlite3.Connection, walk_id: int) -> dict:
    _check_int(walk_id, "walk_id")
    walk = walks.get_walk(conn, walk_id)
    if walk is None:
        raise KeyError(f"No walk {walk_id}")
    return walk


def _slot(slot) -> int:
    _check_int(slot, "slot")
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of: {', '.join(map(str, SLOTS))}")
    return slot


def _tilt(walk_id: int, slot: int, discovery_id: int | None) -> float:
    """Stable 'controlled imperfection': same inputs, same tilt, every reload."""
    seed = f"photo:{discovery_id}" if discovery_id is not None else f"walk:{walk_id}:slot:{slot}"
    n = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    return round((n / 0xFFFFFFFF * 2 - 1) * TILT_MAX, 1)


def _inside_journal(cfg: Config, rel: str | None) -> Path | None:
    """The file for a stored relative path, only if it really is inside the journal folder."""
    if not rel:
        return None
    path = (cfg.data_dir / rel).resolve()
    if cfg.journal_dir.resolve() not in path.parents:
        return None
    return path


def _unlink_page(cfg: Config, rel: str | None) -> None:
    path = _inside_journal(cfg, rel)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass                           # a leftover file is harmless; never fail the request


# ------------------------------------------------------------------ reading

def left_page_path(conn: sqlite3.Connection, cfg: Config, walk_id: int) -> Path | None:
    """Where to serve the handwritten page from, or None if there is none."""
    _walk(conn, walk_id)
    row = conn.execute("SELECT left_image FROM journal_pages WHERE walk_id = ?", (walk_id,)).fetchone()
    path = _inside_journal(cfg, row["left_image"] if row else None)
    return path if path is not None and path.is_file() else None


def neighbors(conn: sqlite3.Connection, walk_id: int) -> dict:
    """Previous and next walk by date, for the page-turn navigation (FR-10). None at the ends."""
    walk = _walk(conn, walk_id)
    prev = conn.execute(
        "SELECT id FROM walks WHERE date < ? ORDER BY date DESC LIMIT 1", (walk["date"],)).fetchone()
    nxt = conn.execute(
        "SELECT id FROM walks WHERE date > ? ORDER BY date ASC LIMIT 1", (walk["date"],)).fetchone()
    return {"previous_walk_id": prev["id"] if prev else None, "next_walk_id": nxt["id"] if nxt else None}


def get_journal(conn: sqlite3.Connection, walk_id: int) -> dict:
    """The whole spread for a walk. Read-only: a walk nobody has journalled still has a
    valid, empty spread (blank left page, two empty slots) and nothing is written to show it."""
    walk = _walk(conn, walk_id)
    page = conn.execute(
        "SELECT left_image, left_rotation FROM journal_pages WHERE walk_id = ?", (walk_id,)).fetchone()
    rows = {r["slot"]: r for r in conn.execute(
        "SELECT slot, discovery_id, caption, removed FROM polaroids WHERE walk_id = ?", (walk_id,))}

    polaroids = []
    for slot in SLOTS:
        r = rows.get(slot)
        if r is not None and r["removed"]:
            polaroids.append({"slot": slot, "removed": True, "caption": "", "discovery_id": None,
                              "tilt": None, "discovery": None})
            continue
        did = r["discovery_id"] if r else None
        polaroids.append({
            "slot": slot,
            "removed": False,
            "caption": r["caption"] if r else "",
            "discovery_id": did,
            "tilt": _tilt(walk_id, slot, did),
            "discovery": walks.get_discovery(conn, did) if did is not None else None,
        })

    return {
        "walk": walk,
        "left_page": {"image": page["left_image"] if page else None,
                      "rotation": page["left_rotation"] if page else 0},
        "polaroids": polaroids,
        **neighbors(conn, walk_id),
    }


# ------------------------------------------------------------------ left page

def set_left_page(conn: sqlite3.Connection, cfg: Config, walk_id: int, data: bytes) -> dict:
    """Upload, or replace, the handwritten page. A new page starts unrotated."""
    _walk(conn, walk_id)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Please choose a photo of your page.")
    data = bytes(data)
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            page = ImageOps.exif_transpose(im).convert("RGB")
            page.thumbnail((PAGE_MAX_SIDE, PAGE_MAX_SIDE), Image.LANCZOS)
    except Exception as e:
        raise ValueError(f"That file is not a readable image ({type(e).__name__}).") from e

    rel = (Path("journal") / str(walk_id) / f"page_{hashlib.sha256(data).hexdigest()[:16]}.jpg").as_posix()
    dest = cfg.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    page.save(dest, format="JPEG", quality=90)               # no exif= argument: metadata dropped

    old = conn.execute("SELECT left_image FROM journal_pages WHERE walk_id = ?", (walk_id,)).fetchone()
    old_rel = old["left_image"] if old else None
    try:
        conn.execute(
            "INSERT INTO journal_pages(walk_id, left_image, left_rotation) VALUES(?, ?, 0) "
            "ON CONFLICT(walk_id) DO UPDATE SET left_image = excluded.left_image, left_rotation = 0",
            (walk_id, rel))
        conn.commit()
    except sqlite3.Error:
        if rel != old_rel:                                   # do not leave an orphan file behind
            _unlink_page(cfg, rel)
        raise
    if old_rel and old_rel != rel:                           # same bytes again = same file: keep it
        _unlink_page(cfg, old_rel)
    return get_journal(conn, walk_id)


def rotate_left_page(conn: sqlite3.Connection, walk_id: int, degrees: int = 90) -> dict:
    """Turn the page by a multiple of 90 degrees (negative turns the other way).
    Only the stored rotation changes; the image file is untouched."""
    _walk(conn, walk_id)
    _check_int(degrees, "degrees")
    if degrees == 0 or degrees % 90 != 0:
        raise ValueError("degrees must be a non-zero multiple of 90")
    row = conn.execute(
        "SELECT left_image, left_rotation FROM journal_pages WHERE walk_id = ?", (walk_id,)).fetchone()
    if row is None or not row["left_image"]:
        raise ValueError("There is no page to rotate yet.")
    conn.execute("UPDATE journal_pages SET left_rotation = ? WHERE walk_id = ?",
                 ((row["left_rotation"] + degrees) % 360, walk_id))
    conn.commit()
    return get_journal(conn, walk_id)


def remove_left_page(conn: sqlite3.Connection, cfg: Config, walk_id: int) -> dict:
    """Take the handwritten page off. Safe to repeat."""
    _walk(conn, walk_id)
    row = conn.execute("SELECT left_image FROM journal_pages WHERE walk_id = ?", (walk_id,)).fetchone()
    conn.execute("DELETE FROM journal_pages WHERE walk_id = ?", (walk_id,))
    conn.commit()
    if row:
        _unlink_page(cfg, row["left_image"])
    return get_journal(conn, walk_id)


# ------------------------------------------------------------------ Polaroids

def set_polaroid_photo(conn: sqlite3.Connection, walk_id: int, slot: int, discovery_id: int) -> dict:
    """Put one of this walk's photos in a slot (also brings back a removed slot).

    Any photo of the walk will do, identified or not. The caption already in the slot is kept.
    """
    _walk(conn, walk_id)
    _slot(slot)
    _check_int(discovery_id, "discovery_id")
    d = conn.execute("SELECT walk_id FROM discoveries WHERE id = ?", (discovery_id,)).fetchone()
    if d is None:
        raise KeyError(f"No discovery {discovery_id}")
    if d["walk_id"] != walk_id:
        raise ValueError("That photo is from a different walk.")
    other = conn.execute(
        "SELECT 1 FROM polaroids WHERE walk_id = ? AND slot != ? AND discovery_id = ? AND removed = 0",
        (walk_id, slot, discovery_id)).fetchone()
    if other:
        raise ValueError("That photo is already on this page.")
    conn.execute(
        "INSERT INTO polaroids(walk_id, slot, discovery_id, caption, removed) VALUES(?, ?, ?, '', 0) "
        "ON CONFLICT(walk_id, slot) DO UPDATE SET discovery_id = excluded.discovery_id, removed = 0",
        (walk_id, slot, discovery_id))
    conn.commit()
    return get_journal(conn, walk_id)


def set_caption(conn: sqlite3.Connection, walk_id: int, slot: int, caption: str | None) -> dict:
    """Type or change a caption. An empty caption clears it. Too long is refused, never
    silently cut: the person's own words should not quietly disappear."""
    _walk(conn, walk_id)
    _slot(slot)
    if caption is not None and not isinstance(caption, str):
        raise ValueError("caption must be text")
    caption = (caption or "").strip()
    if len(caption) > MAX_CAPTION:
        raise ValueError(f"That caption is too long ({MAX_CAPTION} characters max).")
    row = conn.execute(
        "SELECT removed FROM polaroids WHERE walk_id = ? AND slot = ?", (walk_id, slot)).fetchone()
    if row is not None and row["removed"]:
        raise ValueError("This Polaroid was removed. Bring it back to add a caption.")
    conn.execute(
        "INSERT INTO polaroids(walk_id, slot, caption) VALUES(?, ?, ?) "
        "ON CONFLICT(walk_id, slot) DO UPDATE SET caption = excluded.caption",
        (walk_id, slot, caption))
    conn.commit()
    return get_journal(conn, walk_id)


def remove_polaroid(conn: sqlite3.Connection, walk_id: int, slot: int) -> dict:
    """Remove a Polaroid slot, clearing its photo and caption. Safe to repeat."""
    _walk(conn, walk_id)
    _slot(slot)
    conn.execute(
        "INSERT INTO polaroids(walk_id, slot, discovery_id, caption, removed) VALUES(?, ?, NULL, '', 1) "
        "ON CONFLICT(walk_id, slot) DO UPDATE SET discovery_id = NULL, caption = '', removed = 1",
        (walk_id, slot))
    conn.commit()
    return get_journal(conn, walk_id)


def restore_polaroid(conn: sqlite3.Connection, walk_id: int, slot: int) -> dict:
    """Bring a removed slot back, empty. Safe to repeat."""
    _walk(conn, walk_id)
    _slot(slot)
    conn.execute("UPDATE polaroids SET removed = 0 WHERE walk_id = ? AND slot = ?", (walk_id, slot))
    conn.commit()
    return get_journal(conn, walk_id)