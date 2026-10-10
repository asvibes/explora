"""Walks, discoveries and the collection (FR-04 to FR-06, FR-08, FR-11).

The model only suggests; the user decides. State moves like this:

    imported -> identified -> confirmed        (confirm / correct)
                           -> rejected         (Not This)
                           -> saved_unidentified (Save Anyway)

A walk is never "incomplete": there is no completeness field anywhere, and a
walk with zero photos and zero writing is still valid.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from . import safety
from .config import Config
from .identify import IdentifyResult, identify as _identify
from .identify import load_prompt
from .models import HABITATS, USER_CATEGORIES, Status, suggestion_text

WALK_FIELDS = {"area", "habitat", "duration_min"}


class IdentifyError(RuntimeError):
    """Ollama unreachable, model missing, unreadable image... (not a 'not sure' answer)."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso_day(day) -> str:
    if day is None:
        return date.today().isoformat()
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    return date.fromisoformat(str(day)).isoformat()          # raises ValueError if malformed


# ------------------------------------------------------------------ walks

def get_or_create_walk(conn: sqlite3.Connection, day=None) -> dict:
    """One walk per day. Calling again for the same day returns the existing walk."""
    iso = _iso_day(day)
    conn.execute("INSERT OR IGNORE INTO walks(date, created_at) VALUES(?, ?)", (iso, _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM walks WHERE date = ?", (iso,)).fetchone())


def get_walk(conn: sqlite3.Connection, walk_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM walks WHERE id = ?", (walk_id,)).fetchone()
    return dict(row) if row else None


def update_walk(conn: sqlite3.Connection, walk_id: int, **fields) -> dict:
    """Set area / habitat / duration_min. Pass None to clear a field."""
    unknown = set(fields) - WALK_FIELDS
    if unknown:
        raise ValueError(f"Unknown walk field(s): {', '.join(sorted(unknown))}")
    if fields.get("habitat") not in (None, *HABITATS):
        raise ValueError(f"habitat must be one of: {', '.join(HABITATS)}")
    dur = fields.get("duration_min")
    if dur is not None and (not isinstance(dur, int) or dur <= 0):
        raise ValueError("duration_min must be a positive whole number")
    if "area" in fields and fields["area"] is not None:
        fields["area"] = str(fields["area"]).strip()[:200] or None
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE walks SET {sets} WHERE id = ?", (*fields.values(), walk_id))
        conn.commit()
    walk = get_walk(conn, walk_id)
    if walk is None:
        raise KeyError(f"No walk {walk_id}")
    return walk


def list_walks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT w.*, (SELECT COUNT(*) FROM discoveries d WHERE d.walk_id = w.id) AS photo_count "
        "FROM walks w ORDER BY w.date DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ discoveries

def _present(row: sqlite3.Row | dict) -> dict:
    """Add the human wording and the app-side safety note to a discovery row."""
    d = dict(row)
    if d["ai_status"] is None:
        d["suggestion_text"], d["warning"] = None, None
        return d
    d["suggestion_text"] = suggestion_text(d["ai_identification"], d["ai_confidence"], d["ai_status"])
    if d["status"] == Status.CONFIRMED.value:
        d["warning"] = safety.warning_text(d["final_category"], "High", "ok")
    else:
        d["warning"] = safety.warning_text(d["ai_category"], d["ai_confidence"], d["ai_status"])
    return d


def get_discovery(conn: sqlite3.Connection, discovery_id: int) -> dict:
    row = conn.execute("SELECT * FROM discoveries WHERE id = ?", (discovery_id,)).fetchone()
    if row is None:
        raise KeyError(f"No discovery {discovery_id}")
    return _present(row)


def walk_view(conn: sqlite3.Connection, walk_id: int) -> dict:
    walk = get_walk(conn, walk_id)
    if walk is None:
        raise KeyError(f"No walk {walk_id}")
    rows = conn.execute(
        "SELECT * FROM discoveries WHERE walk_id = ? ORDER BY captured_at, id", (walk_id,)
    ).fetchall()
    return {"walk": walk, "discoveries": [_present(r) for r in rows]}


def identify_discovery(conn, cfg: Config, discovery_id: int, identify_fn=None) -> dict:
    """Run the local model on one photo (also used for "Try Again")."""
    d = get_discovery(conn, discovery_id)
    if d["status"] == Status.CONFIRMED.value:
        raise ValueError("Already confirmed. Photos you confirmed are not re-identified.")
    prompt, phash = load_prompt(cfg.resolved_prompt())
    if isinstance(identify_fn, IdentifyResult):
        res = identify_fn
    else:
        fn = identify_fn or _identify
        res = fn(
            cfg.data_dir / d["photo_path"],
            cfg.model,
            prompt,
            phash,
            {"max_side": cfg.identify_max_side},
            cfg.ollama_url,
        )
    if res.status == "error":
        raise IdentifyError(res.error or "identification failed")

    # Unsafe reassurance is dropped before it can be shown ("safe to eat", "harmless"...).
    fact, explanation = res.fact, res.explanation
    blocked = bool(safety.unsafe_reassurance(fact) or safety.unsafe_reassurance(explanation))
    if safety.unsafe_reassurance(fact):
        fact = ""
    if safety.unsafe_reassurance(explanation):
        explanation = ""

    conn.execute(
        "UPDATE discoveries SET status = ?, ai_status = ?, ai_category = ?, ai_identification = ?, "
        "ai_confidence = ?, ai_explanation = ?, ai_fact = ?, ai_fact_blocked = ?, ai_model = ?, "
        "ai_settings = ?, ai_prompt_hash = ?, ai_time_s = ? WHERE id = ?",
        (Status.IDENTIFIED.value, res.status, res.category, res.identification, res.confidence,
         explanation, fact, int(blocked), res.model, json.dumps(res.settings, sort_keys=True),
         res.prompt_hash, res.elapsed_s, discovery_id),
    )
    conn.commit()
    return get_discovery(conn, discovery_id)


def identify_walk(conn, cfg: Config, walk_id: int, identify_fn=None, only_new: bool = True,
                  progress=None) -> dict:
    """Batch identification. One failure never stops the batch."""
    q = "SELECT id FROM discoveries WHERE walk_id = ?"
    if only_new:
        q += " AND status = 'imported'"
    else:
        q += " AND status != 'confirmed'"
    ids = [r["id"] for r in conn.execute(q + " ORDER BY captured_at, id", (walk_id,))]
    done, errors = 0, []
    for n, did in enumerate(ids, 1):
        try:
            identify_discovery(conn, cfg, did, identify_fn)
            done += 1
        except IdentifyError as e:
            errors.append((did, str(e)))
        if progress:
            progress(n, len(ids))
    return {"identified": done, "errors": errors, "total": len(ids)}


def confirm(conn, discovery_id: int) -> dict:
    """Accept the model's suggestion. Only possible for a High/Medium suggestion."""
    d = get_discovery(conn, discovery_id)
    if d["ai_status"] != "ok" or not d["ai_identification"] or d["ai_confidence"] not in ("High", "Medium"):
        raise ValueError("There is no suggestion to confirm. Correct it, save it unidentified, or try again.")
    conn.execute(
        "UPDATE discoveries SET status = ?, final_label = ?, final_category = ?, final_source = 'confirmed' "
        "WHERE id = ?",
        (Status.CONFIRMED.value, d["ai_identification"], d["ai_category"], discovery_id),
    )
    conn.commit()
    return get_discovery(conn, discovery_id)


def correct(conn, discovery_id: int, label: str, category: str | None = None) -> dict:
    """The user says what it really is. Counts as confirmed (by the user)."""
    d = get_discovery(conn, discovery_id)
    label = (label or "").strip()
    if not label:
        raise ValueError("Please type what it is.")
    if len(label) > 120:
        raise ValueError("That name is too long (120 characters max).")
    if category is None:
        category = d["ai_category"] if d["ai_category"] in USER_CATEGORIES else "other"
    if category not in USER_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(USER_CATEGORIES)}")
    conn.execute(
        "UPDATE discoveries SET status = ?, final_label = ?, final_category = ?, final_source = 'corrected' "
        "WHERE id = ?",
        (Status.CONFIRMED.value, label, category, discovery_id),
    )
    conn.commit()
    return get_discovery(conn, discovery_id)


def _clear_final(conn, discovery_id: int, status: Status) -> dict:
    get_discovery(conn, discovery_id)  # KeyError if missing
    conn.execute(
        "UPDATE discoveries SET status = ?, final_label = NULL, final_category = NULL, final_source = NULL "
        "WHERE id = ?",
        (status.value, discovery_id),
    )
    conn.commit()
    return get_discovery(conn, discovery_id)


def reject(conn, discovery_id: int) -> dict:
    """"Not This": the suggestion was wrong and the user does not want to name it."""
    return _clear_final(conn, discovery_id, Status.REJECTED)


def save_unidentified(conn, discovery_id: int) -> dict:
    """"Save Anyway": keep the photo without an identification."""
    return _clear_final(conn, discovery_id, Status.SAVED_UNIDENTIFIED)


def set_note(conn, discovery_id: int, note: str) -> dict:
    get_discovery(conn, discovery_id)
    conn.execute("UPDATE discoveries SET user_note = ? WHERE id = ?", ((note or "").strip()[:2000], discovery_id))
    conn.commit()
    return get_discovery(conn, discovery_id)


def set_captured_at(conn, discovery_id: int, iso_datetime: str) -> dict:
    """Let the user fix the capture date (transfer apps often strip metadata)."""
    dt = datetime.fromisoformat(iso_datetime)               # ValueError if malformed
    get_discovery(conn, discovery_id)
    conn.execute(
        "UPDATE discoveries SET captured_at = ?, captured_source = 'user' WHERE id = ?",
        (dt.isoformat(timespec="seconds"), discovery_id),
    )
    conn.commit()
    return get_discovery(conn, discovery_id)


# ------------------------------------------------------------------ collection

def collection_counts(conn: sqlite3.Connection) -> dict:
    """Plain record of confirmed discoveries by category. Informational only:
    no percentage, target, ranking or progress anywhere."""
    rows = conn.execute(
        "SELECT final_category AS category, COUNT(*) AS n FROM discoveries "
        "WHERE status = 'confirmed' GROUP BY final_category ORDER BY n DESC, category"
    ).fetchall()
    by_cat = {r["category"]: r["n"] for r in rows}
    return {"total": sum(by_cat.values()), "by_category": by_cat}