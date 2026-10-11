"""Calendar thumbnails (PRD 10.2, FR-21): which photo represents a walk on the Home calendar.

Order of choice for a walk:
  1. The photo the person picked for the calendar (walk_thumbnails). It stays until they change it.
  2. The photo in journal Polaroid slot 1 (if that slot is not removed).
  3. The first confirmed photo (by capture time, then id).
  4. The first photo (by capture time, then id).
  5. None (a walk with no photos is still a valid walk).

Only the person's own pick is stored. Everything else is worked out on the fly, so changing the
journal or confirming a photo is reflected without any extra bookkeeping.

Errors follow the other modules: KeyError for a missing walk or discovery (the HTTP layer turns it
into 404), ValueError for bad input (400).
"""
from __future__ import annotations

import re
import sqlite3

from . import walks
from .models import Status

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _check_id(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):   # True would silently mean 1
        raise ValueError(f"{name} must be a whole number")
    return value


def chosen(conn: sqlite3.Connection, walk_id: int) -> int | None:
    """The photo the person picked, if it still belongs to this walk."""
    row = conn.execute(
        "SELECT t.discovery_id FROM walk_thumbnails t "
        "JOIN discoveries d ON d.id = t.discovery_id AND d.walk_id = t.walk_id "
        "WHERE t.walk_id = ?", (walk_id,)).fetchone()
    return row["discovery_id"] if row else None


def effective(conn: sqlite3.Connection, walk_id: int) -> tuple[int | None, bool]:
    """(discovery_id or None, True if it is the person's own pick)."""
    pick = chosen(conn, walk_id)
    if pick is not None:
        return pick, True
    row = conn.execute(
        "SELECT p.discovery_id FROM polaroids p "
        "JOIN discoveries d ON d.id = p.discovery_id AND d.walk_id = p.walk_id "
        "WHERE p.walk_id = ? AND p.slot = 1 AND p.removed = 0", (walk_id,)).fetchone()
    if row and row["discovery_id"] is not None:
        return row["discovery_id"], False
    row = conn.execute(
        "SELECT id FROM discoveries WHERE walk_id = ? AND status = ? "
        "ORDER BY captured_at, id LIMIT 1", (walk_id, Status.CONFIRMED.value)).fetchone()
    if row:
        return row["id"], False
    row = conn.execute(
        "SELECT id FROM discoveries WHERE walk_id = ? ORDER BY captured_at, id LIMIT 1",
        (walk_id,)).fetchone()
    return (row["id"], False) if row else (None, False)


def set_thumbnail(conn: sqlite3.Connection, walk_id: int, discovery_id: int) -> dict:
    """Remember the person's pick for this walk. Any photo of the walk will do."""
    _check_id(walk_id, "walk_id")
    _check_id(discovery_id, "discovery_id")
    if walks.get_walk(conn, walk_id) is None:
        raise KeyError(f"No walk {walk_id}")
    d = conn.execute("SELECT walk_id FROM discoveries WHERE id = ?", (discovery_id,)).fetchone()
    if d is None:
        raise KeyError(f"No discovery {discovery_id}")
    if d["walk_id"] != walk_id:
        raise ValueError("That photo is from a different walk.")
    conn.execute(
        "INSERT INTO walk_thumbnails(walk_id, discovery_id) VALUES(?, ?) "
        "ON CONFLICT(walk_id) DO UPDATE SET discovery_id = excluded.discovery_id",
        (walk_id, discovery_id))
    conn.commit()
    return {"walk_id": walk_id, "thumbnail_discovery_id": discovery_id, "thumbnail_custom": True}


def month_view(conn: sqlite3.Connection, month: str) -> dict:
    """Walks in one calendar month ("YYYY-MM"), each with its thumbnail. Read-only."""
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError("month must look like 2026-10")
    rows = conn.execute(
        "SELECT w.id, w.date, w.area, w.habitat, "
        "(SELECT COUNT(*) FROM discoveries d WHERE d.walk_id = w.id) AS photo_count "
        "FROM walks w WHERE w.date LIKE ? ORDER BY w.date", (month + "-%",)).fetchall()
    out = []
    for r in rows:
        did, custom = effective(conn, r["id"])
        out.append({**dict(r), "thumbnail_discovery_id": did, "thumbnail_custom": custom})
    return {"month": month, "walks": out}