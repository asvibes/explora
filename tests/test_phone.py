import io
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.config import ROOT, Config
from server.main import create_app

PHONE = ROOT / "phone"


@pytest.fixture
def client(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", prompt_path=tmp_path / "p.txt")
    return TestClient(create_app(cfg, allowed_hosts=["testserver"]))


def jpeg(seed=1):
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (seed * 50 % 256, 90, 140)).save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- the static files

def test_manifest_is_valid_and_installable():
    m = json.loads((PHONE / "manifest.json").read_text(encoding="utf-8"))
    assert m["name"] and m["short_name"] and m["display"] == "standalone"
    assert m["start_url"].startswith(m["scope"]) and m["scope"] == "/phone/"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in m["icons"]:
        path = PHONE / icon["src"].removeprefix("/phone/")
        assert path.is_file(), icon["src"]
        with Image.open(path) as im:
            assert f"{im.width}x{im.height}" == icon["sizes"]


def test_every_file_the_service_worker_precaches_exists():
    sw = (PHONE / "service-worker.js").read_text(encoding="utf-8")
    shell = re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1)
    urls = re.findall(r'"(/phone/[^"]*)"', shell)
    assert "/phone/" in urls and "/phone/capture-queue.js" in urls
    for u in urls:
        rel = u.removeprefix("/phone/") or "index.html"
        assert (PHONE / rel).is_file(), u


def test_service_worker_never_touches_the_api_or_other_hosts():
    sw = (PHONE / "service-worker.js").read_text(encoding="utf-8")
    assert 'url.pathname.startsWith("/phone")' in sw
    assert 'req.method !== "GET"' in sw and "url.origin !== self.location.origin" in sw
    assert "cache.put" in sw and "/api" not in 'const SHELL' + sw.split("const SHELL")[1].split("];")[0]


def test_host_page_loads_the_manifest_and_queue():
    html = (PHONE / "index.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in html and "/phone/capture-queue.js" in html and "ExploraCapture.mount" in html
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?://""", html, re.I)
    assert not re.search(r"""url\(["']?https?://""", html, re.I)


def test_queue_makes_no_external_requests_and_no_pressure_language():
    code = re.sub(r"/\*.*?\*/", "", (PHONE / "capture-queue.js").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"https?://", code)
    for word in ("streak", "badge", "leaderboard", "incomplete", "progress"):
        assert word not in code.lower()


# ---------------------------------------------------------------- served by the app

def test_phone_app_is_served_with_correct_types(client):
    assert client.get("/phone/").status_code == 200
    sw = client.get("/phone/service-worker.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert "javascript" in client.get("/phone/capture-queue.js").headers["content-type"]
    assert client.get("/phone/manifest.json").status_code == 200
    assert client.get("/phone/icons/icon-192.png").headers["content-type"] == "image/png"


def test_phone_mount_does_not_expose_the_rest_of_the_repo(client):
    assert client.get("/phone/../server/main.py").status_code in (404, 400)
    assert client.get("/phone/..%2fserver%2fmain.py").status_code == 404


# ---------------------------------------------------------------- walk for a given day

def test_walk_for_day_creates_once(client):
    a = client.post("/api/walks", json={"date": "2026-10-08"}).json()
    b = client.post("/api/walks", json={"date": "2026-10-08"}).json()
    assert a["walk"]["date"] == "2026-10-08" and a["walk"]["id"] == b["walk"]["id"]
    other = client.post("/api/walks", json={"date": "2026-10-07"}).json()
    assert other["walk"]["id"] != a["walk"]["id"]
    assert a["discoveries"] == [] and a["quests_done"] == []


def test_walk_for_day_matches_today(client):
    today = client.post("/api/walks/today").json()["walk"]
    same = client.post("/api/walks", json={"date": date.today().isoformat()}).json()["walk"]
    assert today["id"] == same["id"]


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-01", "", "1999-12-31",
                                 (date.today() + timedelta(days=30)).isoformat()])
def test_walk_for_day_rejects_bad_dates(client, bad):
    assert client.post("/api/walks", json={"date": bad}).status_code == 400
    assert client.get("/api/walks").json() == {"walks": []}


def test_walk_for_day_requires_a_date(client):
    assert client.post("/api/walks", json={}).status_code == 422


# ---------------------------------------------------------------- capture time on upload

def upload(client, walk_id, data, **form):
    return client.post(f"/api/walks/{walk_id}/photos", files=[("files", ("p.jpg", data, "image/jpeg"))], data=form)


def test_captured_ms_is_used_when_the_photo_has_no_exif_date(client):
    wid = client.post("/api/walks", json={"date": "2026-10-09"}).json()["walk"]["id"]
    ms = int(datetime(2026, 10, 9, 15, 30, 0).timestamp() * 1000)
    r = upload(client, wid, jpeg(1), captured_ms=str(ms))
    assert r.json()["summary"]["added"] == 1
    d = client.get(f"/api/walks/{wid}").json()["discoveries"][0]
    assert d["captured_at"] == "2026-10-09T15:30:00" and d["captured_source"] == "file"


def test_upload_still_works_without_captured_ms(client):
    wid = client.post("/api/walks/today").json()["walk"]["id"]
    assert upload(client, wid, jpeg(2)).json()["summary"]["added"] == 1


@pytest.mark.parametrize("bad", ["0", "-5", "99999999999999", "abc"])
def test_bad_captured_ms_falls_back_safely(client, bad):
    wid = client.post("/api/walks/today").json()["walk"]["id"]
    r = upload(client, wid, jpeg(3), captured_ms=bad)

    assert r.status_code == 200
    assert r.json()["summary"]["added"] == 1

    d = client.get(f"/api/walks/{wid}").json()["discoveries"][0]
    assert d["captured_source"] != "file"


def test_resending_the_same_photo_is_reported_as_duplicate(client):
    """This is what lets the phone retry safely after a lost reply."""
    wid = client.post("/api/walks/today").json()["walk"]["id"]
    assert upload(client, wid, jpeg(4)).json()["results"][0]["status"] == "added"
    again = upload(client, wid, jpeg(4)).json()
    assert again["results"][0]["status"] == "duplicate" and again["summary"]["added"] == 0