import pytest

from server import importer, walks
from server.identify import IdentifyResult
from server.models import suggestion_text


def _discovery(conn, cfg, jpeg, seed=1, day="2026-10-08"):
    wid = walks.get_or_create_walk(conn, day)["id"]
    return wid, importer.import_bytes(conn, cfg, wid, jpeg(seed=seed), f"{seed}.jpg").discovery_id


# ---------------------------------------------------------------- walks

def test_one_walk_per_day(conn):
    a = walks.get_or_create_walk(conn, "2026-10-08")
    b = walks.get_or_create_walk(conn, "2026-10-08")
    c = walks.get_or_create_walk(conn, "2026-10-09")
    assert a["id"] == b["id"] != c["id"]


def test_empty_walk_is_valid_and_has_no_completeness_field(conn):
    w = walks.get_or_create_walk(conn, "2026-10-08")
    view = walks.walk_view(conn, w["id"])
    assert view["discoveries"] == []
    assert not any("complete" in k for k in view["walk"])


def test_update_walk_validates_habitat_and_duration(conn):
    w = walks.get_or_create_walk(conn, "2026-10-08")
    ok = walks.update_walk(conn, w["id"], area="Campus lawn", habitat="campus", duration_min=40)
    assert ok["habitat"] == "campus" and ok["duration_min"] == 40
    for bad in ({"habitat": "street"}, {"habitat": "lake shore"}, {"duration_min": 0}, {"colour": "red"}):
        with pytest.raises(ValueError):
            walks.update_walk(conn, w["id"], **bad)


def test_bad_date_rejected(conn):
    with pytest.raises(ValueError):
        walks.get_or_create_walk(conn, "08/10/2026")


# ---------------------------------------------------------------- wording

@pytest.mark.parametrize("name,conf,expected", [
    ("Blue Jay", "High", "Looks like a Blue Jay."),
    ("Blue Jay", "Medium", "Might be a Blue Jay."),
    ("Oak", "Medium", "Might be an Oak."),
    ("a swallowtail butterfly", "High", "Looks like a swallowtail butterfly."),
    ("Blue Jay", "Low", "Not sure what this is."),
    (None, "High", "Not sure what this is."),
])
def test_wording_follows_confidence(name, conf, expected):
    assert suggestion_text(name, conf) == expected


def test_malformed_is_not_sure_and_has_no_percentages():
    assert suggestion_text("Blue Jay", "High", "malformed") == "Not sure what this is."
    for conf in ("High", "Medium", "Low"):
        assert "%" not in suggestion_text("Blue Jay", conf)


# ---------------------------------------------------------------- identify / confirm / correct

def test_identify_stores_result_and_reproducibility_fields(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    d = walks.identify_discovery(conn, cfg, did, fake())
    assert d["status"] == "identified" and d["ai_identification"] == "Blue Jay"
    assert d["ai_model"] == "test-model" and d["ai_prompt_hash"] and d["ai_settings"]
    assert d["suggestion_text"] == "Looks like a Blue Jay."


def test_confirm_sets_final_label(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    walks.identify_discovery(conn, cfg, did, fake())
    d = walks.confirm(conn, did)
    assert d["status"] == "confirmed" and d["final_label"] == "Blue Jay"
    assert d["final_category"] == "bird" and d["final_source"] == "confirmed"


def test_cannot_confirm_low_confidence_or_unidentified(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    with pytest.raises(ValueError):                      # never identified
        walks.confirm(conn, did)
    walks.identify_discovery(conn, cfg, did, fake(confidence="Low"))
    with pytest.raises(ValueError):                      # "Not sure"
        walks.confirm(conn, did)


def test_malformed_output_becomes_not_sure_never_confident(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    bad = fake(status="malformed", category="unknown", identification=None, confidence="Low")
    d = walks.identify_discovery(conn, cfg, did, bad)
    assert d["suggestion_text"] == "Not sure what this is."
    assert d["warning"]                                   # app adds its own safety note
    with pytest.raises(ValueError):
        walks.confirm(conn, did)


def test_correct_counts_as_confirmed_and_defaults_category(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    walks.identify_discovery(conn, cfg, did, fake(category="butterfly", identification="Monarch", confidence="Medium"))
    d = walks.correct(conn, did, "  Painted Lady ")
    assert d["status"] == "confirmed" and d["final_label"] == "Painted Lady"
    assert d["final_category"] == "butterfly" and d["final_source"] == "corrected"


def test_correct_validates(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    with pytest.raises(ValueError):
        walks.correct(conn, did, "   ")
    with pytest.raises(ValueError):
        walks.correct(conn, did, "Thing", category="spaceship")
    assert walks.correct(conn, did, "Mystery lump")["final_category"] == "other"   # never identified


def test_reject_and_save_unidentified_keep_the_photo(conn, cfg, jpeg, fake):
    _, a = _discovery(conn, cfg, jpeg, seed=1)
    _, b = _discovery(conn, cfg, jpeg, seed=2)
    for did in (a, b):
        walks.identify_discovery(conn, cfg, did, fake())
    assert walks.reject(conn, a)["status"] == "rejected"
    assert walks.save_unidentified(conn, b)["status"] == "saved_unidentified"
    assert walks.get_discovery(conn, a)["final_label"] is None
    assert (cfg.data_dir / walks.get_discovery(conn, a)["photo_path"]).exists()


def test_try_again_replaces_result_but_not_after_confirming(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    walks.identify_discovery(conn, cfg, did, fake(identification="Crow", confidence="Medium"))
    d = walks.identify_discovery(conn, cfg, did, fake(identification="Raven", confidence="High"))
    assert d["ai_identification"] == "Raven"
    walks.confirm(conn, did)
    with pytest.raises(ValueError):
        walks.identify_discovery(conn, cfg, did, fake())


def test_unsafe_reassurance_in_fact_is_dropped(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    d = walks.identify_discovery(conn, cfg, did, fake(category="plant", identification="Berry bush",
                                                       fact="These berries are safe to eat."))
    assert d["ai_fact"] == "" and d["ai_fact_blocked"] == 1


def test_warning_in_fact_is_kept(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    d = walks.identify_discovery(conn, cfg, did, fake(category="plant", identification="Nightshade",
                                                       fact="Not safe to eat; do not touch the berries."))
    assert d["ai_fact"] and d["ai_fact_blocked"] == 0


def test_fungi_always_get_the_app_warning_even_when_model_is_confident(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    d = walks.identify_discovery(conn, cfg, did, fake(category="fungi", identification="Fly agaric", confidence="High"))
    assert d["warning"] and "touch or eat" in d["warning"]
    d = walks.confirm(conn, did)
    assert d["warning"]                                   # still shown after confirming


def test_hard_error_does_not_change_status(conn, cfg, jpeg, fake):
    _, did = _discovery(conn, cfg, jpeg)
    err = fake(status="error", error="connection refused")
    with pytest.raises(walks.IdentifyError):
        walks.identify_discovery(conn, cfg, did, err)
    assert walks.get_discovery(conn, did)["status"] == "imported"


def test_batch_continues_after_an_error(conn, cfg, jpeg, fake):
    wid, a = _discovery(conn, cfg, jpeg, seed=1)
    b = importer.import_bytes(conn, cfg, wid, jpeg(seed=2), "b.jpg").discovery_id
    calls = {"n": 0}

    def flaky(*args, **kw):
        calls["n"] += 1
        result = fake(status="error", error="boom") if calls["n"] == 1 else fake()
        return result

    out = walks.identify_walk(conn, cfg, wid, flaky)
    assert out == {"identified": 1, "errors": [(a, "boom")], "total": 2}
    assert walks.get_discovery(conn, b)["status"] == "identified"


# ---------------------------------------------------------------- notes, dates, collection

def test_note_and_captured_date_edit(conn, cfg, jpeg):
    _, did = _discovery(conn, cfg, jpeg)
    assert walks.set_note(conn, did, " smelled of rain ")["user_note"] == "smelled of rain"
    d = walks.set_captured_at(conn, did, "2026-10-08T16:45:00")
    assert d["captured_at"] == "2026-10-08T16:45:00" and d["captured_source"] == "user"
    with pytest.raises(ValueError):
        walks.set_captured_at(conn, did, "yesterday")


def test_collection_counts_only_confirmed_by_category(conn, cfg, jpeg, fake):
    ids = [_discovery(conn, cfg, jpeg, seed=s)[1] for s in range(1, 6)]
    for i in ids:
        walks.identify_discovery(conn, cfg, i, fake())
    walks.confirm(conn, ids[0])
    walks.confirm(conn, ids[1])
    walks.correct(conn, ids[2], "Oak", category="tree")
    walks.reject(conn, ids[3])                            # not counted
    # ids[4] stays 'identified', not counted
    c = walks.collection_counts(conn)
    assert c == {"total": 3, "by_category": {"bird": 2, "tree": 1}}
    assert not any(k in c for k in ("percent", "target", "rank", "progress"))