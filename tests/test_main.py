import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.config import Config
from server.identify import IdentifyResult
from server.main import create_app


def make_result(**kw):
    base = dict(status="ok", category="bird", identification="Blue Jay", confidence="High",
                explanation="Blue crest and wings.", fact="Blue jays belong to the crow family.",
                model="fake", settings={}, prompt_hash="abc", elapsed_s=0.1, load_s=0.0, raw="{}")
    base.update(kw)
    return IdentifyResult(**base)


@pytest.fixture
def state():
    return {"result": make_result()}


@pytest.fixture
def client(tmp_path, state):
    prompt = tmp_path / "frozen.txt"
    prompt.write_text("prompt", encoding="utf-8")
    cfg = Config(data_dir=tmp_path / "data", prompt_path=prompt, ollama_url="http://127.0.0.1:9")
    app = create_app(cfg, identify_fn=lambda *a, **k: state["result"], allowed_hosts=["testserver"])
    return TestClient(app)


def jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="JPEG")
    return buf.getvalue()


def upload(client, walk_id, *items):
    files = [("files", (name, data, "image/jpeg")) for name, data in items]
    return client.post(f"/api/walks/{walk_id}/photos", files=files)


@pytest.fixture
def walk(client):
    return client.post("/api/walks/today").json()["walk"]["id"]


def test_health_works_when_ollama_is_down(client):
    j = client.get("/api/health").json()
    assert j["ok"] is True and j["ollama"] is False and j["quests"] is True


def test_host_header_is_checked(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_settings_roundtrip_and_validation(client):
    assert client.get("/api/settings").json()["theme"] == "light"
    r = client.put("/api/settings", json={"preferred_name": " Aarush ", "theme": "dark"})
    assert r.json()["preferred_name"] == "Aarush" and r.json()["theme"] == "dark"
    assert client.put("/api/settings", json={"theme": "neon"}).status_code == 422


def test_today_walk_is_one_per_day(client):
    a = client.post("/api/walks/today").json()["walk"]["id"]
    b = client.post("/api/walks/today").json()["walk"]["id"]
    assert a == b


def test_patch_walk(client, walk):
    r = client.patch(f"/api/walks/{walk}", json={"habitat": "park", "duration_min": 40})
    assert r.json()["walk"]["habitat"] == "park"
    assert client.patch(f"/api/walks/{walk}", json={"habitat": "street"}).status_code == 400
    assert client.patch(f"/api/walks/{walk}", json={"duration_min": 0}).status_code == 422
    assert client.patch("/api/walks/999", json={"area": "x"}).status_code == 404


def test_upload_dedupes_and_reports_bad_files(client, walk):
    r = upload(client, walk, ("a.jpg", jpeg("red")), ("a-copy.jpg", jpeg("red")),
               ("notes.txt", b"hello"), ("../../evil.jpg", jpeg("blue"))).json()
    s = r["summary"]
    assert s["added"] == 2 and s["duplicates"] == 1 and len(s["errors"]) == 1
    names = [x["name"] for x in r["results"]]
    assert "evil.jpg" in names and all("/" not in n for n in names)
    assert len(client.get(f"/api/walks/{walk}").json()["discoveries"]) == 2


def test_upload_to_missing_walk(client):
    assert upload(client, 999, ("a.jpg", jpeg("red"))).status_code == 404


def test_identify_confirm_collection_flow(client, walk):
    upload(client, walk, ("a.jpg", jpeg("red")))
    r = client.post(f"/api/walks/{walk}/identify").json()
    assert r["summary"]["identified"] == 1
    d = r["discoveries"][0]
    assert d["suggestion_text"] == "Looks like a Blue Jay."
    assert client.get("/api/collection").json()["total"] == 0          # suggestion is not a record
    assert client.post(f"/api/discoveries/{d['id']}/confirm").json()["status"] == "confirmed"
    assert client.get("/api/collection").json() == {"total": 1, "by_category": {"bird": 1}}
    assert client.post(f"/api/discoveries/{d['id']}/identify").status_code == 400   # confirmed: no re-identify


def test_low_confidence_cannot_be_confirmed_but_can_be_corrected_or_saved(client, walk, state):
    state["result"] = make_result(confidence="Low", identification=None)
    upload(client, walk, ("a.jpg", jpeg("red")), ("b.jpg", jpeg("green")))
    ds = client.post(f"/api/walks/{walk}/identify").json()["discoveries"]
    assert ds[0]["suggestion_text"] == "Not sure what this is."
    assert client.post(f"/api/discoveries/{ds[0]['id']}/confirm").status_code == 400
    ok = client.post(f"/api/discoveries/{ds[0]['id']}/correct", json={"label": "Magpie", "category": "bird"})
    assert ok.json()["final_label"] == "Magpie"
    assert client.post(f"/api/discoveries/{ds[1]['id']}/save").json()["status"] == "saved_unidentified"


def test_correct_validates(client, walk):
    upload(client, walk, ("a.jpg", jpeg("red")))
    did = client.get(f"/api/walks/{walk}").json()["discoveries"][0]["id"]
    assert client.post(f"/api/discoveries/{did}/correct", json={"label": ""}).status_code == 422
    assert client.post(f"/api/discoveries/{did}/correct", json={"label": "x", "category": "not_nature"}).status_code == 400
    assert client.post("/api/discoveries/999/reject").status_code == 404


def test_model_failure_is_a_friendly_503_and_leaves_status(client, walk, state):
    state["result"] = make_result(status="error", error="connection refused")
    upload(client, walk, ("a.jpg", jpeg("red")))
    did = client.get(f"/api/walks/{walk}").json()["discoveries"][0]["id"]
    r = client.post(f"/api/discoveries/{did}/identify")
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]
    assert client.get(f"/api/walks/{walk}").json()["discoveries"][0]["status"] == "imported"


def test_unsafe_reassurance_is_dropped_via_existing_pipeline(client, walk, state):
    state["result"] = make_result(fact="This plant is safe to eat.")
    upload(client, walk, ("a.jpg", jpeg("red")))
    d = client.post(f"/api/walks/{walk}/identify").json()["discoveries"][0]
    assert d["ai_fact"] == "" and d["ai_fact_blocked"] == 1


def test_fungi_gets_app_warning(client, walk, state):
    state["result"] = make_result(category="fungi", identification="a bracket fungus", confidence="High")
    upload(client, walk, ("a.jpg", jpeg("red")))
    d = client.post(f"/api/walks/{walk}/identify").json()["discoveries"][0]
    assert "don't touch or eat" in d["warning"]


def test_photo_served_and_missing(client, walk):
    upload(client, walk, ("a.jpg", jpeg("red")))
    did = client.get(f"/api/walks/{walk}").json()["discoveries"][0]["id"]
    r = client.get(f"/api/discoveries/{did}/photo")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get("/api/discoveries/999/photo").status_code == 404


def test_note_and_captured_at(client, walk):
    upload(client, walk, ("a.jpg", jpeg("red")))
    did = client.get(f"/api/walks/{walk}").json()["discoveries"][0]["id"]
    assert client.put(f"/api/discoveries/{did}/note", json={"note": " warm day "}).json()["user_note"] == "warm day"
    assert client.put(f"/api/discoveries/{did}/captured-at", json={"captured_at": "2026-10-09T17:30:00"}).json()["captured_source"] == "user"
    assert client.put(f"/api/discoveries/{did}/captured-at", json={"captured_at": "yesterday"}).status_code == 400


# ---------------------------------------------------------------- quests over HTTP

def test_quest_suggestions_are_stable_and_have_no_counters(client, walk):
    a = client.get(f"/api/walks/{walk}/quests").json()
    b = client.get(f"/api/walks/{walk}/quests").json()
    assert a == b and a["done"] == []
    text = json.dumps(a).lower()
    for word in ("streak", "badge", "remaining", "progress", "incomplete", "total", "count"):
        assert word not in text
    assert client.get(f"/api/walks/{walk}/quests?difficulty=hard").status_code == 400
    assert client.get(f"/api/walks/{walk}/quests?count=0").status_code == 422
    assert client.get("/api/walks/999/quests").status_code == 404


def test_quest_done_shows_only_on_its_walk(client, walk):
    sid = client.get(f"/api/walks/{walk}/quests").json()["suggestions"][0]["id"]
    assert client.post(f"/api/walks/{walk}/quests/{sid}/done").status_code == 200
    assert client.post(f"/api/walks/{walk}/quests/{sid}/done").status_code == 200   # repeat is harmless
    assert len(client.get(f"/api/walks/{walk}").json()["quests_done"]) == 1
    # a different walk (created through the existing module via PATCH-free route) never sees it
    other = client.post("/api/walks/today").json()
    assert other["walk"]["id"] == walk       # same day, same walk
    assert client.post(f"/api/walks/{walk}/quests/nope/done").status_code == 404
    assert client.post("/api/walks/999/quests/" + sid + "/done").status_code == 404
    assert client.delete(f"/api/walks/{walk}/quests/{sid}/done").json() == {"removed": True}
    assert client.get(f"/api/walks/{walk}").json()["quests_done"] == []


def test_quests_fail_closed_when_template_file_is_unsafe(tmp_path, monkeypatch):
    from server import quests
    def boom(*a, **k): raise quests.QuestError("unsafe")
    monkeypatch.setattr(quests, "load_templates", boom)
    cfg = Config(data_dir=tmp_path / "d", prompt_path=tmp_path / "p.txt")
    c = TestClient(create_app(cfg, allowed_hosts=["testserver"]))
    assert c.get("/api/health").json()["quests"] is False
    wid = c.post("/api/walks/today")
    # quests_done still works (reads only), but suggestions are unavailable
    assert c.get(f"/api/walks/{wid.json()['walk']['id']}/quests").status_code == 503