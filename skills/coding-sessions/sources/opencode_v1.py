"""opencode V1 session source adapter.

V1 stores sessions in the tables session, message, part and todo. Assistant
content is split into part rows whose data JSON is tagged by part type.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .base import (
    AssistantText,
    AwaySummary,
    CompactBoundary,
    EventKind,
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
from .opencode_base import connect, decode_json, ms_to_iso

_LIST_SQL = """
SELECT id, title, directory,
       (SELECT count(*) FROM message m WHERE m.session_id = s.id) AS msgs,
       (SELECT count(*) FROM part p WHERE p.session_id = s.id
          AND json_extract(p.data, '$.type') = 'tool') AS tool_calls,
       (SELECT count(*) FROM part p WHERE p.session_id = s.id
          AND json_extract(p.data, '$.type') = 'text') AS text_parts,
       (SELECT count(*) FROM part p WHERE p.session_id = s.id
          AND json_extract(p.data, '$.type') = 'compaction') AS compacts,
       time_created, time_updated
FROM session s
{where}
ORDER BY s.time_created DESC LIMIT ?
"""

_PROMPT_SQL = """
SELECT p.time_created, p.data
FROM part p JOIN message m ON p.message_id = m.id
WHERE p.session_id = ?
  AND json_extract(m.data, '$.role') = 'user'
  AND json_extract(p.data, '$.type') = 'text'
  AND coalesce(json_extract(p.data, '$.synthetic'), 0) = 0
  AND coalesce(json_extract(p.data, '$.ignored'), 0) = 0
ORDER BY p.time_created, p.id
"""

_ASSISTANT_SQL = """
SELECT p.time_created, p.data
FROM part p JOIN message m ON p.message_id = m.id
WHERE p.session_id = ?
  AND json_extract(m.data, '$.role') = 'assistant'
  AND json_extract(p.data, '$.type') = 'text'
  AND coalesce(json_extract(p.data, '$.synthetic'), 0) = 0
ORDER BY p.time_created, p.id
"""

_TIMELINE_SQL = """
SELECT p.time_created AS p_ts,
       json_extract(m.data, '$.role') AS role,
       p.data
FROM part p JOIN message m ON p.message_id = m.id
WHERE p.session_id = ?
ORDER BY m.time_created, m.id, p.time_created, p.id
"""


class OpencodeV1Source(SessionSource):
    name = "opencode"

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        conn = connect()
        try:
            if all_dirs or not dir_path:
                rows = conn.execute(_LIST_SQL.format(where=""), (limit,)).fetchall()
            else:
                rows = conn.execute(
                    _LIST_SQL.format(where="WHERE s.directory = ?"), (dir_path, limit)
                ).fetchall()
            result: list[SessionRow] = []
            for r in rows:
                result.append(SessionRow(
                    source="opencode",
                    session_id=r["id"],
                    project=r["directory"] or "",
                    title=r["title"] or "",
                    lines=r["msgs"] or 0,
                    user_prompts=self._prompt_count(conn, r["id"]),
                    tool_calls=r["tool_calls"] or 0,
                    assistant_texts=r["text_parts"] or 0,
                    compacts=r["compacts"] or 0,
                    last_ts=ms_to_iso(r["time_updated"] or r["time_created"]),
                    file_size=0,
                ))
            return result
        finally:
            conn.close()

    @staticmethod
    def _prompt_count(conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute(
            "SELECT count(*) AS n FROM part p JOIN message m ON p.message_id = m.id "
            "WHERE p.session_id = ? AND json_extract(m.data, '$.role') = 'user' "
            "  AND json_extract(p.data, '$.type') = 'text' "
            "  AND coalesce(json_extract(p.data, '$.synthetic'), 0) = 0 "
            "  AND coalesce(json_extract(p.data, '$.ignored'), 0) = 0",
            (session_id,),
        ).fetchone()
        return row["n"] or 0

    def find(self, session_id: str, dir_path: str | None) -> str | None:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT id FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def time_updated_ms(self, session_id: str) -> int | None:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT time_updated FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            return row["time_updated"] if row else None
        finally:
            conn.close()

    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        conn = connect()
        try:
            r = conn.execute(
                "SELECT id, title, directory, agent, model, version, cost, "
                "tokens_input, tokens_output, time_created, time_updated, "
                "(SELECT count(*) FROM part p WHERE p.session_id = s.id "
                "  AND json_extract(p.data, '$.type') = 'compaction') AS compact_count, "
                "(SELECT count(*) FROM part p WHERE p.session_id = s.id) AS part_count "
                "FROM session s WHERE s.id = ?",
                (session_id,),
            ).fetchone()
            if not r:
                return SessionMeta(
                    source="opencode", session_id=session_id, title="",
                    cwd="", model="", version="", git_branch="",
                    line_count=0, first_ts="", last_ts="",
                    compact_count=0, file_size=0,
                )
            model = decode_json(r["model"]).get("id", "")
            return SessionMeta(
                source="opencode",
                session_id=r["id"],
                title=r["title"] or "",
                cwd=r["directory"] or "",
                model=model,
                version=r["version"] or "",
                git_branch="",
                line_count=r["part_count"] or 0,
                first_ts=ms_to_iso(r["time_created"]),
                last_ts=ms_to_iso(r["time_updated"]),
                compact_count=r["compact_count"] or 0,
                file_size=0,
                cost=r["cost"] or 0.0,
                tokens_input=r["tokens_input"] or 0,
                tokens_output=r["tokens_output"] or 0,
                duration_s=((r["time_updated"] or 0) - (r["time_created"] or 0)) // 1000,
                agent=r["agent"] or "",
            )
        finally:
            conn.close()

    def user_prompts(self, handle: Any) -> list[Prompt]:
        conn = connect()
        try:
            rows = conn.execute(_PROMPT_SQL, (handle,)).fetchall()
            prompts: list[Prompt] = []
            for p in rows:
                text = decode_json(p["data"]).get("text", "")
                if text:
                    prompts.append(Prompt(
                        text=text,
                        timestamp=ms_to_iso(p["time_created"]),
                    ))
            return prompts
        finally:
            conn.close()

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        conn = connect()
        try:
            rows = conn.execute(_ASSISTANT_SQL, (handle,)).fetchall()
            texts: list[AssistantText] = []
            for p in rows:
                text = decode_json(p["data"]).get("text", "")
                if text:
                    texts.append(AssistantText(
                        text=text,
                        timestamp=ms_to_iso(p["time_created"]),
                    ))
            return texts
        finally:
            conn.close()

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT p.time_created, p.data FROM part p "
                "WHERE p.session_id = ? "
                "  AND json_extract(p.data, '$.type') = 'tool' "
                "ORDER BY p.time_created, p.id",
                (handle,),
            ).fetchall()
            calls: list[ToolCall] = []
            for p in rows:
                data = decode_json(p["data"])
                state = data.get("state", {})
                inp = state.get("input", {})
                out = state.get("output", "")
                calls.append(ToolCall(
                    name=data.get("tool", ""),
                    input=json.dumps(inp) if inp else "",
                    timestamp=ms_to_iso(p["time_created"]),
                    status=state.get("status", ""),
                    tool_id=data.get("callID", ""),
                    output=out if isinstance(out, str) else str(out),
                ))
            return calls
        finally:
            conn.close()

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT p.time_created, p.data FROM part p "
                "WHERE p.session_id = ? "
                "  AND json_extract(p.data, '$.type') = 'compaction' "
                "ORDER BY p.time_created, p.id",
                (handle,),
            ).fetchall()
            boundaries: list[CompactBoundary] = []
            for p in rows:
                data = decode_json(p["data"])
                boundaries.append(CompactBoundary(
                    timestamp=ms_to_iso(p["time_created"]),
                    trigger="auto" if data.get("auto") else "manual",
                    pre_tokens=0,
                    post_tokens=0,
                    dropped_tokens=0,
                    duration_ms=0,
                ))
            return boundaries
        finally:
            conn.close()

    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT p.message_id, p.time_created, p.data FROM part p "
                "JOIN message m ON p.message_id = m.id "
                "WHERE p.session_id = ? "
                "  AND json_extract(m.data, '$.role') = 'assistant' "
                "  AND json_extract(m.data, '$.summary') = 1 "
                "  AND json_extract(p.data, '$.type') = 'text' "
                "ORDER BY p.time_created, p.id",
                (handle,),
            ).fetchall()
            grouped: list[tuple[str, int, list[str]]] = []
            index: dict[str, int] = {}
            for r in rows:
                text = decode_json(r["data"]).get("text", "")
                if not text:
                    continue
                mid = r["message_id"]
                if mid not in index:
                    index[mid] = len(grouped)
                    grouped.append((mid, r["time_created"], [text]))
                else:
                    grouped[index[mid]][2].append(text)
            return [
                AwaySummary(
                    timestamp=ms_to_iso(ts),
                    content="\n".join(texts),
                )
                for _, ts, texts in grouped
            ]
        finally:
            conn.close()

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT p.time_created, p.data FROM part p "
                "WHERE p.session_id = ? "
                "  AND json_extract(p.data, '$.type') = 'reasoning' "
                "ORDER BY p.time_created, p.id",
                (handle,),
            ).fetchall()
            entries: list[ReasoningEntry] = []
            for p in rows:
                text = decode_json(p["data"]).get("text", "")
                if text:
                    entries.append(ReasoningEntry(
                        text=text,
                        timestamp=ms_to_iso(p["time_created"]),
                    ))
            return entries
        finally:
            conn.close()

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id, title, agent FROM session WHERE parent_id = ? "
                "ORDER BY time_created", (handle,)
            ).fetchall()
            return [
                SubagentInfo(
                    agent_type=r["agent"] or "",
                    description=r["title"] or "",
                    agent_id=r["id"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        conn = connect()
        try:
            rows = conn.execute(_TIMELINE_SQL, (handle,)).fetchall()
            events: list[TimelineEvent] = []
            for p in rows:
                data = decode_json(p["data"])
                ptype = data.get("type", "")
                ts = ms_to_iso(p["p_ts"])
                role = p["role"] or ""
                if ptype == "text":
                    if data.get("synthetic") or data.get("ignored"):
                        continue
                    kind = EventKind.USER_MESSAGE if role == "user" else EventKind.ASSISTANT_TEXT
                    events.append(TimelineEvent(
                        timestamp=ts, kind=kind, text=data.get("text", ""),
                    ))
                elif ptype == "tool":
                    state = data.get("state", {})
                    inp = state.get("input", {})
                    out = state.get("output", "")
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TOOL_CALL,
                        tool_name=data.get("tool", ""),
                        tool_input=json.dumps(inp) if inp else "",
                        tool_output=out if isinstance(out, str) else str(out),
                        tool_status=state.get("status", ""),
                        tool_id=data.get("callID", ""),
                    ))
                elif ptype == "reasoning":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.REASONING,
                        text=data.get("text", ""),
                    ))
                elif ptype == "file":
                    src = data.get("source", {})
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.FILE_EDIT,
                        text=src.get("path", data.get("filename", "")),
                    ))
                elif ptype == "compaction":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.COMPACTION,
                        metadata={
                            "auto": data.get("auto", False),
                            "overflow": data.get("overflow", False),
                        },
                    ))
            return events
        finally:
            conn.close()

    def todos(self, session_id: str) -> list[TodoEntry]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT content, status, priority, position "
                "FROM todo WHERE session_id = ? ORDER BY position",
                (session_id,),
            ).fetchall()
            return [
                TodoEntry(
                    content=r["content"],
                    status=r["status"],
                    priority=r["priority"],
                    position=r["position"],
                )
                for r in rows
            ]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
