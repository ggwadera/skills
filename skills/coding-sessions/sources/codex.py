"""Codex session source adapter.

Codex stores one JSONL file per session under
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl (active)
and ~/.codex/archived_sessions/rollout-<timestamp>-<session-id>.jsonl (archived).

Real user prompts are recovered from event_msg/item_completed UserMessage
items, which both legacy and paginated rollouts persist. A response_item
message with user provenance metadata is the next fallback; event_msg
user_message is the legacy-only last resort. Injected context fragments
(environment, AGENTS.md instructions, skill prompts, notifications) are
excluded by marker.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
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

CODEX_DIR = Path(os.environ.get("CODEX_DIR", Path.home() / ".codex"))
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"
CODEX_ARCHIVED_DIR = CODEX_DIR / "archived_sessions"
CODEX_INDEX_FILE = CODEX_DIR / "session_index.jsonl"

_INJECTED_MARKERS = (
    "# AGENTS.md instructions",
    "## Referenced chats with Codex",
    "<environment_context>",
    "<recommended_plugins>",
    "<subagent_notification>",
    "<skill>",
    "<skills_instructions>",
    "<permissions instructions>",
    "<app-context>",
    "<user_shell_command>",
    "<turn_aborted>",
    "<goal_context>",
    "<external_",
    "<codex_internal_context",
    "The following is the Codex agent history",
)

_USER_KINDS = ("user.text", "user.image", "user.audio")

_TOOL_CALL_TYPES = (
    "custom_tool_call",
    "function_call",
    "local_shell_call",
    "web_search_call",
    "tool_search_call",
)

_TOOL_OUTPUT_TYPES = (
    "custom_tool_call_output",
    "function_call_output",
    "tool_search_output",
)

_AGENT_TAG = re.compile(r'<agent\b[^>]*\bname="([^"]+)"')
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _thread_id_from_name(path: Path) -> str:
    match = _UUID_RE.search(path.name)
    return match.group(1) if match else ""


def _session_cwd(path: Path) -> str:
    for obj in _read_jsonl(path):
        if obj.get("type") == "session_meta":
            return (obj.get("payload") or {}).get("cwd", "")
    return ""


def _read_jsonl(path: Path) -> list[dict]:
    lines: list[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                lines.append(obj)
    return lines


_LINE_CACHE: dict[str, tuple[int, int, list[dict]]] = {}
_CACHE_LIMIT = 4


def _read_jsonl_cached(path: Path) -> list[dict]:
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return []
    cached = _LINE_CACHE.get(key)
    if cached and cached[0] == stat.st_size and cached[1] == int(stat.st_mtime):
        return cached[2]
    lines = _read_jsonl(path)
    _LINE_CACHE[key] = (stat.st_size, int(stat.st_mtime), lines)
    while len(_LINE_CACHE) > _CACHE_LIMIT:
        _LINE_CACHE.pop(next(iter(_LINE_CACHE)))
    return lines


def _read_session_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not CODEX_INDEX_FILE.is_file():
        return index
    with open(CODEX_INDEX_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                sid = entry.get("id", "")
                if sid:
                    index[sid] = entry
            except json.JSONDecodeError:
                pass
    return index


def _all_session_files() -> list[Path]:
    files: list[Path] = []
    if CODEX_SESSIONS_DIR.is_dir():
        files.extend(f for f in CODEX_SESSIONS_DIR.rglob("*.jsonl") if f.is_file())
    if CODEX_ARCHIVED_DIR.is_dir():
        files.extend(f for f in CODEX_ARCHIVED_DIR.glob("*.jsonl") if f.is_file())
    return files


def _find_session_file(session_id: str) -> Path | None:
    candidates: list[Path] = []
    targets = (
        (CODEX_SESSIONS_DIR, True),
        (CODEX_ARCHIVED_DIR, False),
    )
    for directory, recursive in targets:
        if not directory.is_dir():
            continue
        for pattern in (f"rollout-*-{session_id}.jsonl", f"*{session_id}*.jsonl"):
            matches = directory.rglob(pattern) if recursive else directory.glob(pattern)
            for candidate in matches:
                if candidate.is_file() and candidate not in candidates:
                    candidates.append(candidate)
    if not candidates:
        return None

    def _mtime_key(path: Path) -> tuple[float, str]:
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            return (0.0, path.name)

    return max(candidates, key=_mtime_key)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("input_text", "output_text", "text"):
                parts.append(block.get("text", ""))
    return "\n".join(parts)


def _is_injected(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(marker) for marker in _INJECTED_MARKERS)


def _raw_prompt_text(source: str, obj_type: str, payload: dict) -> str | None:
    if source == "item_completed":
        if obj_type != "event_msg" or payload.get("type") != "item_completed":
            return None
        item = payload.get("item", {})
        if item.get("type") != "UserMessage":
            return None
        return _content_text(item.get("content"))
    if source == "provenance":
        if obj_type != "response_item":
            return None
        if payload.get("type") != "message" or payload.get("role") != "user":
            return None
        passthrough = payload.get("internal_chat_message_metadata_passthrough") or {}
        kinds = passthrough.get("content_item_kinds") or []
        if not any(kind in _USER_KINDS for kind in kinds):
            return None
        return _content_text(payload.get("content"))
    if source == "user_message":
        if obj_type != "event_msg" or payload.get("type") != "user_message":
            return None
        return payload.get("message", "")
    return None


def _is_approval_transcript(lines: list[dict]) -> bool:
    source = _prompt_source(lines)
    for obj in lines:
        text = _raw_prompt_text(source, obj.get("type", ""), obj.get("payload", {}))
        if text is None:
            continue
        return text.lstrip().startswith("The following is the Codex agent history")
    return False


def _clean_prompt(text: str) -> str | None:
    if not text:
        return None
    if text.startswith("## My request for Codex:"):
        text = text.split("## My request for Codex:", 1)[1].strip()
    if not text or _is_injected(text):
        return None
    return text


def _prompt_source(lines: list[dict]) -> str:
    for obj in lines:
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload", {})
        if payload.get("type") == "item_completed":
            item = payload.get("item", {})
            if item.get("type") == "UserMessage":
                return "item_completed"
    for obj in lines:
        if obj.get("type") != "response_item":
            continue
        payload = obj.get("payload", {})
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        passthrough = payload.get("internal_chat_message_metadata_passthrough") or {}
        kinds = passthrough.get("content_item_kinds") or []
        if any(kind in _USER_KINDS for kind in kinds):
            return "provenance"
    for obj in lines:
        if obj.get("type") != "event_msg":
            continue
        if (obj.get("payload") or {}).get("type") == "user_message":
            return "user_message"
    return "none"


def _user_prompt_text(source: str, obj_type: str, payload: dict) -> str | None:
    if source == "item_completed":
        if obj_type != "event_msg" or payload.get("type") != "item_completed":
            return None
        item = payload.get("item", {})
        if item.get("type") != "UserMessage":
            return None
        return _clean_prompt(_content_text(item.get("content")))
    if source == "provenance":
        if obj_type != "response_item":
            return None
        if payload.get("type") != "message" or payload.get("role") != "user":
            return None
        passthrough = payload.get("internal_chat_message_metadata_passthrough") or {}
        kinds = passthrough.get("content_item_kinds") or []
        if not any(kind in _USER_KINDS for kind in kinds):
            return None
        return _clean_prompt(_content_text(payload.get("content")))
    if source == "user_message":
        if obj_type != "event_msg" or payload.get("type") != "user_message":
            return None
        return _clean_prompt(payload.get("message", ""))
    return None


def _tool_call_payload(payload: dict) -> dict | None:
    ptype = payload.get("type")
    if ptype == "custom_tool_call":
        raw_input = payload.get("input", "")
        return {
            "name": payload.get("name", ""),
            "input": raw_input if isinstance(raw_input, str) else json.dumps(raw_input),
            "status": payload.get("status", ""),
            "call_id": payload.get("call_id", "") or payload.get("id", ""),
        }
    if ptype == "function_call":
        raw_args = payload.get("arguments", "")
        name = payload.get("name", "")
        namespace = payload.get("namespace", "")
        if namespace:
            name = f"{namespace}.{name}"
        return {
            "name": name,
            "input": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
            "status": payload.get("status", ""),
            "call_id": payload.get("call_id", "") or payload.get("id", ""),
        }
    if ptype == "local_shell_call":
        action = payload.get("action", {})
        return {
            "name": "local_shell",
            "input": json.dumps(action) if action else "",
            "status": payload.get("status", ""),
            "call_id": payload.get("call_id", "") or payload.get("id", ""),
        }
    if ptype == "tool_search_call":
        arguments = payload.get("arguments", "")
        return {
            "name": "tool_search",
            "input": arguments if isinstance(arguments, str) else json.dumps(arguments),
            "status": payload.get("status", ""),
            "call_id": payload.get("call_id", "") or payload.get("id", ""),
        }
    if ptype == "web_search_call":
        action = payload.get("action", payload.get("query", ""))
        return {
            "name": "web_search",
            "input": json.dumps(action) if isinstance(action, (dict, list)) else str(action),
            "status": payload.get("status", ""),
            "call_id": payload.get("call_id", "") or payload.get("id", ""),
        }
    return None


def _usage_from_lines(lines: list[dict]) -> tuple[int, int, int]:
    """Return (input_tokens, output_tokens, active_ms) for a rollout.

    Tokens prefer the newest ``thread_token_usage`` record (exact provider
    accounting); ``event_msg/token_count`` totals are the fallback because a
    post-compaction count can be a context-window estimate. Active time is the
    sum of completed and aborted turn durations, which excludes idle gaps
    between user turns.
    """
    tokens_input = 0
    tokens_output = 0
    active_ms = 0
    total_usage: dict | None = None
    for obj in lines:
        obj_type = obj.get("type", "")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if obj_type == "token_usage_record":
            thread = payload.get("thread_token_usage")
            if isinstance(thread, dict):
                total_usage = thread
        elif obj_type == "event_msg":
            ev_type = payload.get("type", "")
            if ev_type == "token_count":
                info = payload.get("info")
                total = info.get("total_token_usage") if isinstance(info, dict) else None
                if isinstance(total, dict):
                    total_usage = total
            elif ev_type in ("task_complete", "turn_aborted"):
                duration = payload.get("duration_ms") or 0
                if isinstance(duration, (int, float)) and duration > 0:
                    active_ms += int(duration)
    if total_usage:
        tokens_input = total_usage.get("input_tokens") or 0
        tokens_output = total_usage.get("output_tokens") or 0
    return tokens_input, tokens_output, active_ms


class CodexSource(SessionSource):
    name = "codex"

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        index = _read_session_index()
        groups: dict[str, list[tuple[SessionMeta, SessionRow]]] = {}
        for f in _all_session_files():
            try:
                lines = _read_jsonl(f)
            except Exception:
                continue
            if not lines:
                continue
            meta = self._meta_from_lines(lines, f, index)
            if not all_dirs and dir_path and (meta.cwd or "") != dir_path:
                continue
            thread_id = _thread_id_from_name(f) or meta.session_id or f.stem
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            row = SessionRow(
                source="codex",
                session_id=thread_id,
                project=meta.cwd or "",
                title=meta.title,
                lines=meta.line_count,
                user_prompts=len(self._prompts_from_lines(lines)),
                tool_calls=len(self._tool_calls_from_lines(lines)),
                assistant_texts=len(self._assistant_texts_from_lines(lines)),
                compacts=len(self._compacted_from_lines(lines)),
                last_ts=meta.last_ts,
                file_size=size,
                tokens_input=meta.tokens_input,
                tokens_output=meta.tokens_output,
                duration_s=meta.duration_s,
            )
            if meta.is_subagent:
                key = f"{meta.session_id}|{meta.nickname or thread_id}"
            else:
                key = meta.session_id or thread_id
            groups.setdefault(key, []).append((meta, row))
        rows: list[SessionRow] = []
        for group in groups.values():
            representative = max(group, key=lambda item: (item[1].lines, item[1].last_ts))
            rows.append(representative[1])
            rep_title = representative[0].title
            for meta, row in group:
                if row is representative[1]:
                    continue
                if meta.title and meta.title != rep_title:
                    rows.append(row)
        return rows

    def find(self, session_id: str, dir_path: str | None) -> Path | None:
        path = _find_session_file(session_id)
        if path is None:
            return None
        if dir_path and _session_cwd(path) != dir_path:
            return None
        return path

    def _meta_from_lines(self, lines: list[dict], f: Path,
                         index: dict[str, dict] | None = None) -> SessionMeta:
        session_id = ""
        cwd = ""
        model = ""
        provider = ""
        version = ""
        source = ""
        git_branch = ""
        first_ts = ""
        last_ts = ""
        is_subagent = False
        nickname = ""
        for obj in lines:
            obj_type = obj.get("type", "")
            ts = obj.get("timestamp", "")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
            payload = obj.get("payload", {})
            if obj_type == "session_meta":
                if not session_id:
                    session_id = payload.get("session_id", "") or payload.get("id", "")
                if not cwd:
                    cwd = payload.get("cwd", "")
                if not version:
                    version = payload.get("cli_version", "")
                raw_source = payload.get("source", "")
                if isinstance(raw_source, dict) and "subagent" in raw_source:
                    is_subagent = True
                if not source:
                    source = raw_source if isinstance(raw_source, str) else json.dumps(raw_source)
                if not nickname:
                    nickname = payload.get("agent_nickname", "") or payload.get("agent_role", "")
                if not provider:
                    provider = payload.get("model_provider", "")
                git = payload.get("git") or {}
                if not git_branch and isinstance(git, dict):
                    git_branch = git.get("branch", "")
            elif obj_type == "turn_context":
                if payload.get("model"):
                    model = payload.get("model", "")
        title = ""
        thread_id = _thread_id_from_name(f)
        keys = (thread_id,) if is_subagent else (thread_id, session_id)
        if index is not None:
            for key in keys:
                if key and key in index:
                    candidate = index[key].get("thread_name", "")
                    if candidate:
                        title = candidate
                        break
        if not title and is_subagent:
            title = f"subagent {nickname}".strip()
        tokens_input, tokens_output, active_ms = _usage_from_lines(lines)
        return SessionMeta(
            source="codex",
            session_id=session_id or f.stem,
            title=title,
            cwd=cwd,
            model=model or provider,
            version=version,
            git_branch=git_branch,
            line_count=len(lines),
            first_ts=first_ts,
            last_ts=last_ts,
            compact_count=len(self._compacted_from_lines(lines)),
            file_size=f.stat().st_size if f.is_file() else 0,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_s=active_ms // 1000,
            source_label=source,
            is_subagent=is_subagent,
            nickname=nickname,
        )

    def _prompts_from_lines(self, lines: list[dict]) -> list[Prompt]:
        if _is_approval_transcript(lines):
            return []
        source = _prompt_source(lines)
        prompts: list[Prompt] = []
        for obj in lines:
            text = _user_prompt_text(source, obj.get("type", ""), obj.get("payload", {}))
            if text:
                prompts.append(Prompt(text=text, timestamp=obj.get("timestamp", "")))
        return prompts

    def _assistant_texts_from_lines(self, lines: list[dict]) -> list[AssistantText]:
        texts: list[AssistantText] = []
        for obj in lines:
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") != "message":
                continue
            if payload.get("role", "") != "assistant":
                continue
            content = payload.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    texts.append(AssistantText(
                        text=block.get("text", ""),
                        timestamp=obj.get("timestamp", ""),
                    ))
        return texts

    def _reasoning_from_lines(self, lines: list[dict]) -> list[ReasoningEntry]:
        reasoning: list[ReasoningEntry] = []
        for obj in lines:
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") != "reasoning":
                continue
            summaries = payload.get("summary", [])
            if not isinstance(summaries, list):
                continue
            parts: list[str] = []
            for s in summaries:
                if isinstance(s, dict) and s.get("type") == "summary_text":
                    parts.append(s.get("text", ""))
            if parts:
                reasoning.append(ReasoningEntry(
                    text=" | ".join(parts),
                    timestamp=obj.get("timestamp", ""),
                ))
        return reasoning

    def _tool_calls_from_lines(self, lines: list[dict]) -> list[ToolCall]:
        outputs: dict[str, str] = {}
        for obj in lines:
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") not in _TOOL_OUTPUT_TYPES:
                continue
            call_id = payload.get("call_id", "")
            if not call_id or "output" not in payload:
                continue
            raw = payload.get("output", "")
            outputs[call_id] = raw if isinstance(raw, str) else _content_text(raw)
        calls: list[ToolCall] = []
        for obj in lines:
            if obj.get("type") != "response_item":
                continue
            parsed = _tool_call_payload(obj.get("payload", {}))
            if parsed is None:
                continue
            calls.append(ToolCall(
                name=parsed["name"],
                input=parsed["input"],
                timestamp=obj.get("timestamp", ""),
                status=parsed["status"],
                tool_id=parsed["call_id"],
                output=outputs.get(parsed["call_id"], ""),
            ))
        return calls

    def _compacted_from_lines(self, lines: list[dict]) -> list[dict]:
        items: list[dict] = []
        for obj in lines:
            if obj.get("type") == "compacted":
                items.append({
                    "timestamp": obj.get("timestamp", ""),
                    "message": (obj.get("payload") or {}).get("message", ""),
                })
        return items

    def _subagents_from_lines(self, lines: list[dict]) -> list[SubagentInfo]:
        subagents: list[SubagentInfo] = []
        seen_ids: set[str] = set()
        for obj in lines:
            if obj.get("type") != "world_state":
                continue
            payload = obj.get("payload", {})
            state = payload.get("state", {})
            envs = state.get("environments", {})
            sa_str = envs.get("subagents", "") if isinstance(envs, dict) else ""
            if not sa_str:
                continue
            for raw_line in sa_str.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                tag = _AGENT_TAG.search(line)
                if tag:
                    name = tag.group(1)
                    if name and name not in seen_ids:
                        seen_ids.add(name)
                        subagents.append(SubagentInfo(agent_id=name, nickname=name))
                    continue
                line = line.lstrip("- ").strip()
                if ":" not in line:
                    continue
                agent_id, nickname = line.split(":", 1)
                agent_id = agent_id.strip()
                nickname = nickname.strip()
                if agent_id and agent_id not in seen_ids:
                    seen_ids.add(agent_id)
                    subagents.append(SubagentInfo(
                        agent_id=agent_id,
                        nickname=nickname,
                    ))
        return subagents

    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        lines = _read_jsonl_cached(handle)
        return self._meta_from_lines(lines, handle, _read_session_index())

    def user_prompts(self, handle: Any) -> list[Prompt]:
        return self._prompts_from_lines(_read_jsonl_cached(handle))

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        return self._assistant_texts_from_lines(_read_jsonl_cached(handle))

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        return self._tool_calls_from_lines(_read_jsonl_cached(handle))

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        return [
            CompactBoundary(
                timestamp=item["timestamp"],
                trigger="compacted",
                pre_tokens=0,
                post_tokens=0,
                dropped_tokens=0,
                duration_ms=0,
            )
            for item in self._compacted_from_lines(_read_jsonl_cached(handle))
        ]

    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        return [
            AwaySummary(timestamp=item["timestamp"], content=item["message"])
            for item in self._compacted_from_lines(_read_jsonl_cached(handle))
            if item["message"]
        ]

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        return self._reasoning_from_lines(_read_jsonl_cached(handle))

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        return self._subagents_from_lines(_read_jsonl_cached(handle))

    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        lines = _read_jsonl_cached(handle)
        prompt_source = "none" if _is_approval_transcript(lines) else _prompt_source(lines)
        has_compacted = any(obj.get("type") == "compacted" for obj in lines)
        events: list[TimelineEvent] = []
        for obj in lines:
            obj_type = obj.get("type", "")
            ts = obj.get("timestamp", "")
            payload = obj.get("payload", {})

            text = _user_prompt_text(prompt_source, obj_type, payload)
            if text:
                events.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.USER_MESSAGE, text=text,
                ))
                continue

            if obj_type == "session_meta":
                events.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.SESSION_META,
                    metadata={
                        "session_id": payload.get("session_id", "") or payload.get("id", ""),
                        "cwd": payload.get("cwd", ""),
                        "version": payload.get("cli_version", ""),
                    },
                ))
            elif obj_type == "turn_context":
                events.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.TURN_CONTEXT,
                    text=payload.get("cwd", ""),
                ))
            elif obj_type == "compacted":
                message = payload.get("message", "")
                events.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.COMPACT_BOUNDARY,
                    metadata={"summary_chars": len(message)},
                ))
            elif obj_type == "world_state":
                state = payload.get("state", {})
                envs = state.get("environments", {})
                sa = envs.get("subagents") if isinstance(envs, dict) else None
                if sa:
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.WORLD_STATE,
                        text=sa[:200],
                    ))
            elif obj_type == "event_msg":
                ev_type = payload.get("type", "")
                if ev_type == "task_started":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TASK_STARTED,
                        metadata={
                            "turn_id": payload.get("turn_id", ""),
                            "model_ctx": payload.get("model_context_window", 0),
                        },
                    ))
                elif ev_type == "agent_message":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.ASSISTANT_TEXT,
                        text=payload.get("message", ""),
                    ))
                elif ev_type == "agent_reasoning":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.REASONING,
                        text=payload.get("text", ""),
                    ))
                elif ev_type == "token_count":
                    info = payload.get("info", {})
                    total = info.get("total_token_usage", {}) if isinstance(info, dict) else {}
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TOKEN_COUNT,
                        metadata={
                            "total_tokens": total.get("total_tokens", 0),
                            "input_tokens": total.get("input_tokens", 0),
                            "output_tokens": total.get("output_tokens", 0),
                        },
                    ))
            elif obj_type == "response_item":
                ri_type = payload.get("type", "")
                if ri_type == "message":
                    role = payload.get("role", "")
                    content = payload.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "output_text":
                                events.append(TimelineEvent(
                                    timestamp=ts, kind=EventKind.ASSISTANT_TEXT,
                                    text=block.get("text", ""),
                                    metadata={"role": role},
                                ))
                elif ri_type == "reasoning":
                    summaries = payload.get("summary", [])
                    parts: list[str] = []
                    for s in summaries if isinstance(summaries, list) else []:
                        if isinstance(s, dict) and s.get("type") == "summary_text":
                            parts.append(s.get("text", ""))
                    if parts:
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.REASONING,
                            text=" | ".join(parts),
                        ))
                elif ri_type in _TOOL_CALL_TYPES:
                    parsed = _tool_call_payload(payload)
                    if parsed:
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.TOOL_CALL,
                            tool_name=parsed["name"],
                            tool_input=parsed["input"],
                            tool_status=parsed["status"],
                            tool_id=parsed["call_id"],
                        ))
                elif ri_type == "compaction":
                    if not has_compacted:
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.COMPACTION,
                            metadata={"auto": False, "encrypted": True},
                        ))
                elif ri_type in _TOOL_OUTPUT_TYPES:
                    raw = payload.get("output", "")
                    text = raw if isinstance(raw, str) else _content_text(raw)
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TOOL_RESULT,
                        text=text,
                        tool_id=payload.get("call_id", ""),
                    ))
        return events
