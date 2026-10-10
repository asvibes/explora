"""Optional nature quests (PRD 10.6, FR-13).

Quests are quiet suggestions, not tasks. This module deliberately has:
- no streaks, badges, points, counters, "remaining" or "x of y" anywhere;
- no model calls: wording comes from reviewed templates in
  server/content/quest_templates.json, so it is stable, offline and testable;
- no automatic completion: a quest is done only when the person says "I did this".

Safety (PRD 13): every template is scanned with safety.quest_violations when the
file is loaded. If a template trips the scan, or the banned-phrase list has no
[quests] section, loading fails. Quests fail closed instead of being shown unscanned.

Storage: only completed quests are stored (quest_completions). A suggestion that
was not done leaves no trace, and a completion is shown only on its own walk.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import safety
from .models import HABITATS

TEMPLATES_FILE = Path(__file__).resolve().parent / "content" / "quest_templates.json"
DIFFICULTIES = ("easy", "tricky")
DEFAULT_SUGGESTIONS = 3
MAX_SUGGESTIONS = 5
_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_MAX_TEXT = 160


class QuestError(ValueError):
    """The template file is missing, malformed or unsafe."""


@dataclass(frozen=True)
class QuestTemplate:
    id: str
    text: str
    difficulty: str = "easy"
    habitats: tuple[str, ...] = ()      # empty = suits any habitat


# ------------------------------------------------------------------ loading

def _parse(raw: object, source: str) -> tuple[QuestTemplate, ...]:
    items = raw.get("templates") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise QuestError(f"{source}: expected an object with a non-empty 'templates' list")
    banned = safety.load_banned().get("quests", ())
    if not banned:
        raise QuestError("banned_phrases.txt has no [quests] section; refusing to load quests unscanned")
    out: list[QuestTemplate] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise QuestError(f"{source}: template #{i + 1} is not an object")
        tid, text = it.get("id"), it.get("text")
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            raise QuestError(f"{source}: template #{i + 1} needs an id of lowercase letters, digits, underscores")
        if tid in seen:
            raise QuestError(f"{source}: duplicate template id '{tid}'")
        seen.add(tid)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > _MAX_TEXT:
            raise QuestError(f"{source}: template '{tid}' needs text of 1 to {_MAX_TEXT} characters")
        text = text.strip()
        diff = it.get("difficulty", "easy")
        if diff not in DIFFICULTIES:
            raise QuestError(f"{source}: template '{tid}' difficulty must be one of {DIFFICULTIES}")
        habitats = it.get("habitats", [])
        if not isinstance(habitats, list) or any(h not in HABITATS for h in habitats):
            raise QuestError(f"{source}: template '{tid}' habitats must be a list from {HABITATS}")
        hits = safety.quest_violations(text)
        if hits:
            raise QuestError(f"{source}: template '{tid}' contains banned phrase(s): {', '.join(hits)}")
        out.append(QuestTemplate(tid, text, diff, tuple(habitats)))
    return tuple(out)


@lru_cache(maxsize=4)
def _load(path_str: str) -> tuple[QuestTemplate, ...]:
    path = Path(path_str)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise QuestError(f"cannot read {path}: {e.strerror or e}") from e
    except json.JSONDecodeError as e:
        raise QuestError(f"{path} is not valid JSON: {e}") from e
    return _parse(raw, path.name)


def load_templates(path: str | Path | None = None) -> tuple[QuestTemplate, ...]:
    return _load(str(path or TEMPLATES_FILE))


# ------------------------------------------------------------------ suggestions

def _order_key(walk_id: int, template_id: str) -> str:
    # sha256, not hash(): Python's hash() changes between runs, this does not.
    return hashlib.sha256(f"{walk_id}:{template_id}".encode()).hexdigest()


def suggest(walk_id: int, habitat: str | None = None, difficulty: str | None = None,
            count: int = DEFAULT_SUGGESTIONS, templates=None) -> list[dict]:
    """A few optional ideas for a walk.

    Seeded by walk id, so the same walk shows the same ideas after every reload
    (the same stable-imperfection rule as the Polaroid tilt). Returns only the
    ideas: no totals, no "remaining", nothing to complete.
    """
    if habitat is not None and habitat not in HABITATS:
        raise ValueError(f"habitat must be one of: {', '.join(HABITATS)}")
    if difficulty is not None and difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty must be one of: {', '.join(DIFFICULTIES)}")
    if not isinstance(count, int) or not 1 <= count <= MAX_SUGGESTIONS:
        raise ValueError(f"count must be a whole number from 1 to {MAX_SUGGESTIONS}")
    pool = templates if templates is not None else load_templates()
    pool = [t for t in pool
            if (difficulty is None or t.difficulty == difficulty)
            and (habitat is None or not t.habitats or habitat in t.habitats)]
    pool.sort(key=lambda t: _order_key(walk_id, t.id))
    return [{"id": t.id, "text": t.text, "difficulty": t.difficulty} for t in pool[:count]]


# ------------------------------------------------------------------ "I did this"

def _require_walk(conn: sqlite3.Connection, walk_id: int) -> None:
    if conn.execute("SELECT 1 FROM walks WHERE id = ?", (walk_id,)).fetchone() is None:
        raise KeyError(f"No walk {walk_id}")


def _completion(conn: sqlite3.Connection, walk_id: int, template_id: str) -> dict | None:
    row = conn.execute(
        "SELECT template_id, wording, confirmed_at FROM quest_completions "
        "WHERE walk_id = ? AND template_id = ? ORDER BY id LIMIT 1", (walk_id, template_id)).fetchone()
    return dict(row) if row else None


def mark_done(conn: sqlite3.Connection, walk_id: int, template_id: str, templates=None) -> dict:
    """The person taps "I did this". Nothing is ever awarded automatically.

    Safe to repeat: a second tap returns the existing completion. The wording
    shown at that moment is stored, so the walk keeps reading the same later.
    """
    _require_walk(conn, walk_id)
    pool = templates if templates is not None else load_templates()
    tpl = next((t for t in pool if t.id == template_id), None)
    if tpl is None:
        raise KeyError(f"No quest '{template_id}'")
    conn.execute(
        "INSERT INTO quest_completions(walk_id, template_id, wording, confirmed_at) "
        "SELECT ?, ?, ?, ? WHERE NOT EXISTS "
        "(SELECT 1 FROM quest_completions WHERE walk_id = ? AND template_id = ?)",
        (walk_id, tpl.id, tpl.text, datetime.now().isoformat(timespec="seconds"), walk_id, tpl.id))
    conn.commit()
    return _completion(conn, walk_id, tpl.id)  # type: ignore[return-value]


def unmark(conn: sqlite3.Connection, walk_id: int, template_id: str) -> bool:
    """Take back a mistaken tap. Returns True if something was removed."""
    _require_walk(conn, walk_id)
    cur = conn.execute("DELETE FROM quest_completions WHERE walk_id = ? AND template_id = ?",
                       (walk_id, template_id))
    conn.commit()
    return cur.rowcount > 0


def completions(conn: sqlite3.Connection, walk_id: int) -> list[dict]:
    """Quests done on this walk. They are visible only here, never aggregated."""
    _require_walk(conn, walk_id)
    rows = conn.execute(
        "SELECT template_id, wording, confirmed_at FROM quest_completions "
        "WHERE walk_id = ? ORDER BY confirmed_at, id", (walk_id,)).fetchall()
    return [dict(r) for r in rows]