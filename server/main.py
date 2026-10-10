"""Explora local server: the HTTP layer over the existing modules.

Run (from the repo root):
    python -m server.main                 # http://127.0.0.1:8000, this laptop only
    python -m server.main --port 8080

This file only wires things together. Importing, identification, safety wording,
walks and collection logic live in importer.py, walks.py, safety.py and
quests.py, and are called, not copied. Everything is local: the only network
call is an optional health check to the local Ollama server.

Defaults are private. The server binds to 127.0.0.1 and rejects other Host
headers (this blocks DNS-rebinding tricks from web pages). Serving the phone over
home Wi-Fi (PRD stretch) needs --host 0.0.0.0, which turns the Host check off:
that traffic is unencrypted, as the PRD says plainly.

Needs: fastapi, uvicorn, python-multipart (all free). Tests also need httpx.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import importer, quests, walks
from .config import ROOT, Config, load_config
from .db import connect, get_setting, set_setting
from .walks import IdentifyError

log = logging.getLogger("explora")

MAX_FILES_PER_UPLOAD = 100
MAX_PHOTO_BYTES = 30 * 1024 * 1024
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
SETTING_KEYS = ("name", "preferred_name", "theme", "journal_name")


# ------------------------------------------------------------------ request bodies

class SettingsBody(BaseModel):
    name: str | None = Field(None, max_length=80)
    preferred_name: str | None = Field(None, max_length=80)
    theme: str | None = Field(None, pattern="^(light|dark)$")
    journal_name: str | None = Field(None, max_length=80)


class WalkBody(BaseModel):
    area: str | None = Field(None, max_length=200)
    habitat: str | None = None
    duration_min: int | None = Field(None, ge=1, le=1440)


class CorrectBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    category: str | None = Field(None, max_length=40)


class NoteBody(BaseModel):
    note: str = Field("", max_length=2000)


class CapturedAtBody(BaseModel):
    captured_at: str = Field(..., max_length=40)


# ------------------------------------------------------------------ helpers

@contextmanager
def _session(cfg: Config) -> Iterator[Any]:
    """One SQLite connection per request, opened and closed in the thread that uses it."""
    conn = connect(cfg)
    try:
        yield conn
    finally:
        conn.close()


def _run(fn: Callable, *args, **kwargs):
    """Call an existing module function and turn its failures into friendly HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "Not found")
    except IdentifyError as e:
        raise HTTPException(503, f"The local model could not identify this photo: {str(e)[:200]}. "
                                 "Is Ollama running and the model pulled?")
    except FileNotFoundError:
        raise HTTPException(503, "A file Explora needs is missing (for example the identify prompt).")
    except ValueError as e:                       # includes quests.QuestError
        raise HTTPException(400, str(e))
    except Exception:
        log.exception("unexpected error")
        raise HTTPException(500, "Something went wrong on the laptop. Details are in the server log.")


def _ollama_reachable(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _read_limited(upload: UploadFile) -> bytes | None:
    """Read an upload, or None if it is over the size limit."""
    data = upload.file.read(MAX_PHOTO_BYTES + 1)
    return None if len(data) > MAX_PHOTO_BYTES else data


# ------------------------------------------------------------------ app

def create_app(cfg: Config | None = None, identify_fn: Callable | None = None,
               allowed_hosts: list[str] | None = None) -> FastAPI:
    """Build the app. `identify_fn` lets tests replace the model; nothing touches disk until a request."""
    cfg = cfg or load_config()
    app = FastAPI(title="Explora", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or DEFAULT_ALLOWED_HOSTS)
    identify_lock = threading.Lock()     # one identification job at a time on a 6GB GPU

    # Quests fail closed: a bad or unscanned template file disables quests, not the whole app.
    templates, quest_problem = None, None
    try:
        templates = quests.load_templates()
    except (OSError, ValueError) as e:
        quest_problem = str(e)
        log.error("quests disabled: %s", e)

    def _templates():
        if templates is None:
            raise HTTPException(503, "Quests are unavailable right now.")
        return templates

    # ---- status and settings
    @app.get("/api/health")
    def health():
        return {"ok": True, "model": cfg.model, "ollama": _ollama_reachable(cfg.ollama_url),
                "quests": templates is not None}

    @app.get("/api/settings")
    def get_settings():
        with _session(cfg) as conn:
            return {k: get_setting(conn, k, "") for k in SETTING_KEYS} | \
                   {"theme": get_setting(conn, "theme", "light")}

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        with _session(cfg) as conn:
            for k, v in body.model_dump(exclude_none=True).items():
                _run(set_setting, conn, k, v.strip())
            return {k: get_setting(conn, k, "") for k in SETTING_KEYS} | \
                   {"theme": get_setting(conn, "theme", "light")}

    # ---- walks
    @app.get("/api/walks")
    def list_walks():
        with _session(cfg) as conn:
            return {"walks": _run(walks.list_walks, conn)}

    @app.post("/api/walks/today")
    def today():
        with _session(cfg) as conn:
            walk = _run(walks.get_or_create_walk, conn)
            return {**_run(walks.walk_view, conn, walk["id"]), "quests_done": _run(quests.completions, conn, walk["id"])}

    @app.get("/api/walks/{walk_id}")
    def get_walk(walk_id: int):
        with _session(cfg) as conn:
            return {**_run(walks.walk_view, conn, walk_id), "quests_done": _run(quests.completions, conn, walk_id)}

    @app.patch("/api/walks/{walk_id}")
    def patch_walk(walk_id: int, body: WalkBody):
        with _session(cfg) as conn:
            _run(walks.update_walk, conn, walk_id, **body.model_dump(exclude_unset=True))
            return _run(walks.walk_view, conn, walk_id)

    # ---- import
    @app.post("/api/walks/{walk_id}/photos")
    def upload_photos(walk_id: int, files: list[UploadFile] = File(...)):
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise HTTPException(400, f"Please send at most {MAX_FILES_PER_UPLOAD} photos at a time.")
        with _session(cfg) as conn:
            if _run(walks.get_walk, conn, walk_id) is None:
                raise HTTPException(404, f"No walk {walk_id}")
            results = []
            for up in files:
                name = Path(up.filename or "photo").name[:200] or "photo"   # display name only, never a path
                data = _read_limited(up)
                if data is None:
                    results.append(importer.ImportResult("error", name, reason="photo is too large"))
                else:
                    results.append(_run(importer.import_bytes, conn, cfg, walk_id, data, name))
            return {"results": [dataclasses.asdict(r) for r in results],
                    "summary": importer.summarize(results)}

    @app.get("/api/discoveries/{discovery_id}/photo")
    def discovery_photo(discovery_id: int):
        with _session(cfg) as conn:
            d = _run(walks.get_discovery, conn, discovery_id)
        path = (cfg.data_dir / d["photo_path"]).resolve()
        if cfg.photos_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "Photo not found")
        return FileResponse(path, media_type="image/jpeg")

    # ---- identification (the model suggests; the person decides)
    def _locked(fn: Callable, *args, **kwargs):
        if not identify_lock.acquire(blocking=False):
            raise HTTPException(409, "Identification is already running. Please wait for it to finish.")
        try:
            return _run(fn, *args, **kwargs)
        finally:
            identify_lock.release()

    @app.post("/api/walks/{walk_id}/identify")
    def identify_walk(walk_id: int, only_new: bool = True):
        with _session(cfg) as conn:
            _run(walks.walk_view, conn, walk_id)                     # 404 if the walk does not exist
            summary = _locked(walks.identify_walk, conn, cfg, walk_id, identify_fn, only_new)
            return {"summary": summary, **_run(walks.walk_view, conn, walk_id)}

    @app.post("/api/discoveries/{discovery_id}/identify")
    def identify_one(discovery_id: int):
        """Also "Try Again"."""
        with _session(cfg) as conn:
            return _locked(walks.identify_discovery, conn, cfg, discovery_id, identify_fn)

    @app.post("/api/discoveries/{discovery_id}/confirm")
    def confirm(discovery_id: int):
        with _session(cfg) as conn:
            return _run(walks.confirm, conn, discovery_id)

    @app.post("/api/discoveries/{discovery_id}/correct")
    def correct(discovery_id: int, body: CorrectBody):
        with _session(cfg) as conn:
            return _run(walks.correct, conn, discovery_id, body.label, body.category)

    @app.post("/api/discoveries/{discovery_id}/reject")
    def reject(discovery_id: int):
        with _session(cfg) as conn:
            return _run(walks.reject, conn, discovery_id)

    @app.post("/api/discoveries/{discovery_id}/save")
    def save_unidentified(discovery_id: int):
        with _session(cfg) as conn:
            return _run(walks.save_unidentified, conn, discovery_id)

    @app.put("/api/discoveries/{discovery_id}/note")
    def note(discovery_id: int, body: NoteBody):
        with _session(cfg) as conn:
            return _run(walks.set_note, conn, discovery_id, body.note)

    @app.put("/api/discoveries/{discovery_id}/captured-at")
    def captured_at(discovery_id: int, body: CapturedAtBody):
        with _session(cfg) as conn:
            return _run(walks.set_captured_at, conn, discovery_id, body.captured_at)

    # ---- collection
    @app.get("/api/collection")
    def collection():
        with _session(cfg) as conn:
            return _run(walks.collection_counts, conn)

    # ---- quests: optional ideas, and "I did this" for one walk
    @app.get("/api/walks/{walk_id}/quests")
    def walk_quests(walk_id: int, difficulty: str | None = None,
                    count: int = Query(quests.DEFAULT_SUGGESTIONS, ge=1, le=quests.MAX_SUGGESTIONS)):
        pool = _templates()
        with _session(cfg) as conn:
            walk = _run(walks.get_walk, conn, walk_id)
            if walk is None:
                raise HTTPException(404, f"No walk {walk_id}")
            return {"suggestions": _run(quests.suggest, walk_id, walk["habitat"], difficulty, count, pool),
                    "done": _run(quests.completions, conn, walk_id)}

    @app.post("/api/walks/{walk_id}/quests/{template_id}/done")
    def quest_done(walk_id: int, template_id: str):
        pool = _templates()
        with _session(cfg) as conn:
            return _run(quests.mark_done, conn, walk_id, template_id, pool)

    @app.delete("/api/walks/{walk_id}/quests/{template_id}/done")
    def quest_undo(walk_id: int, template_id: str):
        _templates()
        with _session(cfg) as conn:
            return {"removed": _run(quests.unmark, conn, walk_id, template_id)}

    # ---- the front end (once web/index.html exists). Mounted last so /api wins.
    web = ROOT / "web"
    if (web / "index.html").exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")

    return app


# ------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Explora local server.")
    ap.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (this laptop only)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback:
        print("WARNING: listening beyond this laptop. Traffic on home Wi-Fi is unencrypted and the "
              "Host check is off. Use only on a network you trust.")
    import uvicorn   # imported here so tests and tooling can import this module without it

    cfg = load_config()
    app = create_app(cfg, allowed_hosts=None if loopback else ["*"])
    print(f"Explora: http://{args.host}:{args.port}  (data: {cfg.data_dir}, model: {cfg.model})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())