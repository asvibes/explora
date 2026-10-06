"""Local photo identification through Ollama (Gemma 4).

This is the single identify path: the app and eval/run_eval.py both call
`identify()`, so the evaluation tests exactly what ships.

Design rules from the PRD:
- The model only suggests. Malformed or contradictory output becomes an honest
  "not sure" result (Low confidence, no identification). It is never dropped
  and never treated as confident.
- Every result records the model, settings and prompt hash for reproducibility.
- Nothing is sent anywhere except the local Ollama server.

Needs: Pillow. Ollama is reached with the standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageOps

OLLAMA_URL = "http://localhost:11434"

CATEGORIES = (
    "bird", "butterfly", "insect", "plant", "tree", "flower", "fungi",
    "rock", "shell", "mammal", "reptile", "other", "not_nature", "unknown",
)
CONFIDENCES = ("High", "Medium", "Low")

# Defaults: thinking off, small image, capped output. Temperature/top_p/top_k
# are left at the model's own defaults unless passed in `options`.
DEFAULT_SETTINGS = {
    "max_side": 768,          # longest image side sent to the model (bounds image tokens)
    "think": False,           # start with thinking disabled (model-page tip)
    "keep_alive": "10m",
    "options": {"num_predict": 400},
}


@dataclass
class IdentifyResult:
    status: str                 # "ok" | "malformed" | "error"
    category: str
    identification: str | None
    confidence: str
    explanation: str
    fact: str
    model: str
    settings: dict
    prompt_hash: str
    elapsed_s: float            # submit -> result, wall clock (includes image prep)
    load_s: float               # model load time reported by Ollama (large on a cold start)
    raw: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- prompt / image

def load_prompt(path: str | Path) -> tuple[str, str]:
    """Return (prompt_text, short_sha256). The hash identifies the exact prompt used."""
    text = Path(path).read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prepare_image(path: str | Path, max_side: int) -> str:
    """Fix EXIF rotation, shrink so the longest side is <= max_side, return base64 JPEG."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------- parsing

def _extract_json(raw: str) -> dict | None:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_result(raw: str) -> dict | None:
    """Validate model output. Returns clean fields, or None if malformed."""
    obj = _extract_json(raw)
    if obj is None:
        return None
    category = str(obj.get("category", "")).strip().lower()
    confidence = str(obj.get("confidence", "")).strip().capitalize()
    if category not in CATEGORIES or confidence not in CONFIDENCES:
        return None
    ident = obj.get("identification")
    ident = ident.strip() if isinstance(ident, str) and ident.strip() else None
    if ident and ident.lower() in ("null", "none", "unknown", "n/a"):
        ident = None
    # A confident answer with nothing identified is contradictory: downgrade.
    if ident is None:
        confidence = "Low"
    return {
        "category": category,
        "identification": ident,
        "confidence": confidence,
        "explanation": str(obj.get("explanation") or "").strip(),
        "fact": str(obj.get("fact") or "").strip(),
    }


# ----------------------------------------------------------------- Ollama call

def _post_chat(host: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _merge_settings(overrides: dict | None) -> dict:
    s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (overrides or {}).items():
        if k == "options":
            s["options"].update(v)
        else:
            s[k] = v
    return s


def identify(
    image_path: str | Path,
    model: str,
    prompt: str,
    prompt_hash: str = "",
    settings: dict | None = None,
    host: str = OLLAMA_URL,
    timeout: float = 300.0,
) -> IdentifyResult:
    s = _merge_settings(settings)
    t0 = time.perf_counter()

    def fail(status: str, raw: str, err: str | None, load_s: float = 0.0) -> IdentifyResult:
        return IdentifyResult(
            status=status, category="unknown", identification=None, confidence="Low",
            explanation="", fact="", model=model, settings=s, prompt_hash=prompt_hash,
            elapsed_s=round(time.perf_counter() - t0, 2), load_s=load_s, raw=raw, error=err,
        )

    try:
        b64 = prepare_image(image_path, int(s["max_side"]))
    except Exception as e:  # unreadable / corrupt image
        return fail("error", "", f"image: {e}")

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": s["keep_alive"],
        "options": s["options"],
        # Image is attached to the same user message as the prompt; Ollama places
        # it ahead of the text in the model's template.
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
    }
    if s.get("think") is not None:
        payload["think"] = bool(s["think"])

    try:
        try:
            resp = _post_chat(host, payload, timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 400 and "think" in body.lower() and "think" in payload:
                payload.pop("think")           # server/model rejects the field: retry without
                s["think"] = "not-supported-by-server"
                resp = _post_chat(host, payload, timeout)
            else:
                return fail("error", body, f"HTTP {e.code}: {body[:200]}")
    except Exception as e:  # connection refused, timeout, bad JSON...
        return fail("error", "", f"{type(e).__name__}: {e}")

    raw = (resp.get("message") or {}).get("content", "") or ""
    load_s = round((resp.get("load_duration") or 0) / 1e9, 2)
    parsed = parse_result(raw)
    elapsed = round(time.perf_counter() - t0, 2)

    if parsed is None:
        return fail("malformed", raw, "model output was not valid result JSON", load_s)
    return IdentifyResult(
        status="ok", model=model, settings=s, prompt_hash=prompt_hash,
        elapsed_s=elapsed, load_s=load_s, raw=raw, **parsed,
    )


# ----------------------------------------------------------------- app safety (placeholder)

_WARN_CATEGORIES = {"fungi", "reptile", "unknown"}
_LIVING = {"bird", "butterfly", "insect", "plant", "tree", "flower", "mammal"}


def needs_safety_warning(result: IdentifyResult) -> bool:
    """Should the app add its own 'don't touch or eat it' note, regardless of the model's text?

    FR-07. Placeholder so the evaluation can check the fungi probes; move this
    into server/safety.py when that module is written.
    """
    if result.status != "ok":
        return True
    if result.category in _WARN_CATEGORIES:
        return True
    return result.confidence == "Low" and result.category in _LIVING