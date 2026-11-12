"""DeepSeek harness (dsh) session source adapter.

The harness stores one JSONL transcript per session under
~/.dsh/sessions/<projectKey>/<sessionId>/session.vN.jsonl[.zstd]; the home
directory is overridable via DSH_HOME. Line 1 is the session header
({type: "session", version, id, createdAt, cwd, ...}); every following line
is a typed event envelope ({type, seq, time, data} with an optional
surfaceOp). The current format generation is v4; each session directory
yields its newest generation, and other generations are parsed best-effort
with the same envelope rules.

Real user prompts are user/message events whose source.kind is "user"
(mirroring the harness's own Markdown export); injected context
(hook/parent/disposed/runtime-context sources and system/developer
messages) is excluded.

Supersession: surface events can be replaced by a later event carrying
surfaceOp {op: "replace", startSeq, endSeq}. Replacements only supersede
same-kind predecessors: an edited user message supersedes the original
prompt, a refreshed tool/result supersedes the old result, and a
regenerated assistant message supersedes the old one. Compaction
checkpoints (user/message with source.kind "compact-checkpoint") replace
the compacted range on the live surface but do not erase transcript
history, so pre-compaction prompts and assistant messages are preserved.

zstd-compressed files (the default encoding) decode through stdlib
compression.zstd (Python 3.14+), the zstandard package, or the zstd CLI,
in that order.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
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
    TodoEntry,
    ToolCall,
)

#: Matches generation filenames: session.jsonl, session.v3.jsonl,
#: session.v4.jsonl.zstd (.lock and *.tmp files never match).
_SESSION_FILE_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl(\.zstd)?$")

#: Parsed (header, events) cache keyed by path, validated by size and mtime.
_PARSE_CACHE: dict[str, tuple[int, int, tuple[dict, list[dict]]]] = {}
_CACHE_LIMIT = 8


class DshDecodeError(OSError):
    """Raised when a zstd session file cannot be decoded by any backend."""


# ---------------------------------------------------------------------------
# Paths and file discovery
# ---------------------------------------------------------------------------

def _dsh_home() -> Path:
    configured = os.environ.get("DSH_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".dsh"


def _sessions_root() -> Path:
    return _dsh_home() / "sessions"


def _pick_generation(directory: Path) -> Path | None:
    """Return the newest session generation file inside a session directory."""
    best: tuple[int, int, str] | None = None
    best_path: Path | None = None
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        match = _SESSION_FILE_RE.match(entry.name)
        if match is None or not entry.is_file():
            continue
        version = int(match.group(1)) if match.group(1) else 0
        compressed = 1 if match.group(2) else 0
        key = (version, compressed, entry.name)
        if best is None or key > best:
            best, best_path = key, entry
    return best_path


def _session_dirs() -> list[tuple[Path, Path]]:
    """Return (session_dir, chosen_file) pairs for every stored session."""
    root = _sessions_root()
    if not root.is_dir():
        return []
    found: list[tuple[Path, Path]] = []
    try:
        projects = sorted(root.iterdir())
    except OSError:
        return []
    for project_dir in projects:
        if not project_dir.is_dir():
            continue
        try:
            children = sorted(project_dir.iterdir())
        except OSError:
            continue
        for session_dir in children:
            if not session_dir.is_dir():
                continue
            path = _pick_generation(session_dir)
            if path is not None:
                found.append((session_dir, path))
    return found


def _session_size(session_dir: Path) -> int:
    total = 0
    try:
        entries = list(session_dir.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if _SESSION_FILE_RE.match(entry.name) and entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# Decoding and parsing
# ---------------------------------------------------------------------------

def _decompress(data: bytes) -> bytes:
    errors: list[str] = []

    try:
        from compression import zstd as _stdlib_zstd
    except ImportError:
        _stdlib_zstd = None
    if _stdlib_zstd is not None:
        try:
            return _stdlib_zstd.decompress(data)
        except Exception as exc:  # ZstdError and input-shape errors
            errors.append(f"compression.zstd: {exc}")

    try:
        import zstandard as _zstandard
    except ImportError:
        _zstandard = None
    if _zstandard is not None:
        try:
            src = io.BytesIO(data)
            with _zstandard.ZstdDecompressor().stream_reader(
                src, read_across_frames=True,
            ) as reader:
                return reader.read()
        except Exception as exc:
            errors.append(f"zstandard: {exc}")

    if shutil.which("zstd"):
        try:
            proc = subprocess.run(
                ["zstd", "-dc"], input=data, capture_output=True, timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"zstd CLI: {exc}")
        else:
            if proc.returncode == 0:
                return proc.stdout
            detail = proc.stderr.decode("utf-8", "replace").strip()
            errors.append(f"zstd CLI: {detail or 'exit ' + str(proc.returncode)}")

    detail = "; ".join(errors) if errors else "no zstd backend found"
    raise DshDecodeError(
        "cannot decode zstd session file — install Python 3.14+ "
        "(compression.zstd), the 'zstandard' package, or the zstd CLI "
        f"({detail})"
    )


def _read_session_file(path: Path) -> str:
    if path.name.endswith(".zstd"):
        return _decompress(path.read_bytes()).decode("utf-8", "replace")
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_file(path: Path) -> tuple[dict, list[dict]]:
    """Parse a session file into (header, events), with an mtime cache."""
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return {}, []
    cached = _PARSE_CACHE.get(key)
    if cached and cached[0] == stat.st_size and cached[1] == int(stat.st_mtime):
        return cached[2]
    text = _read_session_file(path)
    parsed: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn tail or foreign line: best-effort skip
        if isinstance(obj, dict):
            parsed.append(obj)
    header: dict = {}
    events = parsed
    if parsed and parsed[0].get("type") == "session":
        header = parsed[0]
        events = parsed[1:]
    result = (header, events)
    _PARSE_CACHE[key] = (stat.st_size, int(stat.st_mtime), result)
    while len(_PARSE_CACHE) > _CACHE_LIMIT:
        _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))
    return result


# ---------------------------------------------------------------------------
# Supersession (surfaceOp replace)
# ---------------------------------------------------------------------------

def _current_events(events: list[dict]) -> list[dict]:
    """Drop events superseded by a same-kind replacement.

    Compaction checkpoints replace the compacted range with a
    user/message whose source.kind is not "user"; those do not erase the
    historical prompts or assistant messages they span.
    """
    by_seq: dict[int, dict] = {}
    for ev in events:
        seq = ev.get("seq")
        if isinstance(seq, int):
            by_seq[seq] = ev
    superseded: dict[str, set[int]] = {}
    for ev in events:
        op = ev.get("surfaceOp")
        if not (isinstance(op, dict) and op.get("op") == "replace"):
            continue
        rtype = ev.get("type", "")
        if rtype == "user/message":
            source = (ev.get("data") or {}).get("source")
            kind = source.get("kind") if isinstance(source, dict) else None
            if kind not in (None, "user"):
                continue  # compact-checkpoint and friends keep history
        start = op.get("startSeq")
        end = op.get("endSeq")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            continue
        bucket = superseded.setdefault(rtype, set())
        for seq in range(start, end + 1):
            target = by_seq.get(seq)
            if target is not None and target.get("type") == rtype:
                bucket.add(seq)
    if not superseded:
        return events
    return [
        ev for ev in events
        if not (
            isinstance(ev.get("seq"), int)
            and ev["seq"] in superseded.get(ev.get("type", ""), set())
        )
    ]


def _effective_seq(ev: dict) -> int:
    """Position of an event in the surface: an edit sits where it replaced."""
    op = ev.get("surfaceOp")
    if isinstance(op, dict) and op.get("op") == "replace":
        start = op.get("startSeq")
        if isinstance(start, int):
            return start
    seq = ev.get("seq")
    return seq if isinstance(seq, int) else 0


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _ms_to_iso(ms: Any) -> str:
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _is_real_user_message(ev: dict) -> bool:
    if ev.get("type") != "user/message":
        return False
    source = (ev.get("data") or {}).get("source")
    if isinstance(source, dict):
        return source.get("kind") == "user"
    return True  # older formats without provenance


def _prompts(events: list[dict]) -> list[Prompt]:
    prompts: list[tuple[int, Prompt]] = []
    for ev in events:
        if not _is_real_user_message(ev):
            continue
        prompts.append((_effective_seq(ev), Prompt(
            text=_content_text((ev.get("data") or {}).get("content")),
            timestamp=_ms_to_iso(ev.get("time")),
        )))
    prompts.sort(key=lambda item: item[0])
    return [item[1] for item in prompts]


def _assistant_texts(events: list[dict]) -> list[AssistantText]:
    texts: list[tuple[int, AssistantText]] = []
    for ev in events:
        if ev.get("type") != "assistant/message":
            continue
        content = ((ev.get("data") or {}).get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        ts = _ms_to_iso(ev.get("time"))
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                texts.append((_effective_seq(ev), AssistantText(text=text, timestamp=ts)))
    texts.sort(key=lambda item: item[0])
    return [item[1] for item in texts]


def _reasoning(events: list[dict]) -> list[ReasoningEntry]:
    entries: list[tuple[int, ReasoningEntry]] = []
    for ev in events:
        if ev.get("type") != "assistant/message":
            continue
        content = ((ev.get("data") or {}).get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        ts = _ms_to_iso(ev.get("time"))
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "reasoning":
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                entries.append((_effective_seq(ev), ReasoningEntry(text=text, timestamp=ts)))
    entries.sort(key=lambda item: item[0])
    return [item[1] for item in entries]


def _tool_result_status(data: dict) -> tuple[str, str]:
    """Return (output_text, status) for a tool/result payload."""
    message = data.get("message") or {}
    text = _content_text(message.get("content"))
    error = data.get("error")
    if isinstance(error, dict):
        reason = error.get("reason") or error.get("code") or error.get("name")
        return (text or str(reason or "error"), "error")
    if message.get("isError"):
        return (text, "error")
    return (text, "completed")


def _str_field(mapping: dict, key: str) -> str:
    """String value of mapping[key], or "" when absent or not a string."""
    value = mapping.get(key)
    return value if isinstance(value, str) else ""


def _result_call_id(message: dict) -> str:
    """Call ID a tool/result answers: toolCallId, else source.callId."""
    call_id = message.get("toolCallId")
    if not isinstance(call_id, str) or not call_id:
        source = message.get("source")
        call_id = source.get("callId") if isinstance(source, dict) else ""
    return call_id if isinstance(call_id, str) else ""


def _tool_results_by_call(events: list[dict]) -> dict[str, tuple[str, str]]:
    results: dict[str, tuple[str, str]] = {}
    for ev in events:
        if ev.get("type") != "tool/result":
            continue
        data = ev.get("data") or {}
        call_id = _result_call_id(data.get("message") or {})
        if not call_id:
            continue
        results[call_id] = _tool_result_status(data)  # later refresh wins
    return results


def _tool_calls(events: list[dict]) -> list[ToolCall]:
    results = _tool_results_by_call(events)
    calls: list[ToolCall] = []
    for ev in events:
        if ev.get("type") != "tool/call":
            continue
        data = ev.get("data") or {}
        call_id = _str_field(data, "callId")
        raw = data.get("arguments", "")
        output, status = results.get(call_id, ("", ""))
        calls.append(ToolCall(
            name=_str_field(data, "name"),
            input=raw if isinstance(raw, str) else json.dumps(raw),
            timestamp=_ms_to_iso(ev.get("time")),
            status=status,
            tool_id=call_id,
            output=output,
        ))
    return calls


def _title(events: list[dict]) -> str:
    title = ""
    for ev in events:
        if ev.get("type") == "session/title":
            candidate = (ev.get("data") or {}).get("title")
            if isinstance(candidate, str) and candidate:
                title = candidate
    return title


def _model(events: list[dict]) -> str:
    model = ""
    provider = ""
    for ev in events:
        data = ev.get("data") or {}
        if ev.get("type") == "request/context":
            if isinstance(data.get("model"), str) and data["model"]:
                model = data["model"]
            if isinstance(data.get("provider"), str) and data["provider"]:
                provider = data["provider"]
        elif ev.get("type") == "request/header":
            config = (data.get("header") or {}).get("config") or {}
            if isinstance(config.get("model"), str) and config["model"]:
                model = config["model"]
            if isinstance(config.get("provider"), str) and config["provider"]:
                provider = config["provider"]
    return model or provider


def _compaction_pairs(events: list[dict]) -> list[dict]:
    """Pair compaction/start with its summary and end, in start order."""
    starts: list[tuple[str, dict]] = []
    summaries: dict[str, dict] = {}
    ends: dict[str, dict] = {}
    for ev in events:
        ev_type = ev.get("type")
        if ev_type not in ("compaction/start", "compaction/summary", "compaction/end"):
            continue
        compaction_id = (ev.get("data") or {}).get("compactionId")
        if not isinstance(compaction_id, str) or not compaction_id:
            continue
        if ev_type == "compaction/start":
            starts.append((compaction_id, ev))
        elif ev_type == "compaction/summary":
            summaries.setdefault(compaction_id, ev)
        else:
            ends.setdefault(compaction_id, ev)
    pairs = []
    for compaction_id, start in starts:
        pairs.append({
            "id": compaction_id,
            "start": start,
            "summary": summaries.get(compaction_id),
            "end": ends.get(compaction_id),
        })
    return pairs


def _standalone_prunes(events: list[dict],
                       windows: list[tuple[int, int]]) -> list[dict]:
    """Prune events outside every compaction start/end window."""
    prunes = []
    for ev in events:
        if ev.get("type") != "compaction/prune":
            continue
        ts = ev.get("time")
        if not isinstance(ts, int):
            ts = 0
        if any(start <= ts <= end for start, end in windows):
            continue
        prunes.append(ev)
    return prunes


def _compaction_windows(pairs: list[dict]) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for pair in pairs:
        start_time = pair["start"].get("time") or 0
        end_ev = pair["end"] or pair["summary"] or pair["start"]
        end_time = end_ev.get("time") or start_time
        if isinstance(start_time, int) and isinstance(end_time, int):
            windows.append((start_time, end_time))
    return windows


def _prune_dropped(ev: dict) -> int:
    value = (ev.get("data") or {}).get("shadowedTokenCount")
    return value if isinstance(value, int) else 0


def _boundary_from_pair(pair: dict, prunes: list[dict]) -> CompactBoundary:
    start = pair["start"]
    start_data = start.get("data") or {}
    summary = pair["summary"]
    end = pair["end"]
    pre_tokens = 0
    post_tokens = 0
    dropped_tokens = 0
    if summary is not None:
        summary_data = summary.get("data") or {}
        shadowed = summary_data.get("shadowedTokenCount")
        dropped_tokens = shadowed if isinstance(shadowed, int) else 0
        usage = summary_data.get("usage") or {}
        pre_tokens = 0
        for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"):
            value = usage.get(key)
            if isinstance(value, int):
                pre_tokens += value
        if pre_tokens and dropped_tokens:
            post_tokens = max(pre_tokens - dropped_tokens, 0)
    else:
        # A compaction that never summarized still pruned inside its window.
        start_time = start.get("time") or 0
        end_time = (end.get("time") if end is not None else None) or start_time
        if isinstance(start_time, int) and isinstance(end_time, int):
            for ev in prunes:
                ts = ev.get("time")
                if isinstance(ts, int) and start_time <= ts <= end_time:
                    dropped_tokens += _prune_dropped(ev)
    duration_ms = 0
    if end is not None:
        start_time = start.get("time")
        end_time = end.get("time")
        if isinstance(start_time, int) and isinstance(end_time, int):
            duration_ms = max(end_time - start_time, 0)
    trigger = "command" if start_data.get("sourceCommandId") else "auto"
    return CompactBoundary(
        timestamp=_ms_to_iso(start.get("time")),
        trigger=trigger,
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
        dropped_tokens=dropped_tokens,
        duration_ms=duration_ms,
    )


def _compaction_boundaries(events: list[dict]) -> list[CompactBoundary]:
    pairs = _compaction_pairs(events)
    windows = _compaction_windows(pairs)
    prunes = [ev for ev in events if ev.get("type") == "compaction/prune"]
    boundaries = [_boundary_from_pair(pair, prunes) for pair in pairs]
    standalone = _standalone_prunes(events, windows)
    if standalone:
        dropped = sum(_prune_dropped(ev) for ev in standalone)
        boundaries.append(CompactBoundary(
            timestamp=_ms_to_iso(standalone[0].get("time")),
            trigger="prune",
            pre_tokens=0,
            post_tokens=0,
            dropped_tokens=dropped,
            duration_ms=0,
        ))
    boundaries.sort(key=lambda b: b.timestamp)
    return boundaries


def _away_summaries(events: list[dict]) -> list[AwaySummary]:
    summaries: list[AwaySummary] = []
    for ev in events:
        if ev.get("type") != "compaction/summary":
            continue
        text = _content_text((ev.get("data") or {}).get("summary"))
        if text:
            summaries.append(AwaySummary(timestamp=_ms_to_iso(ev.get("time")), content=text))
    return summaries


def _todos(events: list[dict]) -> list[TodoEntry]:
    raw: list = []
    for ev in events:
        if ev.get("type") == "todo/write":
            candidate = (ev.get("data") or {}).get("todos")
            if isinstance(candidate, list):
                raw = candidate  # todo/write replaces the whole list
    entries: list[TodoEntry] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        content = item.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        status = item.get("status", "")
        entries.append(TodoEntry(
            content=content,
            status=status if isinstance(status, str) else str(status),
            priority="",
            position=position,
        ))
    return entries


def _subagents_from_catalog(events: list[dict]) -> list[SubagentInfo]:
    infos: list[SubagentInfo] = []
    seen: set[str] = set()
    for ev in events:
        if ev.get("type") != "subagent/catalog":
            continue
        data = ev.get("data") or {}
        child_id = data.get("childId")
        if not isinstance(child_id, str) or not child_id or child_id in seen:
            continue
        seen.add(child_id)
        name = _str_field(data, "name")
        label = _str_field(data, "label")
        mode = _str_field(data, "mode")
        infos.append(SubagentInfo(
            agent_type=mode,
            description=label,
            agent_id=child_id,
            nickname=name or label,
        ))
    return infos


def _descriptor(events: list[dict]) -> dict:
    for ev in events:
        if ev.get("type") == "subagent/descriptor":
            data = ev.get("data")
            if isinstance(data, dict):
                return data
    return {}


def _child_sessions(parent_id: str) -> list[SubagentInfo]:
    """Header-scan fallback: sessions created as subagent children."""
    infos: list[SubagentInfo] = []
    for session_dir, path in _session_dirs():
        try:
            header, _events = _parse_file(path)
        except OSError:
            continue
        if header.get("parentSession") != parent_id:
            continue
        origin = header.get("origin")
        depth = header.get("delegationDepth")
        if origin != "subagent" and not (isinstance(depth, int) and depth > 0):
            continue  # forks/resumes are not subagents
        child_id = header.get("id")
        if not isinstance(child_id, str) or not child_id:
            child_id = session_dir.name
        infos.append(SubagentInfo(agent_id=child_id, nickname=child_id))
    return infos


def _stats(events: list[dict]) -> tuple[int, int, int]:
    """Return (input_tokens, output_tokens, active_ms).

    Tokens sum assistant/message usage: input plus cache read/write tokens
    are all prompt tokens. Active time is the sum of completed turn spans,
    which excludes idle gaps between user turns; step spans are the
    fallback for logs without turn markers.
    """
    tokens_input = 0
    tokens_output = 0
    active_ms = 0
    open_turns: dict[Any, int] = {}
    open_steps: list[tuple[Any, int]] = []
    steps_total = 0
    for ev in events:
        ev_type = ev.get("type")
        data = ev.get("data") or {}
        ts = ev.get("time")
        if ev_type == "assistant/message":
            usage = data.get("usage")
            if isinstance(usage, dict):
                output = usage.get("outputTokens") or 0
                tokens_output += output if isinstance(output, int) else 0
                for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"):
                    value = usage.get(key)
                    if isinstance(value, int):
                        tokens_input += value
        elif ev_type == "turn/start":
            if isinstance(ts, int):
                open_turns[data.get("turn")] = ts
        elif ev_type == "turn/end":
            start = open_turns.pop(data.get("turn"), None)
            if isinstance(start, int) and isinstance(ts, int) and ts >= start:
                active_ms += ts - start
        elif ev_type == "step/start":
            if isinstance(ts, int):
                open_steps.append((data.get("step"), ts))
        elif ev_type == "step/end" and open_steps and isinstance(ts, int):
            _step, start = open_steps.pop(0)
            steps_total += max(ts - start, 0)
    if active_ms == 0 and steps_total:
        active_ms = steps_total
    return tokens_input, tokens_output, active_ms


def _is_subagent_header(header: dict) -> bool:
    if header.get("origin") == "subagent":
        return True
    depth = header.get("delegationDepth")
    return isinstance(depth, int) and depth > 0


def _header_session_id(header: dict, session_dir: Path) -> str:
    session_id = header.get("id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return session_dir.name


def _header_cwd(header: dict) -> str:
    cwd = header.get("cwd")
    return cwd if isinstance(cwd, str) else ""


def _event_timestamps(events: list[dict], header: dict) -> tuple[str, str]:
    created = header.get("createdAt")
    first = created if isinstance(created, int) else None
    last = first
    for ev in events:
        ts = ev.get("time")
        if not isinstance(ts, int):
            continue
        # Seeded forks inherit events older than this file's createdAt.
        if first is None or ts < first:
            first = ts
        if last is None or ts > last:
            last = ts
    if first is None:
        first = last
    return _ms_to_iso(first), _ms_to_iso(last)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class DshSource(SessionSource):
    name = "dsh"

    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        rows: list[SessionRow] = []
        decode_error: OSError | None = None
        for session_dir, path in _session_dirs():
            try:
                header, events = _parse_file(path)
            except DshDecodeError as exc:
                decode_error = decode_error or exc
                continue
            except OSError:
                continue
            if not header and not events:
                continue
            cwd = _header_cwd(header)
            if not all_dirs and dir_path and cwd != dir_path:
                continue
            rows.append(self._row(header, events, session_dir, path))
        if not rows and decode_error is not None:
            raise decode_error
        rows.sort(key=lambda r: r.last_ts, reverse=True)
        if limit is not None and limit >= 0:
            rows = rows[:limit]
        return rows

    def find(self, session_id: str, dir_path: str | None) -> Path | None:
        # Fast path: session directories are the identity-encoded session ID
        # for the usual session-<uuid> form.
        root = _sessions_root()
        if root.is_dir():
            try:
                for project_dir in root.iterdir():
                    if not project_dir.is_dir():
                        continue
                    candidate = project_dir / session_id
                    if not candidate.is_dir():
                        continue
                    path = _pick_generation(candidate)
                    if path is None:
                        continue
                    try:
                        header, _events = _parse_file(path)
                    except OSError:
                        continue
                    if _header_session_id(header, candidate) == session_id:
                        if dir_path and _header_cwd(header) != dir_path:
                            return None
                        return path
            except OSError:
                pass
        for session_dir, path in _session_dirs():
            try:
                header, _events = _parse_file(path)
            except OSError:
                continue
            if _header_session_id(header, session_dir) != session_id:
                continue
            if dir_path and _header_cwd(header) != dir_path:
                continue
            return path
        return None

    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        header, events = _parse_file(handle)
        return self._meta(header, events, Path(handle))

    def user_prompts(self, handle: Any) -> list[Prompt]:
        _header, events = _parse_file(handle)
        return _prompts(_current_events(events))

    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        _header, events = _parse_file(handle)
        return _assistant_texts(_current_events(events))

    def tool_calls(self, handle: Any) -> list[ToolCall]:
        _header, events = _parse_file(handle)
        return _tool_calls(_current_events(events))

    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        _header, events = _parse_file(handle)
        return _compaction_boundaries(_current_events(events))

    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        _header, events = _parse_file(handle)
        return _away_summaries(_current_events(events))

    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        _header, events = _parse_file(handle)
        return _reasoning(_current_events(events))

    def subagents(self, handle: Any) -> list[SubagentInfo]:
        header, events = _parse_file(handle)
        infos = _subagents_from_catalog(_current_events(events))
        session_id = _header_session_id(header, Path(handle).parent)
        known = {info.agent_id for info in infos}
        for child in _child_sessions(session_id):
            if child.agent_id not in known:
                infos.append(child)
        return infos

    def todos(self, session_id: str) -> list[TodoEntry]:
        path = self.find(session_id, None)
        if path is None:
            return []
        try:
            _header, events = _parse_file(path)
        except OSError:
            return []
        return _todos(_current_events(events))

    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        header, raw = _parse_file(handle)
        events = _current_events(raw)
        results = _tool_results_by_call(events)
        pairs = _compaction_pairs(events)
        windows = _compaction_windows(pairs)
        prunes = _standalone_prunes(events, windows)
        prune_list = [ev for ev in events if ev.get("type") == "compaction/prune"]
        boundary_meta: dict[str, CompactBoundary] = {
            pair["id"]: _boundary_from_pair(pair, prune_list)
            for pair in pairs
        }
        prune_dropped = sum(_prune_dropped(ev) for ev in prunes)
        prune_reported = False

        out: list[TimelineEvent] = [TimelineEvent(
            timestamp=_ms_to_iso(header.get("createdAt")),
            kind=EventKind.SESSION_META,
            metadata={
                "session_id": _header_session_id(header, Path(handle).parent),
                "cwd": _header_cwd(header),
                "version": "",
            },
        )]
        open_turns: dict[Any, int] = {}
        for ev in events:
            ev_type = ev.get("type")
            data = ev.get("data") or {}
            ts = _ms_to_iso(ev.get("time"))

            if ev_type == "user/message":
                if _is_real_user_message(ev):
                    out.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.USER_MESSAGE,
                        text=_content_text(data.get("content")),
                    ))
            elif ev_type == "assistant/message":
                content = (data.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        text = block.get("text")
                        if not isinstance(text, str) or not text:
                            continue
                        if block.get("type") == "text":
                            out.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.ASSISTANT_TEXT, text=text,
                            ))
                        elif block.get("type") == "reasoning":
                            out.append(TimelineEvent(
                                timestamp=ts, kind=EventKind.REASONING, text=text,
                            ))
            elif ev_type == "tool/call":
                call_id = _str_field(data, "callId")
                status = results.get(call_id, ("", ""))[1]
                raw_args = data.get("arguments", "")
                out.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.TOOL_CALL,
                    tool_name=_str_field(data, "name"),
                    tool_input=raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                    tool_status=status,
                    tool_id=call_id,
                ))
            elif ev_type == "tool/result":
                call_id = _result_call_id(data.get("message") or {})
                text, _status = _tool_result_status(data)
                out.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.TOOL_RESULT,
                    text=text, tool_id=call_id,
                ))
            elif ev_type == "compaction/start":
                compaction_id = data.get("compactionId")
                boundary = boundary_meta.get(compaction_id) if isinstance(compaction_id, str) else None
                out.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.COMPACT_BOUNDARY,
                    metadata={
                        "trigger": boundary.trigger if boundary else "auto",
                        "pre_tokens": boundary.pre_tokens if boundary else 0,
                        "post_tokens": boundary.post_tokens if boundary else 0,
                        "dropped_tokens": boundary.dropped_tokens if boundary else 0,
                    },
                ))
            elif ev_type == "compaction/summary":
                text = _content_text(data.get("summary"))
                if text:
                    out.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.AWAY_SUMMARY, text=text,
                    ))
            elif ev_type == "session/title":
                title = data.get("title")
                if isinstance(title, str) and title:
                    out.append(TimelineEvent(timestamp=ts, kind=EventKind.AI_TITLE, text=title))
            elif ev_type == "turn/start":
                if isinstance(ev.get("time"), int):
                    open_turns[data.get("turn")] = ev["time"]
            elif ev_type == "turn/end":
                start = open_turns.pop(data.get("turn"), None)
                end_time = ev.get("time")
                if isinstance(start, int) and isinstance(end_time, int) and end_time >= start:
                    out.append(TimelineEvent(
                        timestamp=ts, kind=EventKind.TURN_DURATION,
                        metadata={"duration_ms": end_time - start},
                    ))
            elif ev_type == "working-directory/change":
                cwd = data.get("cwd")
                if isinstance(cwd, str) and cwd:
                    out.append(TimelineEvent(timestamp=ts, kind=EventKind.TURN_CONTEXT, text=cwd))
            elif ev_type == "compaction/prune" and not prune_reported:
                prune_reported = True
                out.append(TimelineEvent(
                    timestamp=ts, kind=EventKind.COMPACTION,
                    metadata={"auto": True, "prune_count": len(prunes),
                              "dropped_tokens": prune_dropped},
                ))
        return out

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _row(self, header: dict, events: list[dict], session_dir: Path,
             path: Path) -> SessionRow:
        active = _current_events(events)
        meta = self._meta(header, events, path, session_dir=session_dir)
        return SessionRow(
            source="dsh",
            session_id=meta.session_id,
            project=meta.cwd,
            title=meta.title,
            lines=len(events),
            user_prompts=len(_prompts(active)),
            tool_calls=len(_tool_calls(active)),
            assistant_texts=len(_assistant_texts(active)),
            compacts=len(_compaction_boundaries(active)),
            last_ts=meta.last_ts,
            file_size=meta.file_size,
            tokens_input=meta.tokens_input,
            tokens_output=meta.tokens_output,
            duration_s=meta.duration_s,
        )

    def _meta(self, header: dict, events: list[dict], path: Path,
              session_dir: Path | None = None) -> SessionMeta:
        if session_dir is None:
            session_dir = path.parent
        active = _current_events(events)
        tokens_input, tokens_output, active_ms = _stats(active)
        first_ts, last_ts = _event_timestamps(events, header)
        descriptor = _descriptor(active)
        nickname = _str_field(descriptor, "label")
        cwd = _header_cwd(header)
        agent = _str_field(header, "agentPreset")
        return SessionMeta(
            source="dsh",
            session_id=_header_session_id(header, session_dir),
            title=_title(active),
            cwd=cwd,
            model=_model(active),
            version="",
            git_branch="",
            line_count=len(events),
            first_ts=first_ts,
            last_ts=last_ts,
            compact_count=len(_compaction_boundaries(active)),
            file_size=_session_size(session_dir),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_s=active_ms // 1000,
            agent=agent,
            source_label="deepseek-harness",
            is_subagent=_is_subagent_header(header),
            nickname=nickname,
        )
