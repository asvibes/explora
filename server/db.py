"""SQLite storage. One file on the user's laptop, no server, no accounts.

The whole PRD data model (section 12) is created up front so later modules
(journal, quests, firsts) need no migration. Nothing here tracks "completeness":
a walk is never incomplete, and quests that were not done are simply not stored.
"""
from __future__ import annotations

import sqlite3

from .config import Config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS walks (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL UNIQUE,          -- one walk per day (YYYY-MM-DD)
    area         TEXT,                          -- typed, optional, never GPS
    habitat      TEXT,                          -- optional, from HABITATS
    duration_min INTEGER,                       -- optional
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discoveries (
    id                  INTEGER PRIMARY KEY,
    walk_id             INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    photo_path          TEXT NOT NULL,          -- resized copy, relative to the data dir
    content_hash        TEXT NOT NULL UNIQUE,   -- duplicates only, not authenticity
    original_name       TEXT,
    captured_at         TEXT NOT NULL,          -- ISO datetime, editable
    captured_source     TEXT NOT NULL,          -- exif | file | unknown | user
    status              TEXT NOT NULL DEFAULT 'imported',
    ai_status           TEXT,                   -- ok | malformed (NULL = never identified)
    ai_category         TEXT,
    ai_identification   TEXT,
    ai_confidence       TEXT,
    ai_explanation      TEXT,
    ai_fact             TEXT,
    ai_fact_blocked     INTEGER NOT NULL DEFAULT 0,
    ai_model            TEXT,
    ai_settings         TEXT,                   -- JSON, so a result can be reproduced
    ai_prompt_hash      TEXT,
    ai_time_s           REAL,
    final_label         TEXT,                   -- what the user confirmed or typed
    final_category      TEXT,
    final_source        TEXT,                   -- confirmed | corrected
    user_note           TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discoveries_walk ON discoveries(walk_id);

CREATE TABLE IF NOT EXISTS journal_pages (
    walk_id       INTEGER PRIMARY KEY REFERENCES walks(id) ON DELETE CASCADE,
    left_image    TEXT,                         -- handwritten page photo (never read or OCR'd)
    left_rotation INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS polaroids (
    walk_id      INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    slot         INTEGER NOT NULL CHECK (slot IN (1, 2)),
    discovery_id INTEGER REFERENCES discoveries(id) ON DELETE SET NULL,
    caption      TEXT NOT NULL DEFAULT '',
    removed      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (walk_id, slot)
);

CREATE TABLE IF NOT EXISTS quest_completions (
    id           INTEGER PRIMARY KEY,
    walk_id      INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    template_id  TEXT NOT NULL,
    wording      TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS firsts (
    id           INTEGER PRIMARY KEY,
    category     TEXT NOT NULL UNIQUE,
    discovery_id INTEGER NOT NULL REFERENCES discoveries(id) ON DELETE CASCADE,
    walk_id      INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL
);
"""


def connect(cfg: Config) -> sqlite3.Connection:
    """Open (and create if needed) the database. Open one connection per request/thread."""
    cfg.ensure_dirs()
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()