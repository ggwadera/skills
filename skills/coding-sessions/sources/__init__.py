"""Session source registry and auto-detection.

Each source is a SessionSource subclass. The registry maps the --source name
to the class. Auto-detection tries sources in precedence order until one
returns a handle for the session ID.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .base import SessionSource
from .claude import ClaudeSource
from .codex import CodexSource
from .dsh import DshSource
from .opencode import OpencodeSource

#: Maps --source value to adapter instance
SOURCES: dict[str, SessionSource] = {
    "claude": ClaudeSource(),
    "codex": CodexSource(),
    "opencode": OpencodeSource(),
    "dsh": DshSource(),
}

#: Precedence order for auto-detection (opencode first: ses_ prefix is indexed)
AUTO_DETECT_ORDER: list[str] = ["opencode", "claude", "codex", "dsh"]


def get_source(name: str) -> SessionSource:
    if name not in SOURCES:
        raise ValueError(f"Unknown source: {name}. Available: {list(SOURCES)}")
    return SOURCES[name]


def detect_source(session_id: str, dir_path: str | None = None) -> tuple[SessionSource, Any] | None:
    """Try each source in precedence order. Return (adapter, handle) for the first match."""
    for name in AUTO_DETECT_ORDER:
        source = SOURCES[name]
        try:
            handle = source.find(session_id, dir_path)
        except (OSError, sqlite3.Error):
            continue
        if handle is not None:
            return source, handle
    return None


def resolve_session(session_id: str, source: str = "auto",
                    dir_path: str | None = None) -> tuple[SessionSource, Any] | None:
    """Find a session by ID, optionally guided by --source.

    Returns (adapter, handle) or None.
    """
    if source == "auto":
        return detect_source(session_id, dir_path)
    adapter = get_source(source)
    try:
        handle = adapter.find(session_id, dir_path)
    except (OSError, sqlite3.Error):
        return None
    if handle is None:
        return None
    return adapter, handle