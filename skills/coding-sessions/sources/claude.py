"""Claude Code session source adapter.

Claude Code stores one JSONL file per session under
~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl.
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

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_DIR / "projects"

_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def _encode_cwd(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _all_project_dirs() -> list[Path]:
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.name)


def _resolve_project_dir(dir_path: str | None) -> Path | None:
    if dir_path:
        return PROJECTS_DIR / _encode_cwd(dir_path)
    return None


def _find_session_file(session_id: str, project_dir: Path) -> Path | None:
    if not project_dir.is_dir():
        return None
    f = project_dir / f"{session_id}.jsonl"
    return f if f.is_file() else None


def _find_session_file_anywhere(session_id: str) -> Path | None:
    for d in _all_project_dirs():
        f = _find_session_file(session_id, d)
        if f:
            return f
    return None


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
_CACHE_LIMIT = 8


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


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                c = block.get("content", "")
                if isinstance(c, list):
                    for sub in c:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
                elif isinstance(c, str):
                    parts.append(c)
        return "\n".join(parts)
    return ""


def _extract_tool_calls(content) -> list[dict]:
    calls: list[dict] = []
    if not isinstance(content, list):
        return calls
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            calls.append({
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}),
            })
    return calls


def _is_real_user_prompt(obj: dict) -> bool:
    if obj.get("isMeta"):
        return False
    msg = obj.get("message", {})
    content = msg.get("content")
    if isinstance(content, str):
        if content.startswith("<local-command-"):
            return False
        if content.startswith("<command-name>"):
            return False
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return False
        return True
    return False


class ClaudeSource(SessionSource):
    name = "claude"

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        if all_dirs:
            dirs = _all_project_dirs()
        elif dir_path:
            dirs = [_resolve_project_dir(dir_path)]
        else:
            dirs = [_resolve_project_dir(os.getcwd())]
        dirs = [d for d in dirs if d and d.is_dir()]

        rows: list[SessionRow] = []
        for d in dirs:
            for f in d.iterdir():
                if f.suffix != ".jsonl":
                    continue
                try:
                    lines = _read_jsonl(f)
                except Exception:
                    continue
                if not lines:
                    continue
                meta = self._meta_from_lines(lines, f)
                rows.append(SessionRow(
                    source="claude",
                    session_id=meta.session_id or f.stem,
                    project=meta.cwd or d.name,
                    title=meta.title,
                    lines=meta.line_count,
                    user_prompts=len(self._prompts_from_lines(lines)),
                    tool_calls=len(self._tool_calls_from_lines(lines)),
                    assistant_texts=len(self._assistant_texts_from_lines(lines)),
                    compacts=meta.compact_count,
                    last_ts=meta.last_ts,
                    file_size=f.stat().st_size,
                ))
        return rows

    def find(self, session_id: str, dir_path: str | None) -> Path | None:
        if dir_path:
            project_dir = _resolve_project_dir(dir_path)
            if not project_dir or not project_dir.is_dir():
                return None
            return _find_session_file(session_id, project_dir)
        return _find_session_file_anywhere(session_id)

    def _meta_from_lines(self, lines: list[dict], f: Path) -> SessionMeta:
        session_id = ""
        cwd = ""
        model = ""
        version = ""
        git_branch = ""
        title = ""
        first_ts = ""
        last_ts = ""
        compact_count = 0
        for obj in lines:
            sid = obj.get("sessionId") or obj.get("session_id", "")
            if sid and not session_id:
                session_id = sid
            c = obj.get("cwd", "")
            if c and not cwd:
                cwd = c
            v = obj.get("version", "")
            if v and not version:
                version = v
            gb = obj.get("gitBranch", "")
            if gb and not git_branch:
                git_branch = gb
            ts = obj.get("timestamp", "")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
            if obj.get("type") == "ai-title":
                title = obj.get("aiTitle", "")
            if obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
                compact_count += 1
            msg = obj.get("message", {})
            if isinstance(msg, dict):
                m = msg.get("model", "")
                if m and not model:
                    model = m
        return SessionMeta(
            source="claude",
            session_id=session_id or f.stem,
            title=title,
            cwd=cwd,
            model=model,
            version=version,
            git_branch=git_branch,
            line_count=len(lines),
            first_ts=first_ts,
            last_ts=last_ts,
            compact_count=compact_count,
            file_size=f.stat().st_size if f.is_file() else 0,
        )

    def _prompts_from_lines(self, lines: list[dict]) -> list[Prompt]:
        prompts: list[Prompt] = []
        for obj in lines:
            if obj.get("type") != "user":
                continue
            if not _is_real_user_prompt(obj):
                continue
            msg = obj.get("message", {})
            content = msg.get("content", "")
            text = _extract_text_from_content(content)
            prompts.append(Prompt(
                text=text,
                timestamp=obj.get("timestamp", ""),
            ))
        return prompts

    def _assistant_texts_from_lines(self, lines: list[dict]) -> list[AssistantText]:
        texts: list[AssistantText] = []
        for obj in lines:
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            content = msg.get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(AssistantText(
                        text=block.get("text", ""),
                        timestamp=obj.get("timestamp", ""),
                    ))
        return texts

    def _tool_calls_from_lines(self, lines: list[dict]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for obj in lines:
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            content = msg.get("content", [])
            for tc in _extract_tool_calls(content):
                calls.append(ToolCall(
                    name=tc["name"],
                    input=json.dumps(tc["input"]) if tc["input"] else "",
                    timestamp=obj.get("timestamp", ""),
                    tool_id=tc["id"],
                ))
        return calls

    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        lines = _read_jsonl_cached(handle)
        return self._meta_from_lines(lines, handle)

    def user_prompts(self, handle: Any) -> list[Prompt]:
        return self._prompts_from_lines(_read_jsonl_cached(handle))

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        return self._assistant_texts_from_lines(_read_jsonl_cached(handle))

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        return self._tool_calls_from_lines(_read_jsonl_cached(handle))

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        lines = _read_jsonl_cached(handle)
        boundaries: list[CompactBoundary] = []
        for obj in lines:
            if obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
                cm = obj.get("compactMetadata", {})
                boundaries.append(CompactBoundary(
                    timestamp=obj.get("timestamp", ""),
                    trigger=cm.get("trigger", ""),
                    pre_tokens=cm.get("preTokens", 0),
                    post_tokens=cm.get("postTokens", 0),
                    dropped_tokens=cm.get("cumulativeDroppedTokens", 0),
                    duration_ms=cm.get("durationMs", 0),
                ))
        return boundaries

    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        lines = _read_jsonl_cached(handle)
        summaries: list[AwaySummary] = []
        for obj in lines:
            if obj.get("type") == "system" and obj.get("subtype") == "away_summary":
                summaries.append(AwaySummary(
                    timestamp=obj.get("timestamp", ""),
                    content=obj.get("content", ""),
                ))
        return summaries

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        return []

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        project_dir = handle.parent
        session_id = handle.stem
        subagents_dir = project_dir / session_id / "subagents"
        if not subagents_dir.is_dir():
            return []
        results: list[SubagentInfo] = []
        for f in sorted(subagents_dir.iterdir()):
            if f.suffix == ".json" and f.name.startswith("agent-"):
                try:
                    meta = json.loads(f.read_text())
                    results.append(SubagentInfo(
                        agent_type=meta.get("agentType", ""),
                        description=meta.get("description", ""),
                    ))
                except (json.JSONDecodeError, OSError):
                    pass
        return results

    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        lines = _read_jsonl_cached(handle)
        events: list[TimelineEvent] = []
        for obj in lines:
            obj_type = obj.get("type", "")
            ts = obj.get("timestamp", "")

            if obj_type == "user":
                if obj.get("isMeta"):
                    continue
                msg = obj.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    has_tool_result = False
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            has_tool_result = True
                            tool_id = block.get("tool_use_id", "")
                            result_text = _extract_text_from_content(block.get("content", ""))
                            events.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.TOOL_RESULT,
                                text=result_text, tool_id=tool_id,
                            ))
                    if not has_tool_result:
                        text = _extract_text_from_content(content)
                        if text:
                            events.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.USER_MESSAGE,
                                text=text,
                            ))
                elif isinstance(content, str):
                    if content.startswith("<local-command-") or content.startswith("<command-name>"):
                        cmd_name = ""
                        if "<command-name>" in content:
                            cmd_name = content.split("<command-name>")[1].split("</command-name>")[0].strip()
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.SLASH_COMMAND, text=cmd_name,
                        ))
                    else:
                        events.append(TimelineEvent(
                            timestamp=ts, kind=EventKind.USER_MESSAGE, text=content,
                        ))

            elif obj_type == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "text":
                            events.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.ASSISTANT_TEXT,
                                text=block.get("text", ""),
                            ))
                        elif btype == "tool_use":
                            inp = block.get("input", {})
                            inp_str = json.dumps(inp) if inp else ""
                            events.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.TOOL_CALL,
                                tool_name=block.get("name", ""),
                                tool_input=inp_str,
                                tool_id=block.get("id", ""),
                            ))
                        elif btype == "thinking":
                            events.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.THINKING,
                                text=block.get("thinking", ""),
                            ))

            elif obj_type == "system":
                subtype = obj.get("subtype", "")
                if subtype == "compact_boundary":
                    cm = obj.get("compactMetadata", {})
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.COMPACT_BOUNDARY,
                        metadata={
                            "trigger": cm.get("trigger", ""),
                            "pre_tokens": cm.get("preTokens", 0),
                            "post_tokens": cm.get("postTokens", 0),
                        },
                    ))
                elif subtype == "away_summary":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.AWAY_SUMMARY,
                        text=obj.get("content", ""),
                    ))
                elif subtype == "turn_duration":
                    events.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TURN_DURATION,
                        metadata={"duration_ms": obj.get("durationMs", 0)},
                    ))

            elif obj_type == "ai-title":
                events.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.AI_TITLE,
                    text=obj.get("aiTitle", ""),
                ))
        return events

    def files_edited(self, session_id: str) -> list[Path]:
        path = self.find(session_id, None)
        if path is None:
            return []
        edited: list[str] = []
        for obj in _read_jsonl_cached(path):
            if obj.get("type") != "assistant":
                continue
            message = obj.get("message", {})
            content = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in _EDIT_TOOLS:
                    continue
                tool_input = block.get("input", {}) or {}
                file_path = (tool_input.get("file_path")
                             or tool_input.get("filePath")
                             or tool_input.get("notebook_path")
                             or tool_input.get("path"))
                if isinstance(file_path, str) and file_path and file_path not in edited:
                    edited.append(file_path)
        return [Path(item) for item in edited]
