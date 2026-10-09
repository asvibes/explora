"""Safety for what Explora *generates* (PRD section 13).

Two jobs:
1. App-added warnings (FR-07) for fungi, unknown organisms and potentially
   dangerous animals. These come from the app, independent of model output.
2. Banned-phrase scans: quests/activities must never invite touching, eating,
   roads, etc.; generated facts must never give unsafe *reassurance*
   ("safe to eat"). Warnings such as "do not touch" are fine in facts.

The user's private journal is never scanned. That is a deliberate non-goal.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

BANNED_FILE = Path(__file__).resolve().parent / "content" / "banned_phrases.txt"

WARNING_UNKNOWN = "Not sure what this is. Please don't touch or eat it."
WARNING_FUNGI = "Fungi can be dangerous. Please don't touch or eat it."
WARNING_ANIMAL = "Please keep your distance and don't touch it."

_WARN_CATEGORIES = {"fungi", "reptile", "unknown"}
_LIVING = {"bird", "butterfly", "insect", "plant", "tree", "flower", "mammal"}


def needs_warning(category: str | None, confidence: str | None, status: str | None = "ok") -> bool:
    if status != "ok":
        return True                       # malformed output = "not sure"
    if category in _WARN_CATEGORIES:
        return True
    return confidence == "Low" and category in _LIVING


def warning_text(category: str | None, confidence: str | None, status: str | None = "ok") -> str | None:
    if not needs_warning(category, confidence, status):
        return None
    if category == "fungi":
        return WARNING_FUNGI
    if category == "reptile":
        return WARNING_ANIMAL
    return WARNING_UNKNOWN


# --------------------------------------------------------------- banned phrases

@lru_cache(maxsize=4)
def load_banned(path: str | None = None) -> dict[str, tuple[str, ...]]:
    """Parse banned_phrases.txt: `[quests]` and `[facts]` sections, one phrase per line."""
    p = Path(path) if path else BANNED_FILE
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line.lower())
    return {k: tuple(v) for k, v in sections.items()}


def _pattern(phrase: str) -> re.Pattern[str]:
    """Word-start match that tolerates suffixes ("touch" -> "touching") and hyphen/space variants."""
    tokens = [re.escape(t) for t in re.split(r"[\s-]+", phrase.strip()) if t]
    return re.compile(r"\b" + r"[\s-]+".join(tokens) + r"\w*", re.IGNORECASE)


_NEGATORS = {"not", "no", "never", "isn't", "isnt", "aren't", "arent", "cannot"}


def _negated(text: str, start: int) -> bool:
    words = re.findall(r"[\w']+", text[:start].lower())[-2:]
    return any(w in _NEGATORS or w.endswith("n't") for w in words)


def find_banned(text: str, phrases, ignore_negated: bool = False) -> list[str]:
    """Return the banned phrases found in `text` (empty list = clean)."""
    hits: list[str] = []
    for phrase in phrases:
        for m in _pattern(phrase).finditer(text or ""):
            if ignore_negated and _negated(text, m.start()):
                continue
            hits.append(phrase)
            break
    return hits


def quest_violations(text: str) -> list[str]:
    """Banned phrases in a quest or generated activity (no exceptions, no negation pass)."""
    return find_banned(text, load_banned().get("quests", ()))


def unsafe_reassurance(text: str) -> list[str]:
    """Unsafe reassurance in generated facts/identification text.

    "Not safe to eat" is a warning and passes; "safe to eat" is reassurance and is flagged.
    """
    return find_banned(text, load_banned().get("facts", ()), ignore_negated=True)