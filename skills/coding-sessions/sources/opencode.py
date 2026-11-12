"""opencode union session source adapter.

A database can contain the legacy V1 tables (session, message, part, todo),
the V2 tables (session_v2, session_message, session_part), or both. The union
adapter reads every schema present and de-duplicates sessions by ID, keeping
the newest copy (highest time_updated). Per-session reads dispatch to the
adapter that owns the schema the handle came from.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .base import (
    AssistantText,
    AwaySummary,
    CompactBoundary,
    Prompt,
    ReasoningEntry,
    SessionMeta,
    SessionRow,
    SessionSource,
    SubagentInfo,
    TimelineEvent,
    TodoEntry,
    ToolCall,
)
from .opencode_base import connect, db_path, detect_schemas
from .opencode_v1 import OpencodeV1Source
from .opencode_v2 import OpencodeV2Source


@dataclass(frozen=True)
class OpencodeHandle:
    schema: str
    session_id: str


class OpencodeSource(SessionSource):
    name = "opencode"

    def __init__(self) -> None:
        self._v1 = OpencodeV1Source()
        self._v2 = OpencodeV2Source()

    def _adapter(self, schema: str) -> SessionSource:
        return self._v1 if schema == "v1" else self._v2

    def _adapters(self) -> list[tuple[str, SessionSource]]:
        return [(schema, self._adapter(schema)) for schema in detect_schemas()]

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        schemas = detect_schemas()
        if not schemas:
            db = db_path()
            if not db.is_file():
                raise FileNotFoundError(f"opencode database not found at {db}")
            probe = connect()
            try:
                probe.execute("SELECT 1").fetchone()
            finally:
                probe.close()
            return []
        best: dict[str, SessionRow] = {}
        for schema in schemas:
            adapter = self._adapter(schema)
            try:
                rows = adapter.list_sessions(dir_path, all_dirs, limit=limit)
            except (sqlite3.Error, OSError):
                continue
            for row in rows:
                current = best.get(row.session_id)
                if current is None or row.last_ts >= current.last_ts:
                    best[row.session_id] = row
        return list(best.values())

    def find(self, session_id: str, dir_path: str | None) -> OpencodeHandle | None:
        candidates: list[tuple[int, str]] = []
        for schema, adapter in self._adapters():
            try:
                if adapter.find(session_id, dir_path) is None:
                    continue
                updated = adapter.time_updated_ms(session_id) or 0
            except (FileNotFoundError, sqlite3.Error):
                continue
            candidates.append((updated, schema))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], 0 if item[1] == "v1" else 1))
        return OpencodeHandle(candidates[-1][1], session_id)

    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        return self._adapter(handle.schema).metadata(handle.session_id, session_id)

    def user_prompts(self, handle: Any) -> list[Prompt]:
        return self._adapter(handle.schema).user_prompts(handle.session_id)

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        return self._adapter(handle.schema).assistant_texts(handle.session_id)

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        return self._adapter(handle.schema).tool_calls(handle.session_id)

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        return self._adapter(handle.schema).compact_boundaries(handle.session_id)

    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        return self._adapter(handle.schema).away_summaries(handle.session_id)

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        return self._adapter(handle.schema).reasoning(handle.session_id)

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        return self._adapter(handle.schema).subagents(handle.session_id)

    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        return self._adapter(handle.schema).timeline_events(handle.session_id)

    def todos(self, session_id: str) -> list[TodoEntry]:
        for _schema, adapter in self._adapters():
            try:
                rows = adapter.todos(session_id)
            except (FileNotFoundError, sqlite3.Error):
                continue
            if rows:
                return rows
        return []
