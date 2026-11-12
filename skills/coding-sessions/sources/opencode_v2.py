"""opencode V2 session source adapter.

V2 stores sessions in session_v2, session_message and (optionally) session_part.
Messages are type-tagged; assistant content lives inline in data.content by
default, or in session_part rows when experimental.part_rows is enabled, or a
mix of both. This adapter merges both layouts per message.
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
    ToolCall,
)
from .opencode_base import connect, decode_json, ms_to_iso

_LIST_HEAD = """
SELECT id, title, directory,
       (SELECT count(*) FROM session_message m WHERE m.session_id = s.id) AS msgs,
       (SELECT count(*) FROM session_message m WHERE m.session_id = s.id
          AND m.type = 'user') AS user_msgs,
       {tool_count} AS tool_calls,
       (SELECT count(*) FROM session_message m WHERE m.session_id = s.id
          AND m.type = 'compaction') AS compacts,
       time_created, time_updated
FROM session_v2 s
{where}
ORDER BY s.time_created DESC LIMIT ?
"""

_TOOL_COUNT_INLINE = """(SELECT count(*) FROM session_message m, json_each(m.data, '$.content') c
          WHERE m.session_id = s.id AND m.type = 'assistant'
            AND json_extract(c.value, '$.type') = 'tool')"""

_TOOL_COUNT_WITH_PARTS = """((SELECT count(*) FROM session_part p WHERE p.session_id = s.id
          AND p.type = 'tool') + """ + _TOOL_COUNT_INLINE + ')'


def _block_timestamp(block: dict, fallback_ms: int | None) -> str:
    time = block.get("time") or {}
    created = time.get("created") if isinstance(time, dict) else None
    if isinstance(created, (int, float)) and created:
        return ms_to_iso(int(created))
    return ms_to_iso(fallback_ms)


def _tool_output(state: dict) -> str:
    if not isinstance(state, dict):
        return ""
    if state.get("status") == "error":
        error = state.get("error") or {}
        if isinstance(error, dict):
            return error.get("message", "") or str(error)
        return str(error)
    content = state.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "file":
                parts.append(f"[file: {block.get('uri') or block.get('name') or ''}]")
        return "\n".join(parts)
    return ""


class OpencodeV2Source(SessionSource):
    name = "opencode"

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone() is not None

    def _load_messages(self, conn: sqlite3.Connection, session_id: str) -> list[dict]:
        messages = conn.execute(
            "SELECT id, type, seq, time_created, data FROM session_message "
            "WHERE session_id = ? ORDER BY seq, id", (session_id,)
        ).fetchall()
        part_rows = []
        if self._table_exists(conn, "session_part"):
            part_rows = conn.execute(
                "SELECT message_id, ordinal, type, call_id, data FROM session_part "
                "WHERE session_id = ? ORDER BY message_id, ordinal", (session_id,)
            ).fetchall()
        parts_by_message: dict[str, list[sqlite3.Row]] = {}
        for part in part_rows:
            parts_by_message.setdefault(part["message_id"], []).append(part)

        result: list[dict] = []
        for msg in messages:
            data = decode_json(msg["data"])
            blocks: list[dict] = []
            for part in parts_by_message.get(msg["id"], []):
                block = decode_json(part["data"])
                if "type" not in block:
                    block["type"] = part["type"]
                if part["type"] == "tool" and "id" not in block and part["call_id"]:
                    block["id"] = part["call_id"]
                blocks.append(block)
            inline = data.get("content") or []
            if isinstance(inline, list):
                blocks.extend(b for b in inline if isinstance(b, dict))
            result.append({
                "id": msg["id"],
                "type": msg["type"],
                "time_created": msg["time_created"],
                "data": data,
                "blocks": blocks,
            })
        return result

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        conn = connect()
        try:
            tool_count = (_TOOL_COUNT_WITH_PARTS
                          if self._table_exists(conn, "session_part")
                          else _TOOL_COUNT_INLINE)
            if all_dirs or not dir_path:
                rows = conn.execute(
                    _LIST_HEAD.format(where="", tool_count=tool_count), (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    _LIST_HEAD.format(where="WHERE s.directory = ?", tool_count=tool_count),
                    (dir_path, limit),
                ).fetchall()
            result: list[SessionRow] = []
            for r in rows:
                result.append(SessionRow(
                    source="opencode",
                    session_id=r["id"],
                    project=r["directory"] or "",
                    title=r["title"] or "",
                    lines=r["msgs"] or 0,
                    user_prompts=r["user_msgs"] or 0,
                    tool_calls=r["tool_calls"] or 0,
                    assistant_texts=0,
                    compacts=r["compacts"] or 0,
                    last_ts=ms_to_iso(r["time_updated"] or r["time_created"]),
                    file_size=0,
                ))
            return result
        finally:
            conn.close()

    def find(self, session_id: str, dir_path: str | None) -> str | None:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT id FROM session_v2 WHERE id = ?", (session_id,)
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def time_updated_ms(self, session_id: str) -> int | None:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT time_updated FROM session_v2 WHERE id = ?", (session_id,)
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
                "(SELECT count(*) FROM session_message m WHERE m.session_id = s.id "
                "  AND m.type = 'compaction') AS compact_count, "
                "(SELECT count(*) FROM session_message m WHERE m.session_id = s.id) AS msg_count "
                "FROM session_v2 s WHERE s.id = ?",
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
                line_count=r["msg_count"] or 0,
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
            prompts: list[Prompt] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "user":
                    continue
                data = msg["data"]
                text = data.get("text", "")
                if not text:
                    continue
                time = data.get("time") or {}
                created = time.get("created") if isinstance(time, dict) else None
                ts = ms_to_iso(created) if isinstance(created, (int, float)) and created else ms_to_iso(msg["time_created"])
                prompts.append(Prompt(text=text, timestamp=ts))
            return prompts
        finally:
            conn.close()

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        conn = connect()
        try:
            texts: list[AssistantText] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "assistant":
                    continue
                for block in msg["blocks"]:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            texts.append(AssistantText(
                                text=text,
                                timestamp=_block_timestamp(block, msg["time_created"]),
                            ))
            return texts
        finally:
            conn.close()

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        conn = connect()
        try:
            calls: list[ToolCall] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "assistant":
                    continue
                for block in msg["blocks"]:
                    if block.get("type") != "tool":
                        continue
                    state = block.get("state") or {}
                    inp = state.get("input") if isinstance(state, dict) else None
                    calls.append(ToolCall(
                        name=block.get("name", ""),
                        input=json.dumps(inp) if inp else "",
                        timestamp=_block_timestamp(block, msg["time_created"]),
                        status=state.get("status", "") if isinstance(state, dict) else "",
                        tool_id=block.get("id", ""),
                        output=_tool_output(state),
                    ))
            return calls
        finally:
            conn.close()

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        conn = connect()
        try:
            boundaries: list[CompactBoundary] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "compaction":
                    continue
                reason = msg["data"].get("reason", "")
                boundaries.append(CompactBoundary(
                    timestamp=ms_to_iso(msg["time_created"]),
                    trigger=reason or "manual",
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
            summaries: list[AwaySummary] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "compaction":
                    continue
                summary = msg["data"].get("summary", "")
                if summary:
                    summaries.append(AwaySummary(
                        timestamp=ms_to_iso(msg["time_created"]),
                        content=summary,
                    ))
            return summaries
        finally:
            conn.close()

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        conn = connect()
        try:
            entries: list[ReasoningEntry] = []
            for msg in self._load_messages(conn, handle):
                if msg["type"] != "assistant":
                    continue
                for block in msg["blocks"]:
                    if block.get("type") != "reasoning":
                        continue
                    text = block.get("text", "")
                    if text:
                        entries.append(ReasoningEntry(
                            text=text,
                            timestamp=_block_timestamp(block, msg["time_created"]),
                        ))
            return entries
        finally:
            conn.close()

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id, title, agent FROM session_v2 WHERE parent_id = ? "
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
            events: list[TimelineEvent] = []
            for msg in self._load_messages(conn, handle):
                msg_type = msg["type"]
                ts = ms_to_iso(msg["time_created"])
                if msg_type == "user":
                    text = msg["data"].get("text", "")
                    if text:
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.USER_MESSAGE, text=text,
                        ))
                elif msg_type == "assistant":
                    for block in msg["blocks"]:
                        btype = block.get("type")
                        if btype == "text":
                            events.append(TimelineEvent(
                                timestamp=_block_timestamp(block, msg["time_created"]),
                                kind=EventKind.ASSISTANT_TEXT,
                                text=block.get("text", ""),
                            ))
                        elif btype == "reasoning":
                            events.append(TimelineEvent(
                                timestamp=_block_timestamp(block, msg["time_created"]),
                                kind=EventKind.REASONING,
                                text=block.get("text", ""),
                            ))
                        elif btype == "tool":
                            state = block.get("state") or {}
                            inp = state.get("input") if isinstance(state, dict) else None
                            events.append(TimelineEvent(
                                timestamp=_block_timestamp(block, msg["time_created"]),
                                kind=EventKind.TOOL_CALL,
                                tool_name=block.get("name", ""),
                                tool_input=json.dumps(inp) if inp else "",
                                tool_output=_tool_output(state),
                                tool_status=state.get("status", "") if isinstance(state, dict) else "",
                                tool_id=block.get("id", ""),
                            ))
                elif msg_type == "shell":
                    data = msg["data"]
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TOOL_CALL,
                        tool_name="shell",
                        tool_input=data.get("command", ""),
                        tool_output=data.get("output", "") or "",
                        tool_status=data.get("status", ""),
                    ))
                elif msg_type == "compaction":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.COMPACTION,
                        metadata={"auto": msg["data"].get("reason") == "auto"},
                    ))
            return events
        finally:
            conn.close()

    def todos(self, session_id: str) -> list:
        return []
