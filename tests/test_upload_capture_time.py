"""Photo import keeps the browser's file date when the photo has no EXIF (Step 1 fix)."""
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


import pytest

MS = 1_700_000_000_000                                   # a plausible File.lastModified


def _disc(c, walk_id, discovery_id):
    return next(d for d in c.get(f"/api/walks/{walk_id}").json()["discoveries"] if d["id"] == discovery_id)


def _upload(c, w, data, ms=None, name="p.jpg"):
    r = c.post(f"/api/walks/{w}/photos", files={"files": (name, data, "image/jpeg")},
               data={"captured_ms": ms} if ms is not None else None)
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def test_file_date_is_used_when_no_exif(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    res = _upload(c, w, make_jpeg(), str(MS))
    assert res["captured_source"] == "file"
    assert res["captured_at"] == datetime.fromtimestamp(MS / 1000).replace(microsecond=0).isoformat(timespec="seconds")
    assert _disc(c, w, res["discovery_id"])["captured_at"] == res["captured_at"]


def test_exif_wins_over_file_date(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    res = _upload(c, w, make_jpeg("2024:05:06 07:08:09"), str(MS))
    assert (res["captured_source"], res["captured_at"]) == ("exif", "2024-05-06T07:08:09")


@pytest.mark.parametrize("ms", ["abc", "", "5", "99999999999999", "-1"])
def test_bad_values_fall_back_to_before(tmp_path, ms):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    res = _upload(c, w, make_jpeg(), ms)
    assert res["captured_source"] == "unknown"           # exactly the behaviour from before the change


def test_no_field_is_unchanged(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    assert _upload(c, w, make_jpeg())["captured_source"] == "unknown"


def test_several_files_match_by_position(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    a, b, d = make_jpeg(), make_jpeg(), make_jpeg()
    r = c.post(f"/api/walks/{w}/photos",
               files=[("files", ("a.jpg", a, "image/jpeg")), ("files", ("b.jpg", b, "image/jpeg")),
                      ("files", ("c.jpg", d, "image/jpeg"))],
               data={"captured_ms": [str(MS), "junk"]})                 # fewer values than files
    res = r.json()["results"]
    assert [x["captured_source"] for x in res] == ["file", "unknown", "unknown"]


def test_duplicate_still_skipped(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    data = make_jpeg()
    assert _upload(c, w, data, str(MS))["status"] == "added"
    assert _upload(c, w, data, str(MS + 5000))["status"] == "duplicate"