"""Shared vocabulary: statuses, habitats, and the confidence -> wording rule."""
from __future__ import annotations

from enum import Enum

from .identify import CATEGORIES


class Status(str, Enum):
    IMPORTED = "imported"
    IDENTIFIED = "identified"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SAVED_UNIDENTIFIED = "saved_unidentified"


# Walk habitats. Streets and water edges are deliberately absent (road/water safety).
HABITATS = ("park", "garden", "campus", "trail", "backyard")

# What a person may correct a discovery to. "unknown" and "not_nature" are model-only answers.
USER_CATEGORIES = tuple(c for c in CATEGORIES if c not in ("unknown", "not_nature"))

NOT_SURE = "Not sure what this is."


def with_article(name: str) -> str:
    n = name.strip()
    if n.lower().startswith(("a ", "an ", "the ", "some ")):
        return n
    return ("an " if n[:1].lower() in "aeiou" else "a ") + n


def suggestion_text(identification: str | None, confidence: str | None, ai_status: str | None = "ok") -> str:
    """Wording follows confidence (PRD 10.5). Never a percentage.

    High   -> "Looks like a Blue Jay."
    Medium -> "Might be a Blue Jay."
    Low / unknown / malformed -> "Not sure what this is."
    """
    if ai_status != "ok" or not identification or confidence not in ("High", "Medium"):
        return NOT_SURE
    prefix = "Looks like" if confidence == "High" else "Might be"
    return f"{prefix} {with_article(identification)}."