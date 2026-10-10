import json
import sqlite3

import pytest

from server import quests, safety
from server.config import Config
from server.db import connect
from server.models import HABITATS
from server.walks import get_or_create_walk


@pytest.fixture
def conn(tmp_path):
    c = connect(Config(data_dir=tmp_path / "data"))
    yield c
    c.close()


def write_templates(tmp_path, items):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"templates": items}), encoding="utf-8")
    return p


# ---------------------------------------------------------------- templates

def test_shipped_templates_load_and_are_clean():
    templates = quests.load_templates()
    assert len(templates) >= 3
    for t in templates:
        assert safety.quest_violations(t.text) == [], t.id
        assert t.difficulty in quests.DIFFICULTIES


def test_template_ids_unique():
    ids = [t.id for t in quests.load_templates()]
    assert len(ids) == len(set(ids))


def test_unsafe_template_is_refused(tmp_path):
    p = write_templates(tmp_path, [{"id": "bad", "text": "Pick up a snail and look closely."}])
    with pytest.raises(quests.QuestError, match="banned"):
        quests.load_templates(p)


def test_unsafe_template_with_suffix_is_refused(tmp_path):
    p = write_templates(tmp_path, [{"id": "bad", "text": "Try eating a berry."}])
    with pytest.raises(quests.QuestError):
        quests.load_templates(p)


@pytest.mark.parametrize("text", ["Try tasting a berry.", "Go handling a frog.", "Try chasing a butterfly.",
                                  "Go picking up a feather.", "Try consuming a leaf."])
def test_inflections_that_drop_a_final_e_are_caught(tmp_path, text):
    with pytest.raises(quests.QuestError):
        quests.load_templates(write_templates(tmp_path, [{"id": "bad", "text": text}]))


def test_known_false_positive_petal_is_flagged_by_pet(tmp_path):
    # 'pet' is a word-start match, so 'petal' trips it. Templates avoid the word; this documents why.
    with pytest.raises(quests.QuestError):
        quests.load_templates(write_templates(tmp_path, [{"id": "p", "text": "Photograph a petal."}]))


@pytest.mark.parametrize("item", [
    {"id": "Bad Id", "text": "Photograph a cloud."},
    {"id": "ok", "text": ""},
    {"id": "ok", "text": "Photograph a cloud.", "difficulty": "hard"},
    {"id": "ok", "text": "Photograph a cloud.", "habitats": ["street"]},
])
def test_malformed_templates_are_refused(tmp_path, item):
    with pytest.raises(quests.QuestError):
        quests.load_templates(write_templates(tmp_path, [item]))


def test_duplicate_ids_refused(tmp_path):
    item = {"id": "a", "text": "Photograph a cloud."}
    with pytest.raises(quests.QuestError, match="duplicate"):
        quests.load_templates(write_templates(tmp_path, [item, dict(item)]))


def test_missing_and_broken_files_raise_quest_error(tmp_path):
    with pytest.raises(quests.QuestError):
        quests.load_templates(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(quests.QuestError):
        quests.load_templates(bad)


def test_refuses_to_load_when_quest_banned_list_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "load_banned", lambda path=None: {"facts": ("edible",)})
    with pytest.raises(quests.QuestError, match="unscanned"):
        quests.load_templates(write_templates(tmp_path, [{"id": "a", "text": "Photograph a cloud."}]))


# ---------------------------------------------------------------- suggestions

def test_suggestions_are_stable_for_a_walk():
    assert quests.suggest(7) == quests.suggest(7)


def test_suggestions_can_differ_between_walks():
    seen = {tuple(q["id"] for q in quests.suggest(w)) for w in range(1, 30)}
    assert len(seen) > 1


def test_suggestion_shape_has_no_pressure_fields():
    out = quests.suggest(1, count=5)
    assert out
    for q in out:
        assert set(q) == {"id", "text", "difficulty"}


def test_difficulty_filter_and_count():
    out = quests.suggest(1, difficulty="easy", count=2)
    assert len(out) == 2 and all(q["difficulty"] == "easy" for q in out)


def test_habitat_filter():
    t = (quests.QuestTemplate("a", "Photograph a cloud.", "easy", ("garden",)),
         quests.QuestTemplate("b", "Photograph a shadow.", "easy", ()))
    assert [q["id"] for q in quests.suggest(1, habitat="trail", templates=t)] == ["b"]
    assert {q["id"] for q in quests.suggest(1, habitat="garden", templates=t)} == {"a", "b"}


@pytest.mark.parametrize("kw", [{"habitat": "street"}, {"difficulty": "hard"}, {"count": 0}, {"count": 99}])
def test_suggest_validates_input(kw):
    with pytest.raises(ValueError):
        quests.suggest(1, **kw)


# ---------------------------------------------------------------- I did this

def test_mark_done_is_scoped_to_one_walk(conn):
    w1 = get_or_create_walk(conn, "2026-10-09")["id"]
    w2 = get_or_create_walk(conn, "2026-10-10")["id"]
    done = quests.mark_done(conn, w1, "something_yellow")
    assert done["template_id"] == "something_yellow" and done["wording"]
    assert [c["template_id"] for c in quests.completions(conn, w1)] == ["something_yellow"]
    assert quests.completions(conn, w2) == []


def test_mark_done_twice_stores_once(conn):
    w = get_or_create_walk(conn)["id"]
    first = quests.mark_done(conn, w, "something_round")
    second = quests.mark_done(conn, w, "something_round")
    assert first == second
    assert len(quests.completions(conn, w)) == 1


def test_wording_is_kept_as_shown(conn):
    w = get_or_create_walk(conn)["id"]
    t = (quests.QuestTemplate("x", "Photograph a cloud.", "easy"),)
    quests.mark_done(conn, w, "x", templates=t)
    assert quests.completions(conn, w)[0]["wording"] == "Photograph a cloud."


def test_unknown_walk_or_quest(conn):
    w = get_or_create_walk(conn)["id"]
    with pytest.raises(KeyError):
        quests.mark_done(conn, 999, "something_yellow")
    with pytest.raises(KeyError):
        quests.mark_done(conn, w, "no_such_quest")
    with pytest.raises(KeyError):
        quests.completions(conn, 999)


def test_unmark(conn):
    w = get_or_create_walk(conn)["id"]
    quests.mark_done(conn, w, "something_yellow")
    assert quests.unmark(conn, w, "something_yellow") is True
    assert quests.unmark(conn, w, "something_yellow") is False
    assert quests.completions(conn, w) == []


def test_suggesting_stores_nothing(conn):
    w = get_or_create_walk(conn)["id"]
    quests.suggest(w)
    assert conn.execute("SELECT COUNT(*) FROM quest_completions").fetchone()[0] == 0


def test_no_pressure_vocabulary_in_module_or_templates():
    src = open(quests.__file__, encoding="utf-8").read().lower()
    data = open(quests.TEMPLATES_FILE, encoding="utf-8").read().lower()
    for word in ("streak", "badge", "leaderboard", "expired", "missed", "incomplete"):
        assert word not in data
    # the module may name them only to say they are absent (docstring); no code uses them
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')) )
    assert "def streak" not in code and "badge =" not in code