"""Tests for server/journal.py. Self-contained: photos are imported with the real importer."""
import io
import itertools

import pytest
from PIL import Image

from server import journal, walks
from server import importer
from server.config import Config
from server.db import connect

_colors = itertools.count(1)


@pytest.fixture
def jenv(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    conn = connect(cfg)
    yield cfg, conn
    conn.close()


def _jpeg(size=(64, 48), color=None, exif=False) -> bytes:
    n = next(_colors)
    color = color or (n % 256, (n * 7) % 256, (n * 13) % 256)
    buf = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[306] = "2026:10:10 10:00:00"
        ex[0x010F] = "TestMake"
        kw["exif"] = ex.tobytes()
    Image.new("RGB", size, color).save(buf, "JPEG", **kw)
    return buf.getvalue()


def _discovery(conn, cfg, walk_id):
    res = importer.import_bytes(conn, cfg, walk_id, _jpeg(), f"p{next(_colors)}.jpg")
    assert res.status == "added", res
    return res.discovery_id


def _walk(conn, day="2026-10-10"):
    return walks.get_or_create_walk(conn, day)["id"]


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ the empty spread

def test_walk_with_no_journal_has_a_valid_empty_spread_and_nothing_is_written(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    spread = journal.get_journal(conn, wid)

    assert spread["walk"]["id"] == wid
    assert spread["left_page"] == {"image": None, "rotation": 0}
    assert [p["slot"] for p in spread["polaroids"]] == [1, 2]
    for p in spread["polaroids"]:
        assert p["removed"] is False
        assert p["caption"] == ""
        assert p["discovery"] is None and p["discovery_id"] is None
        assert isinstance(p["tilt"], float)
    assert _count(conn, "journal_pages") == 0 and _count(conn, "polaroids") == 0


def test_missing_walk_is_key_error_everywhere(jenv):
    cfg, conn = jenv
    calls = [
        lambda: journal.get_journal(conn, 99),
        lambda: journal.neighbors(conn, 99),
        lambda: journal.left_page_path(conn, cfg, 99),
        lambda: journal.set_left_page(conn, cfg, 99, _jpeg()),
        lambda: journal.rotate_left_page(conn, 99),
        lambda: journal.remove_left_page(conn, cfg, 99),
        lambda: journal.set_polaroid_photo(conn, 99, 1, 1),
        lambda: journal.set_caption(conn, 99, 1, "x"),
        lambda: journal.remove_polaroid(conn, 99, 1),
        lambda: journal.restore_polaroid(conn, 99, 1),
    ]
    for call in calls:
        with pytest.raises(KeyError):
            call()
    assert _count(conn, "journal_pages") == 0 and _count(conn, "polaroids") == 0


@pytest.mark.parametrize("bad", [None, "1", 1.0, True])
def test_bad_walk_id_is_value_error(jenv, bad):
    _, conn = jenv
    with pytest.raises(ValueError):
        journal.get_journal(conn, bad)


# ------------------------------------------------------------------ left page

def test_upload_stores_a_clean_copy_inside_the_journal_folder(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    spread = journal.set_left_page(conn, cfg, wid, _jpeg(exif=True))

    rel = spread["left_page"]["image"]
    assert rel.startswith(f"journal/{wid}/") and rel.endswith(".jpg")
    path = cfg.data_dir / rel
    assert path.is_file() and cfg.journal_dir.resolve() in path.resolve().parents
    with Image.open(path) as im:
        assert im.format == "JPEG"
        assert len(im.getexif()) == 0          # EXIF (and any GPS) never reaches the data folder
    assert spread["left_page"]["rotation"] == 0
    assert journal.left_page_path(conn, cfg, wid) == path.resolve()


def test_large_page_is_shrunk(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    spread = journal.set_left_page(conn, cfg, wid, _jpeg(size=(3000, 1500)))
    with Image.open(cfg.data_dir / spread["left_page"]["image"]) as im:
        assert max(im.size) == journal.PAGE_MAX_SIDE


def test_replace_removes_old_file_and_resets_rotation(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    first = journal.set_left_page(conn, cfg, wid, _jpeg())
    journal.rotate_left_page(conn, wid, 90)
    old = cfg.data_dir / first["left_page"]["image"]

    second = journal.set_left_page(conn, cfg, wid, _jpeg())
    new = cfg.data_dir / second["left_page"]["image"]

    assert new != old and new.is_file() and not old.exists()
    assert second["left_page"]["rotation"] == 0
    assert _count(conn, "journal_pages") == 1


def test_uploading_the_same_photo_again_keeps_the_file(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    data = _jpeg()
    first = journal.set_left_page(conn, cfg, wid, data)
    again = journal.set_left_page(conn, cfg, wid, data)
    assert again["left_page"]["image"] == first["left_page"]["image"]
    assert (cfg.data_dir / again["left_page"]["image"]).is_file()


@pytest.mark.parametrize("bad", [b"", b"definitely not an image", None, "text", 5])
def test_unreadable_upload_is_refused_and_leaves_nothing(jenv, bad):
    cfg, conn = jenv
    wid = _walk(conn)
    with pytest.raises(ValueError):
        journal.set_left_page(conn, cfg, wid, bad)
    assert _count(conn, "journal_pages") == 0
    assert not list(cfg.journal_dir.rglob("*.jpg"))


def test_failed_replace_keeps_the_existing_page(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    good = journal.set_left_page(conn, cfg, wid, _jpeg())
    with pytest.raises(ValueError):
        journal.set_left_page(conn, cfg, wid, b"nope")
    assert journal.get_journal(conn, wid)["left_page"]["image"] == good["left_page"]["image"]
    assert (cfg.data_dir / good["left_page"]["image"]).is_file()


def test_rotate_adds_up_modulo_360_and_never_touches_the_file(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    spread = journal.set_left_page(conn, cfg, wid, _jpeg())
    path = cfg.data_dir / spread["left_page"]["image"]
    before = path.read_bytes()

    rots = [journal.rotate_left_page(conn, wid, d)["left_page"]["rotation"] for d in (90, 90, 90, 90, -90, 180)]
    assert rots == [90, 180, 270, 0, 270, 90]
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", [0, 45, 100, "90", 90.0, True, None])
def test_rotate_rejects_bad_degrees(jenv, bad):
    cfg, conn = jenv
    wid = _walk(conn)
    journal.set_left_page(conn, cfg, wid, _jpeg())
    with pytest.raises(ValueError):
        journal.rotate_left_page(conn, wid, bad)


def test_rotate_needs_a_page(jenv):
    _, conn = jenv
    wid = _walk(conn)
    with pytest.raises(ValueError, match="no page"):
        journal.rotate_left_page(conn, wid)
    assert _count(conn, "journal_pages") == 0


def test_remove_page_deletes_file_and_is_repeatable(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    spread = journal.set_left_page(conn, cfg, wid, _jpeg())
    path = cfg.data_dir / spread["left_page"]["image"]

    gone = journal.remove_left_page(conn, cfg, wid)
    assert gone["left_page"] == {"image": None, "rotation": 0}
    assert not path.exists() and journal.left_page_path(conn, cfg, wid) is None
    journal.remove_left_page(conn, cfg, wid)           # nothing to remove: still fine


def test_left_page_path_refuses_paths_outside_the_journal_folder(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    conn.execute("INSERT INTO journal_pages(walk_id, left_image) VALUES(?, ?)", (wid, "../../etc/passwd"))
    conn.commit()
    assert journal.left_page_path(conn, cfg, wid) is None
    # and removing never deletes outside the folder either
    outside = cfg.data_dir / "photos" / "keep.jpg"
    outside.write_bytes(b"x")
    conn.execute("UPDATE journal_pages SET left_image = ? WHERE walk_id = ?", ("photos/keep.jpg", wid))
    conn.commit()
    journal.remove_left_page(conn, cfg, wid)
    assert outside.exists()


# ------------------------------------------------------------------ Polaroids

def test_put_a_photo_in_a_slot_and_keep_discovery_details(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    walks.correct(conn, did, "Blue Jay", "bird")
    walks.set_note(conn, did, "Loud one by the gate")

    spread = journal.set_polaroid_photo(conn, wid, 1, did)
    p1, p2 = spread["polaroids"]

    assert p1["discovery_id"] == did
    d = p1["discovery"]
    assert d["final_label"] == "Blue Jay"
    assert d["user_note"] == "Loud one by the gate"
    assert d["captured_at"] and d["photo_path"]
    assert p2["discovery"] is None and p2["removed"] is False


def test_unidentified_photos_can_be_used(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)                    # status "imported": never identified
    assert journal.set_polaroid_photo(conn, wid, 2, did)["polaroids"][1]["discovery_id"] == did


def test_photo_must_belong_to_this_walk(jenv):
    cfg, conn = jenv
    w1, w2 = _walk(conn, "2026-10-01"), _walk(conn, "2026-10-02")
    other = _discovery(conn, cfg, w2)
    with pytest.raises(ValueError, match="different walk"):
        journal.set_polaroid_photo(conn, w1, 1, other)
    with pytest.raises(KeyError):
        journal.set_polaroid_photo(conn, w1, 1, 12345)
    assert _count(conn, "polaroids") == 0


def test_same_photo_cannot_fill_both_slots_but_can_replace_itself(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    a, b = _discovery(conn, cfg, wid), _discovery(conn, cfg, wid)
    journal.set_polaroid_photo(conn, wid, 1, a)
    with pytest.raises(ValueError, match="already"):
        journal.set_polaroid_photo(conn, wid, 2, a)
    journal.set_polaroid_photo(conn, wid, 1, a)         # same slot, same photo: fine
    assert journal.set_polaroid_photo(conn, wid, 1, b)["polaroids"][0]["discovery_id"] == b
    assert journal.set_polaroid_photo(conn, wid, 2, a)["polaroids"][1]["discovery_id"] == a


@pytest.mark.parametrize("bad", [0, 3, -1, "1", 1.0, True, None])
def test_bad_slot_is_value_error(jenv, bad):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    for call in (lambda: journal.set_polaroid_photo(conn, wid, bad, did),
                 lambda: journal.set_caption(conn, wid, bad, "x"),
                 lambda: journal.remove_polaroid(conn, wid, bad),
                 lambda: journal.restore_polaroid(conn, wid, bad)):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("bad", [None, "1", 1.5, True])
def test_bad_discovery_id_is_value_error(jenv, bad):
    _, conn = jenv
    wid = _walk(conn)
    with pytest.raises(ValueError):
        journal.set_polaroid_photo(conn, wid, 1, bad)


def test_caption_is_trimmed_kept_when_photo_changes_and_can_be_cleared(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    a, b = _discovery(conn, cfg, wid), _discovery(conn, cfg, wid)
    journal.set_polaroid_photo(conn, wid, 1, a)

    spread = journal.set_caption(conn, wid, 1, "  By the pond  ")
    assert spread["polaroids"][0]["caption"] == "By the pond"
    spread = journal.set_polaroid_photo(conn, wid, 1, b)
    assert spread["polaroids"][0]["caption"] == "By the pond"       # words survive a photo swap
    assert journal.set_caption(conn, wid, 1, "")["polaroids"][0]["caption"] == ""
    journal.set_caption(conn, wid, 1, "again")
    assert journal.set_caption(conn, wid, 1, None)["polaroids"][0]["caption"] == ""


def test_caption_works_on_an_empty_slot(jenv):
    _, conn = jenv
    wid = _walk(conn)
    spread = journal.set_caption(conn, wid, 2, "Left blank on purpose")
    assert spread["polaroids"][1]["caption"] == "Left blank on purpose"
    assert spread["polaroids"][1]["discovery"] is None


def test_caption_length_limit_refuses_instead_of_cutting(jenv):
    _, conn = jenv
    wid = _walk(conn)
    journal.set_caption(conn, wid, 1, "x" * journal.MAX_CAPTION)       # exactly the limit is fine
    with pytest.raises(ValueError, match="too long"):
        journal.set_caption(conn, wid, 1, "y" * (journal.MAX_CAPTION + 1))
    assert journal.get_journal(conn, wid)["polaroids"][0]["caption"] == "x" * journal.MAX_CAPTION
    with pytest.raises(ValueError):
        journal.set_caption(conn, wid, 1, 123)


def test_removing_a_slot_clears_it_and_is_repeatable(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    journal.set_polaroid_photo(conn, wid, 1, did)
    journal.set_caption(conn, wid, 1, "gone soon")

    spread = journal.remove_polaroid(conn, wid, 1)
    assert spread["polaroids"][0] == {"slot": 1, "removed": True, "caption": "", "discovery_id": None,
                                      "tilt": None, "discovery": None}
    assert spread["polaroids"][1]["removed"] is False
    journal.remove_polaroid(conn, wid, 1)
    # the photo itself is untouched: it is still in the walk
    assert [d["id"] for d in walks.walk_view(conn, wid)["discoveries"]] == [did]


def test_both_slots_can_be_removed(jenv):
    _, conn = jenv
    wid = _walk(conn)
    journal.remove_polaroid(conn, wid, 1)
    spread = journal.remove_polaroid(conn, wid, 2)
    assert [p["removed"] for p in spread["polaroids"]] == [True, True]


def test_removed_slot_needs_restoring_before_a_caption(jenv):
    _, conn = jenv
    wid = _walk(conn)
    journal.remove_polaroid(conn, wid, 1)
    with pytest.raises(ValueError, match="removed"):
        journal.set_caption(conn, wid, 1, "nope")
    spread = journal.restore_polaroid(conn, wid, 1)
    p = spread["polaroids"][0]
    assert p["removed"] is False and p["caption"] == "" and p["discovery"] is None
    journal.restore_polaroid(conn, wid, 1)                              # repeatable
    journal.restore_polaroid(conn, wid, 2)                              # never removed: harmless
    assert journal.set_caption(conn, wid, 1, "back")["polaroids"][0]["caption"] == "back"


def test_putting_a_photo_in_a_removed_slot_brings_it_back(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    journal.remove_polaroid(conn, wid, 2)
    spread = journal.set_polaroid_photo(conn, wid, 2, did)
    assert spread["polaroids"][1]["removed"] is False
    assert spread["polaroids"][1]["discovery_id"] == did


def test_slots_are_independent_between_walks(jenv):
    cfg, conn = jenv
    w1, w2 = _walk(conn, "2026-10-01"), _walk(conn, "2026-10-02")
    journal.set_caption(conn, w1, 1, "walk one")
    journal.remove_polaroid(conn, w1, 2)
    other = journal.get_journal(conn, w2)
    assert [p["caption"] for p in other["polaroids"]] == ["", ""]
    assert [p["removed"] for p in other["polaroids"]] == [False, False]


def test_deleting_a_photo_empties_the_slot_but_keeps_the_caption(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    journal.set_polaroid_photo(conn, wid, 1, did)
    journal.set_caption(conn, wid, 1, "still mine")
    conn.execute("DELETE FROM discoveries WHERE id = ?", (did,))     # schema: ON DELETE SET NULL
    conn.commit()
    p = journal.get_journal(conn, wid)["polaroids"][0]
    assert p["discovery"] is None and p["discovery_id"] is None and p["caption"] == "still mine"


# ------------------------------------------------------------------ tilt

def test_tilt_is_stable_bounded_and_follows_the_photo(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    did = _discovery(conn, cfg, wid)
    first = journal.set_polaroid_photo(conn, wid, 1, did)["polaroids"][0]["tilt"]

    fresh = connect(cfg)                                              # a "reload": new connection
    try:
        again = journal.get_journal(fresh, wid)["polaroids"][0]["tilt"]
    finally:
        fresh.close()
    assert first == again
    assert -journal.TILT_MAX <= first <= journal.TILT_MAX

    moved = journal.remove_polaroid(conn, wid, 1)                     # clear slot 1, then use slot 2
    moved = journal.set_polaroid_photo(conn, wid, 2, did)["polaroids"][1]["tilt"]
    assert moved == first                                             # same photo, same tilt

    empty = [journal.get_journal(conn, wid)["polaroids"][i]["tilt"] for i in (0,)]
    assert empty[0] is None                                           # removed slot has no tilt


def test_empty_slots_have_a_stable_tilt_too(jenv):
    _, conn = jenv
    wid = _walk(conn)
    a = [p["tilt"] for p in journal.get_journal(conn, wid)["polaroids"]]
    b = [p["tilt"] for p in journal.get_journal(conn, wid)["polaroids"]]
    assert a == b and all(isinstance(t, float) for t in a)


# ------------------------------------------------------------------ navigation

def test_neighbors_follow_dates_not_ids(jenv):
    _, conn = jenv
    mid = _walk(conn, "2026-10-05")
    late = _walk(conn, "2026-10-09")
    early = _walk(conn, "2026-10-01")                                 # created last, dated first

    assert journal.neighbors(conn, mid) == {"previous_walk_id": early, "next_walk_id": late}
    assert journal.neighbors(conn, early) == {"previous_walk_id": None, "next_walk_id": mid}
    assert journal.neighbors(conn, late) == {"previous_walk_id": mid, "next_walk_id": None}
    spread = journal.get_journal(conn, mid)
    assert spread["previous_walk_id"] == early and spread["next_walk_id"] == late


def test_single_walk_has_no_neighbors(jenv):
    _, conn = jenv
    wid = _walk(conn)
    assert journal.neighbors(conn, wid) == {"previous_walk_id": None, "next_walk_id": None}


# ------------------------------------------------------------------ guard rails

def test_no_progress_or_completeness_fields_in_the_spread(jenv):
    cfg, conn = jenv
    wid = _walk(conn)
    journal.set_left_page(conn, cfg, wid, _jpeg())
    spread = journal.get_journal(conn, wid)
    assert set(spread) == {"walk", "left_page", "polaroids", "previous_walk_id", "next_walk_id"}
    assert set(spread["left_page"]) == {"image", "rotation"}
    assert set(spread["polaroids"][0]) == {"slot", "removed", "caption", "discovery_id", "tilt", "discovery"}
    text = repr(spread).lower()
    for word in ("complete", "incomplete", "percent", "streak", "score", "remaining"):
        assert word not in text