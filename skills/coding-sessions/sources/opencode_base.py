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


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of a table; empty when it does not exist."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {row[1] for row in rows}


def stat_columns_sql(conn: sqlite3.Connection, table: str) -> str:
    """SQL fragment selecting cost, tokens_input and tokens_output.

    Prompt tokens include cache reads and cache writes when those columns
    exist, which matches how the other sources count prompt tokens. Older
    schemas without the cache columns still work.
    """
    cols = table_columns(conn, table)
    cost = "coalesce(s.cost, 0)" if "cost" in cols else "0.0"
    input_parts = (["coalesce(s.tokens_input, 0)"]
                   if "tokens_input" in cols else ["0"])
    if "tokens_cache_read" in cols:
        input_parts.append("coalesce(s.tokens_cache_read, 0)")
    if "tokens_cache_write" in cols:
        input_parts.append("coalesce(s.tokens_cache_write, 0)")
    output = "coalesce(s.tokens_output, 0)" if "tokens_output" in cols else "0"
    return (f"{cost} AS cost,\n"
            f"       {' + '.join(input_parts)} AS tokens_input,\n"
            f"       {output} AS tokens_output")


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
