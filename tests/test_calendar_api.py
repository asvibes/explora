"""HTTP tests for calendar thumbnails (Step 1): the choice order and that the person's pick persists."""
import pytest

import io
import itertools
from datetime import datetime

from fastapi.testclient import TestClient
from PIL import Image

from server import walks
from server.config import Config
from server.db import connect
from server.main import create_app

_n = itertools.count(1)


def make_jpeg(exif_datetime=None) -> bytes:
    """A small JPEG, different bytes every call (so it is never a duplicate)."""
    n = next(_n)
    im = Image.new("RGB", (64, 48), ((n * 37) % 256, (n * 91) % 256, (n * 53) % 256))
    buf = io.BytesIO()
    if exif_datetime:
        exif = Image.Exif()
        exif[306] = exif_datetime
        im.save(buf, "JPEG", exif=exif)
    else:
        im.save(buf, "JPEG")
    return buf.getvalue()


def make_client(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg, allowed_hosts=["testserver"]))


def make_cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path / "data")


def new_walk(cfg: Config, iso: str) -> int:
    conn = connect(cfg)
    try:
        return walks.get_or_create_walk(conn, iso)["id"]
    finally:
        conn.close()


def add_photo(c: TestClient, walk_id: int, **kw) -> int:
    r = c.post(f"/api/walks/{walk_id}/photos", files={"files": ("p.jpg", make_jpeg(**kw), "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["discovery_id"]


def _month(c, ym="2026-10"):
    return c.get(f"/api/calendar/{ym}").json()["walks"]


def _entry(c, walk_id, ym="2026-10"):
    return next(w for w in _month(c, ym) if w["id"] == walk_id)


@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "abc", "2026-00", "26-10"])
def test_bad_month_is_400(tmp_path, bad):
    assert make_client(make_cfg(tmp_path)).get(f"/api/calendar/{bad}").status_code == 400


def test_month_lists_only_that_month_in_order(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    sep, a, b = new_walk(cfg, "2026-09-30"), new_walk(cfg, "2026-10-04"), new_walk(cfg, "2026-10-11")
    nov = new_walk(cfg, "2026-11-01")
    assert [w["id"] for w in _month(c)] == [a, b]
    assert [w["id"] for w in _month(c, "2026-09")] == [sep]
    assert [w["id"] for w in _month(c, "2026-12")] == []
    assert nov not in [w["id"] for w in _month(c)]


def test_walk_without_photos_has_no_thumbnail(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    e = _entry(c, w)
    assert e["photo_count"] == 0 and e["thumbnail_discovery_id"] is None and e["thumbnail_custom"] is False


def test_choice_order(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    p1, p2, p3 = add_photo(c, w), add_photo(c, w), add_photo(c, w)
    e = _entry(c, w)
    assert (e["thumbnail_discovery_id"], e["thumbnail_custom"], e["photo_count"]) == (p1, False, 3)   # first photo
    c.post(f"/api/discoveries/{p2}/correct", json={"label": "Sunflower", "category": "flower"})
    assert _entry(c, w)["thumbnail_discovery_id"] == p2                                               # first confirmed
    c.put(f"/api/walks/{w}/journal/polaroids/1/photo", json={"discovery_id": p3})
    assert _entry(c, w)["thumbnail_discovery_id"] == p3                                               # Polaroid slot 1
    c.delete(f"/api/walks/{w}/journal/polaroids/1")
    assert _entry(c, w)["thumbnail_discovery_id"] == p2                                               # slot removed
    c.put(f"/api/walks/{w}/journal/polaroids/1/photo", json={"discovery_id": p3})                     # (restores it)
    r = c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": p1})
    assert r.status_code == 200 and r.json() == {"walk_id": w, "thumbnail_discovery_id": p1, "thumbnail_custom": True}
    c.put(f"/api/walks/{w}/journal/polaroids/2/photo", json={"discovery_id": p2})
    e = _entry(c, w)
    assert (e["thumbnail_discovery_id"], e["thumbnail_custom"]) == (p1, True)                         # the pick wins
    c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": p3})
    assert _entry(c, w)["thumbnail_discovery_id"] == p3                                               # until changed again


def test_pick_survives_restart(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    add_photo(c, w)
    p2 = add_photo(c, w)
    c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": p2})
    e = _entry(make_client(cfg), w)                      # a brand-new app on the same data folder
    assert (e["thumbnail_discovery_id"], e["thumbnail_custom"]) == (p2, True)


def test_thumbnail_validation(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w, other = new_walk(cfg, "2026-10-11"), new_walk(cfg, "2026-10-10")
    mine, foreign = add_photo(c, w), add_photo(c, other)
    assert c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": foreign}).status_code == 400
    assert c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": 99999}).status_code == 404
    assert c.put("/api/walks/999/thumbnail", json={"discovery_id": mine}).status_code == 404
    assert c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": "x"}).status_code == 422
    assert _entry(c, w)["thumbnail_custom"] is False


def test_pick_is_dropped_if_its_photo_is_deleted(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    p1, p2 = add_photo(c, w), add_photo(c, w)
    c.put(f"/api/walks/{w}/thumbnail", json={"discovery_id": p2})
    conn = connect(cfg)
    conn.execute("DELETE FROM discoveries WHERE id = ?", (p2,))
    conn.commit()
    conn.close()
    e = _entry(c, w)
    assert (e["thumbnail_discovery_id"], e["thumbnail_custom"]) == (p1, False)     # falls back, no error