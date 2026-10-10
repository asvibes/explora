"""Regression tests for eval/run_eval.py ground-truth handling.

Root cause being guarded: a ground-truth CSV saved with a UTF-8 BOM (Excel "CSV UTF-8",
some Windows editors) was read with plain utf-8, so the first header became '\\ufeffphoto_id'.
It prints like 'photo_id' but r["photo_id"] raised KeyError at the repeat-ids line.

No Ollama is needed: run_eval.identify / load_prompt / needs_safety_warning are replaced.
"""
import csv
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

RUN_EVAL = Path(__file__).resolve().parents[1] / "eval" / "run_eval.py"
FIELDS = ["photo_id", "category", "difficulty", "split", "ground_truth", "truth_level", "how_verified", "notes"]
NINE = [f"f{n:02d}" for n in range(1, 10)]


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("run_eval_under_test", RUN_EVAL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    def fake_identify(photo, model, prompt, phash, settings, host=None, timeout=None):
        return SimpleNamespace(
            status="ok", category="plant", identification="a plant", confidence="Medium",
            explanation="", fact="", settings=dict(settings), elapsed_s=0.1, load_s=0.0, error=None,
        )

    monkeypatch.setattr(m, "identify", fake_identify)
    monkeypatch.setattr(m, "load_prompt", lambda p: (Path(p).read_text(encoding="utf-8"), "testhash0001"))
    monkeypatch.setattr(m, "needs_safety_warning", lambda r: False)
    return m


def snapshot(folder: Path) -> dict:
    if not folder.is_dir():
        return {}
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def write_gt(path: Path, header_sep=",", encoding="utf-8", spaced=False, ids=NINE):
    sep = header_sep + (" " if spaced else "")
    lines = [sep.join(FIELDS)]
    lines += [header_sep.join([i, "plant", "easy", "scored", "a plant", "visual_group", "visual_only", ""]) for i in ids]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))   # CRLF, like Windows


@pytest.fixture
def field(tmp_path):
    photos = tmp_path / "field_photos"
    photos.mkdir()
    for i in NINE:
        (photos / f"{i}.jpg").write_bytes(b"not read by the fake identify")
    prompt = tmp_path / "frozen.txt"
    prompt.write_text("test prompt", encoding="utf-8")
    return SimpleNamespace(root=tmp_path, photos=photos, prompt=prompt, gt=tmp_path / "field_gt.csv",
                           results=tmp_path / "field_results")


def run(mod, monkeypatch, f, *extra, results=True):
    argv = ["run_eval.py", "--model", "gemma4:e2b", "--condition", "original",
            "--ground-truth", str(f.gt), "--photos-dir", str(f.photos), "--prompt", str(f.prompt)]
    if results:
        argv += ["--results-dir", str(f.results)]
    monkeypatch.setattr(sys, "argv", argv + [str(x) for x in extra])
    return mod.main()


@pytest.mark.parametrize("variant", ["plain", "utf8_bom", "spaced_header"])
def test_nine_field_photos_run_into_their_own_results_dir(mod, monkeypatch, field, variant):
    if variant == "plain":
        write_gt(field.gt)
    elif variant == "utf8_bom":
        write_gt(field.gt, encoding="utf-8-sig")        # the KeyError: 'photo_id' case
    else:
        write_gt(field.gt, spaced=True)
    original_results = mod.EVAL_DIR / "results"
    before = snapshot(original_results)

    assert run(mod, monkeypatch, field) == 0

    with (field.results / "e2b_original.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["photo_id"] for r in rows] == NINE
    assert {r["prompt_hash"] for r in rows} == {"testhash0001"}
    assert snapshot(original_results) == before          # original evaluation results untouched


def test_wrong_delimiter_gives_a_readable_error_not_a_keyerror(mod, monkeypatch, field, capsys):
    write_gt(field.gt, header_sep=";")
    assert run(mod, monkeypatch, field) == 2
    out = capsys.readouterr().out
    assert "photo_id" in out and "found" in out.lower()
    assert not field.results.exists()


def test_custom_ground_truth_needs_its_own_results_dir(mod, monkeypatch, field, capsys):
    write_gt(field.gt)
    before = snapshot(mod.EVAL_DIR / "results")
    assert run(mod, monkeypatch, field, results=False) == 2
    assert "--results-dir" in capsys.readouterr().out
    assert snapshot(mod.EVAL_DIR / "results") == before


def test_allow_default_results_flag_overrides_the_guard(mod, monkeypatch, field):
    write_gt(field.gt)
    mod.EVAL_DIR = field.root / "eval"                   # defaults now point inside tmp_path
    assert run(mod, monkeypatch, field, "--allow-default-results", results=False) == 0
    assert (field.root / "eval" / "results" / "e2b_original.csv").exists()


def test_frozen_prompt_guard_still_applies(mod, monkeypatch, field, tmp_path):
    write_gt(field.gt, encoding="utf-8-sig")
    field.prompt = tmp_path / "v1.txt"
    field.prompt.write_text("draft prompt", encoding="utf-8")
    assert run(mod, monkeypatch, field) == 2
    assert not field.results.exists()


def test_missing_photo_is_reported_not_fatal(mod, monkeypatch, field, capsys):
    write_gt(field.gt, encoding="utf-8-sig")
    (field.photos / "f05.jpg").unlink()
    assert run(mod, monkeypatch, field) == 0
    assert "f05" in capsys.readouterr().out
    with (field.results / "e2b_original.csv").open(newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 8