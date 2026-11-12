"""Shared helpers for the opencode V1 and V2 session adapters."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def db_path() -> Path:
    return Path(os.environ.get("OPENCODE_DB", DEFAULT_DB))


def connect() -> sqlite3.Connection:
    db = db_path()
    if not db.is_file():
        raise FileNotFoundError(f"opencode database not found at {db}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ms_to_iso(ms: int | None) -> str:
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_json(text) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def detect_schemas() -> list[str]:
    """Return the storage schemas present in the database, oldest first."""
    try:
        conn = connect()
    except (FileNotFoundError, sqlite3.Error):
        return []
    try:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()}
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    schemas: list[str] = []
    if {"session", "message", "part"} <= names:
        schemas.append("v1")
    if {"session_v2", "session_message"} <= names:
        schemas.append("v2")
    return schemas
