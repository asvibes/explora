"""HTTP tests for the journal and Firsts routes (Step 1)."""
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


def test_empty_spread_is_valid(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    j = c.get(f"/api/walks/{w}/journal").json()
    assert j["left_page"] == {"image": None, "rotation": 0, "url": None}
    assert [p["slot"] for p in j["polaroids"]] == [1, 2]
    assert all(not p["removed"] and p["caption"] == "" and p["discovery_id"] is None for p in j["polaroids"])
    assert j["previous_walk_id"] is None and j["next_walk_id"] is None


def test_missing_walk_is_404(tmp_path):
    c = make_client(make_cfg(tmp_path))
    assert c.get("/api/walks/999/journal").status_code == 404
    assert c.get("/api/walks/999/journal/left-page").status_code == 404
    assert c.get("/api/walks/999/firsts").status_code == 404


def test_neighbors_by_date(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    a, b, d = new_walk(cfg, "2026-10-09"), new_walk(cfg, "2026-10-10"), new_walk(cfg, "2026-10-11")
    j = c.get(f"/api/walks/{b}/journal").json()
    assert (j["previous_walk_id"], j["next_walk_id"]) == (a, d)


def test_left_page_upload_serve_and_persist(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    r = c.put(f"/api/walks/{w}/journal/left-page", files={"file": ("page.jpg", make_jpeg("2024:01:02 03:04:05"), "image/jpeg")})
    assert r.status_code == 200
    url = r.json()["left_page"]["url"]
    assert url.startswith(f"/api/walks/{w}/journal/left-page?v=")
    img = c.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert len(Image.open(io.BytesIO(img.content)).getexif()) == 0          # metadata is dropped
    c2 = make_client(cfg)                                                     # a fresh server, same data folder
    assert c2.get(f"/api/walks/{w}/journal").json()["left_page"]["url"] == url
    assert c2.get(url).status_code == 200


def test_left_page_rejects_non_image(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    r = c.put(f"/api/walks/{w}/journal/left-page", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert c.get(f"/api/walks/{w}/journal").json()["left_page"]["image"] is None


def test_rotate_remove_and_repeat(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    assert c.post(f"/api/walks/{w}/journal/left-page/rotate", json={"degrees": 90}).status_code == 400   # no page yet
    c.put(f"/api/walks/{w}/journal/left-page", files={"file": ("p.jpg", make_jpeg(), "image/jpeg")})
    assert c.post(f"/api/walks/{w}/journal/left-page/rotate", json={"degrees": 90}).json()["left_page"]["rotation"] == 90
    assert c.post(f"/api/walks/{w}/journal/left-page/rotate", json={"degrees": -90}).json()["left_page"]["rotation"] == 0
    assert c.post(f"/api/walks/{w}/journal/left-page/rotate", json={"degrees": 45}).status_code == 400
    assert c.post(f"/api/walks/{w}/journal/left-page/rotate", json={"degrees": 0}).status_code == 400
    r = c.delete(f"/api/walks/{w}/journal/left-page")
    assert r.status_code == 200 and r.json()["left_page"]["url"] is None
    assert c.get(f"/api/walks/{w}/journal/left-page").status_code == 404
    assert c.delete(f"/api/walks/{w}/journal/left-page").status_code == 200        # safe to repeat


def test_replace_page_keeps_one_file(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    c.put(f"/api/walks/{w}/journal/left-page", files={"file": ("a.jpg", make_jpeg(), "image/jpeg")})
    c.put(f"/api/walks/{w}/journal/left-page", files={"file": ("b.jpg", make_jpeg(), "image/jpeg")})
    assert len(list((cfg.data_dir / "journal" / str(w)).glob("page_*.jpg"))) == 1


def test_polaroids_photo_caption_remove_restore(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w, other = new_walk(cfg, "2026-10-11"), new_walk(cfg, "2026-10-10")
    p1, p2, foreign = add_photo(c, w), add_photo(c, w), add_photo(c, other)
    base = f"/api/walks/{w}/journal/polaroids"
    j = c.put(f"{base}/1/photo", json={"discovery_id": p1}).json()
    assert j["polaroids"][0]["discovery_id"] == p1 and j["polaroids"][0]["tilt"] is not None
    assert c.put(f"{base}/2/photo", json={"discovery_id": p1}).status_code == 400      # already on the page
    assert c.put(f"{base}/2/photo", json={"discovery_id": foreign}).status_code == 400  # other walk
    assert c.put(f"{base}/2/photo", json={"discovery_id": 99999}).status_code == 404
    assert c.put(f"{base}/3/photo", json={"discovery_id": p2}).status_code == 400      # no such slot
    assert c.put(f"{base}/1/caption", json={"caption": "first leaf"}).json()["polaroids"][0]["caption"] == "first leaf"
    too_long = c.put(f"{base}/1/caption", json={"caption": "x" * 201})
    assert too_long.status_code == 400
    assert c.get(f"/api/walks/{w}/journal").json()["polaroids"][0]["caption"] == "first leaf"   # never silently cut
    j = c.delete(f"{base}/1").json()
    assert j["polaroids"][0]["removed"] is True and j["polaroids"][0]["caption"] == ""
    assert c.put(f"{base}/1/caption", json={"caption": "nope"}).status_code == 400    # removed slot
    j = c.post(f"{base}/1/restore").json()
    assert j["polaroids"][0]["removed"] is False and j["polaroids"][0]["discovery_id"] is None


def test_polaroid_tilt_is_stable_and_choices_persist(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w = new_walk(cfg, "2026-10-11")
    p1 = add_photo(c, w)
    c.put(f"/api/walks/{w}/journal/polaroids/1/photo", json={"discovery_id": p1})
    c.put(f"/api/walks/{w}/journal/polaroids/1/caption", json={"caption": "still wet"})
    first = c.get(f"/api/walks/{w}/journal").json()["polaroids"]
    again = make_client(cfg).get(f"/api/walks/{w}/journal").json()["polaroids"]
    assert [(p["tilt"], p["caption"], p["discovery_id"]) for p in first] ==            [(p["tilt"], p["caption"], p["discovery_id"]) for p in again]
    assert all(abs(p["tilt"]) <= 3.0 for p in again)


def test_firsts_routes(tmp_path):
    cfg = make_cfg(tmp_path)
    c = make_client(cfg)
    w, w2 = new_walk(cfg, "2026-10-10"), new_walk(cfg, "2026-10-11")
    bird1, bird2, thing = add_photo(c, w), add_photo(c, w2), add_photo(c, w2)
    assert c.get("/api/firsts").json() == {"firsts": []}
    c.post(f"/api/discoveries/{bird1}/correct", json={"label": "Blue Jay", "category": "bird"})
    c.post(f"/api/discoveries/{bird2}/correct", json={"label": "Robin", "category": "bird"})
    c.post(f"/api/discoveries/{thing}/correct", json={"label": "A thing", "category": "other"})
    allf = c.get("/api/firsts").json()["firsts"]
    assert [f["text"] for f in allf] == ["First bird"] and allf[0]["discovery_id"] == bird1   # one per category, 'other' never
    assert [f["text"] for f in c.get(f"/api/walks/{w}/firsts").json()["firsts"]] == ["First bird"]
    assert c.get(f"/api/walks/{w2}/firsts").json() == {"firsts": []}