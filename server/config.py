"""Explora configuration. Everything is local; nothing here points at the internet.

Override with environment variables:
    EXPLORA_DATA     where photos + the database live (default: <repo>/data)
    EXPLORA_MODEL    Ollama model tag (default: gemma4:e2b; set after the evaluation)
    EXPLORA_OLLAMA   Ollama URL (default: http://localhost:11434)
    EXPLORA_PROMPT   prompt file (default: eval/prompts/frozen.txt)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    model: str = "gemma4:e2b"
    ollama_url: str = "http://localhost:11434"
    prompt_path: Path = ROOT / "eval" / "prompts" / "frozen.txt"
    display_max_side: int = 1024     # stored copy used for display and the model
    identify_max_side: int = 768     # longest side actually sent to the model

    @property
    def db_path(self) -> Path:
        return self.data_dir / "explora.db"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def journal_dir(self) -> Path:
        return self.data_dir / "journal"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.photos_dir, self.journal_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolved_prompt(self) -> Path:
        """The frozen prompt if it exists; in development fall back to v1.txt."""
        if self.prompt_path.exists():
            return self.prompt_path
        fallback = self.prompt_path.with_name("v1.txt")
        if fallback.exists():
            return fallback
        raise FileNotFoundError(f"No prompt file found at {self.prompt_path}")

    def with_(self, **changes) -> "Config":
        return replace(self, **changes)


def load_config(**overrides) -> Config:
    cfg = Config(
        data_dir=Path(os.environ.get("EXPLORA_DATA", ROOT / "data")),
        model=os.environ.get("EXPLORA_MODEL", "gemma4:e2b"),
        ollama_url=os.environ.get("EXPLORA_OLLAMA", "http://localhost:11434"),
        prompt_path=Path(os.environ.get("EXPLORA_PROMPT", ROOT / "eval" / "prompts" / "frozen.txt")),
    )
    return replace(cfg, **overrides) if overrides else cfg