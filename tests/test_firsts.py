"""Tests for server/firsts.py. Self-contained: discoveries are made with the real importer
and confirmed with the real walks.confirm/correct code, not by writing rows by hand."""
import io
import itertools

import pytest
from PIL import Image

from server import firsts, importer, walks
from server.config import Config
from server.db import connect

_colors = itertools.count(1)


@pytest.fixture
def fenv(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    conn = connect(cfg)
    yield cfg, conn
    conn.close()


def _discovery(conn, cfg, walk_id):
    """Import one new, unique photo into a walk; returns the discovery id."""
    n = next(_colors)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (n % 256, (n * 7) % 256, (n * 13) % 256)).save(buf, "JPEG")
    res = importer.import_bytes(conn, cfg, walk_id, buf.getvalue(), f"p{n}.jpg")
    assert res.status == "added", res
    return res.discovery_id


def _confirmed(conn, cfg, walk_id, label="Blue Jay", category="bird", clear_first=True):
    did = _discovery(conn, cfg, walk_id)
    walks.correct(conn, did, label, category)
    if clear_first:
        conn.execute("DELETE FROM firsts WHERE discovery_id = ?", (did,))
        conn.commit()
    return did


# ------------------------------------------------------------------ recording

def test_first_confirmed_discovery_earns_the_first(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn, "2026-10-01")
    did = _confirmed(conn, cfg, walk["id"], "Monarch", "butterfly", clear_first=False)

    first = firsts.get_first(conn, "butterfly")

    assert first["category"] == "butterfly"
    assert first["discovery_id"] == did
    assert first["walk_id"] == walk["id"]
    assert first["walk_date"] == "2026-10-01"
    assert first["final_label"] == "Monarch"
    assert first["text"] == "First butterfly"
    assert first["created_at"]


def test_confirm_path_works_too(fenv):
    """walks.confirm (accepting the model's suggestion) must earn a First like correct does."""
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _discovery(conn, cfg, walk["id"])
    conn.execute(
        "UPDATE discoveries SET status='identified', ai_status='ok', ai_category='tree', "
        "ai_identification='an oak', ai_confidence='High' WHERE id = ?", (did,))
    conn.commit()
    walks.confirm(conn, did)
    assert firsts.get_first(conn, "tree")["category"] == "tree"


def test_later_discovery_never_overwrites_a_first(fenv):
    cfg, conn = fenv
    w1 = walks.get_or_create_walk(conn, "2026-10-01")
    w2 = walks.get_or_create_walk(conn, "2026-10-02")
    d1 = _confirmed(conn, cfg, w1["id"], "Blue Jay", "bird")
    d2 = _confirmed(conn, cfg, w2["id"], "Robin", "bird")

    original = firsts.record_first(conn, d1)
    assert firsts.record_first(conn, d2) is None

    kept = firsts.get_first(conn, "bird")
    assert kept == original
    assert kept["discovery_id"] == d1
    assert kept["walk_id"] == w1["id"]
    assert len(firsts.list_firsts(conn)) == 1


def test_recording_the_same_discovery_twice_is_a_no_op(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _confirmed(conn, cfg, walk["id"])
    assert firsts.record_first(conn, did) is not None
    assert firsts.record_first(conn, did) is None
    assert len(firsts.list_firsts(conn)) == 1


def test_each_category_gets_its_own_first(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    for label, cat in (("Blue Jay", "bird"), ("Rose", "flower"), ("Quartz", "rock")):
        assert firsts.record_first(conn, _confirmed(conn, cfg, walk["id"], label, cat)) is not None
    assert [f["category"] for f in firsts.list_firsts(conn)] == ["bird", "flower", "rock"]


def test_second_connection_cannot_overwrite(fenv):
    """The database, not just the Python check, protects an existing First."""
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    d1 = _confirmed(conn, cfg, walk["id"], "Blue Jay", "bird")
    d2 = _confirmed(conn, cfg, walk["id"], "Robin", "bird")
    other = connect(cfg)
    try:
        assert firsts.record_first(other, d1) is not None
        assert firsts.record_first(conn, d2) is None
    finally:
        other.close()
    assert firsts.get_first(conn, "bird")["discovery_id"] == d1


def test_other_never_earns_a_first(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _confirmed(conn, cfg, walk["id"], "Something odd", "other")
    assert firsts.record_first(conn, did) is None
    assert firsts.list_firsts(conn) == []


# ------------------------------------------------------------------ not allowed

@pytest.mark.parametrize("how", ["imported", "rejected", "saved_unidentified"])
def test_unconfirmed_discoveries_cannot_earn_a_first(fenv, how):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _discovery(conn, cfg, walk["id"])
    if how == "rejected":
        walks.reject(conn, did)
    elif how == "saved_unidentified":
        walks.save_unidentified(conn, did)
    with pytest.raises(ValueError, match="confirmed"):
        firsts.record_first(conn, did)
    assert firsts.list_firsts(conn) == []


def test_missing_discovery_is_key_error(fenv):
    _, conn = fenv
    with pytest.raises(KeyError):
        firsts.record_first(conn, 9999)


@pytest.mark.parametrize("bad", [None, "1", 1.5, True, [1]])
def test_bad_discovery_id_is_value_error(fenv, bad):
    _, conn = fenv
    with pytest.raises(ValueError):
        firsts.record_first(conn, bad)


def test_confirmed_row_with_invalid_category_is_refused(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _confirmed(conn, cfg, walk["id"])
    conn.execute("UPDATE discoveries SET final_category = 'unknown' WHERE id = ?", (did,))
    conn.commit()
    with pytest.raises(ValueError, match="category"):
        firsts.record_first(conn, did)
    conn.execute("UPDATE discoveries SET final_category = NULL WHERE id = ?", (did,))
    conn.commit()
    with pytest.raises(ValueError):
        firsts.record_first(conn, did)


# ------------------------------------------------------------------ reading

def test_get_first_is_none_before_one_exists(fenv):
    _, conn = fenv
    assert firsts.get_first(conn, "bird") is None
    assert firsts.list_firsts(conn) == []


def test_list_firsts_is_oldest_first(fenv):
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    a = _confirmed(conn, cfg, walk["id"], "Rose", "flower")
    b = _confirmed(conn, cfg, walk["id"], "Blue Jay", "bird")
    firsts.record_first(conn, a)
    firsts.record_first(conn, b)
    assert [f["category"] for f in firsts.list_firsts(conn)] == ["flower", "bird"]


def test_firsts_for_walk_only_lists_that_walk(fenv):
    cfg, conn = fenv
    w1 = walks.get_or_create_walk(conn, "2026-10-01")
    w2 = walks.get_or_create_walk(conn, "2026-10-02")
    firsts.record_first(conn, _confirmed(conn, cfg, w1["id"], "Blue Jay", "bird"))
    firsts.record_first(conn, _confirmed(conn, cfg, w2["id"], "Rose", "flower"))
    assert [f["category"] for f in firsts.firsts_for_walk(conn, w1["id"])] == ["bird"]
    assert [f["category"] for f in firsts.firsts_for_walk(conn, w2["id"])] == ["flower"]


def test_firsts_for_walk_errors(fenv):
    _, conn = fenv
    with pytest.raises(KeyError):
        firsts.firsts_for_walk(conn, 4242)
    with pytest.raises(ValueError):
        firsts.firsts_for_walk(conn, "1")


def test_a_first_is_kept_if_its_discovery_is_later_changed(fenv):
    """A First is a memory: rejecting the photo afterwards does not erase or move it."""
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _confirmed(conn, cfg, walk["id"])
    firsts.record_first(conn, did)
    walks.reject(conn, did)
    kept = firsts.get_first(conn, "bird")
    assert kept["discovery_id"] == did
    assert kept["final_label"] is None


def test_first_is_removed_with_its_discovery(fenv):
    """Schema rule (ON DELETE CASCADE): deleting the discovery deletes its First."""
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    did = _confirmed(conn, cfg, walk["id"])
    firsts.record_first(conn, did)
    conn.execute("DELETE FROM discoveries WHERE id = ?", (did,))
    conn.commit()
    assert firsts.get_first(conn, "bird") is None


def test_no_progress_or_achievement_fields(fenv):
    """Firsts are memories, not achievements: no counters, points, streaks, targets."""
    cfg, conn = fenv
    walk = walks.get_or_create_walk(conn)
    first = firsts.record_first(conn, _confirmed(conn, cfg, walk["id"]))
    assert set(first) == {"id", "category", "discovery_id", "walk_id", "created_at",
                          "photo_path", "final_label", "captured_at", "walk_date", "text"}
    assert "next" not in first["text"].lower()
    assert "%" not in first["text"]