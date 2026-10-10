"""Firsts: quiet memories of the first time a category was confirmed (PRD 10.7, FR-14).

A First is "your first butterfly", not an achievement. This module deliberately has:
- no points, levels, counters, targets or "next First" hints;
- no model calls and no automatic awarding of anything the person has not confirmed;
- category level only: exact species are less reliable, and a false First would be a
  false memory.

Rules:
- Only a *confirmed* discovery can earn a First (confirm or correct, in walks.py).
- One First per category, ever. The `firsts.category` UNIQUE constraint enforces it and
  the insert uses ON CONFLICT DO NOTHING, so a later discovery can never overwrite an
  existing First, even with two connections racing.
- "First" means first *confirmed*, in the order the person confirmed them. It is not
  re-assigned if an older photo is confirmed later: the memory was already made.
- A First is kept even if the discovery behind it is later changed. It is a record of
  what happened, not a live view of the discovery.
- "other" never earns a First: it is a catch-all, so "First other" would not mean
  anything to the person.

Errors follow the other modules: KeyError for a missing record (the HTTP layer turns it
into 404), ValueError for bad input or an unconfirmed discovery (400).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from .models import USER_CATEGORIES, Status

# Categories that never earn a First.
NO_FIRST_CATEGORIES = frozenset({"other"})

_SELECT = (
    "SELECT f.id, f.category, f.discovery_id, f.walk_id, f.created_at, "
    "       d.photo_path, d.final_label, d.captured_at, w.date AS walk_date "
    "FROM firsts f "
    "JOIN walks w ON w.id = f.walk_id "
    "LEFT JOIN discoveries d ON d.id = f.discovery_id "
)


def first_text(category: str) -> str:
    """Quiet wording, as in the PRD: "First butterfly"."""
    return f"First {category}"


def _present(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["text"] = first_text(d["category"])
    return d


def _check_id(value, name: str) -> int:
    # bool is an int subclass; True would silently mean id 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    return value


def _require_walk(conn: sqlite3.Connection, walk_id: int) -> None:
    if conn.execute("SELECT 1 FROM walks WHERE id = ?", (walk_id,)).fetchone() is None:
        raise KeyError(f"No walk {walk_id}")


# ------------------------------------------------------------------ reading

def get_first(conn: sqlite3.Connection, category: str) -> dict | None:
    """The First for a category, or None if the person has not had one yet."""
    row = conn.execute(_SELECT + "WHERE f.category = ?", (category,)).fetchone()
    return _present(row) if row else None


def list_firsts(conn: sqlite3.Connection) -> list[dict]:
    """All Firsts, oldest first. A personal record: nothing is counted or ranked."""
    rows = conn.execute(_SELECT + "ORDER BY f.created_at, f.id").fetchall()
    return [_present(r) for r in rows]


def firsts_for_walk(conn: sqlite3.Connection, walk_id: int) -> list[dict]:
    """Firsts earned on one walk."""
    _check_id(walk_id, "walk_id")
    _require_walk(conn, walk_id)
    rows = conn.execute(_SELECT + "WHERE f.walk_id = ? ORDER BY f.created_at, f.id", (walk_id,)).fetchall()
    return [_present(r) for r in rows]


# ------------------------------------------------------------------ recording

def record_first(conn: sqlite3.Connection, discovery_id: int) -> dict | None:
    """Record a First for this discovery's category if the category has none yet.

    Returns the new First, or None when the category already had one (nothing is
    changed) or when the category never earns a First. Safe to call after every
    confirm or correct: repeating it, or calling it for a second butterfly, is a no-op.

    Raises KeyError if the discovery does not exist, ValueError if the id is not a whole
    number, the discovery is not confirmed, or its confirmed category is not valid.
    """
    _check_id(discovery_id, "discovery_id")
    row = conn.execute(
        "SELECT id, walk_id, status, final_category FROM discoveries WHERE id = ?", (discovery_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"No discovery {discovery_id}")
    if row["status"] != Status.CONFIRMED.value:
        raise ValueError("Only a discovery you confirmed can earn a First.")
    category = row["final_category"]
    if category not in USER_CATEGORIES:
        raise ValueError(f"Cannot record a First for category {category!r}.")
    if category in NO_FIRST_CATEGORIES:
        return None

    cur = conn.execute(
        "INSERT INTO firsts(category, discovery_id, walk_id, created_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(category) DO NOTHING",
        (category, discovery_id, row["walk_id"], datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None                      # the category already has its First: leave it alone
    return get_first(conn, category)