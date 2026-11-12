#!/usr/bin/env python3
"""Extract and inspect session transcripts from Claude Code, Codex, and opencode.

Claude Code stores session transcripts as JSONL files under
~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl.

Codex stores session transcripts as JSONL files under
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl (active)
and ~/.codex/archived_sessions/rollout-<timestamp>-<session-id>.jsonl (archived).

opencode stores sessions in a SQLite database at
~/.local/share/opencode/opencode.db (overridable via OPENCODE_DB).

This script provides:
  list            List sessions for a given directory or from all sources
  summary         One-page summary: metadata, user prompts, titles
  timeline        Full conversation timeline with assistant text + tool calls
  compact         Extract compaction boundaries and any preserved summary text
  context         Build a continuation prompt from the latest compact summary
                  (or full session if never compacted) for pasting into a new
                  opencode session

Usage:
  python3 session_extract.py list --dir /Users/me/myproject
  python3 session_extract.py list --source codex
  python3 session_extract.py summary <session-id> --source opencode
  python3 session_extract.py timeline <session-id> --dir /Users/me/myproject
  python3 session_extract.py compact <session-id> --dir /Users/me/myproject
  python3 session_extract.py context <session-id> --source codex
  python3 session_extract.py list --all
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

from sources import SOURCES, get_source, resolve_session
from sources.base import EventKind


def parse_timestamp(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts


def _truncate(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit] + " [...]"
    return text


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _default_dir(args):
    dir_path = getattr(args, "dir", None)
    if dir_path is None and not getattr(args, "all", False):
        return os.getcwd()
    return dir_path


# ---------------------------------------------------------------------------
# Shared formatters
# ---------------------------------------------------------------------------

def format_list(args) -> int:
    source = getattr(args, "source", "auto") or "auto"
    needle = getattr(args, "grep", None)
    if needle and getattr(args, "dir", None) is None:
        dir_path = None
        all_dirs = True
    else:
        dir_path = _default_dir(args)
        all_dirs = getattr(args, "all", False)
    limit = args.limit
    if limit is not None and limit < 0:
        limit = 0
    scan_limit = -1 if needle else (limit if limit is not None else 200)
    rows = []
    failures: list[tuple[str, str]] = []

    names = list(SOURCES) if source == "auto" else [source]
    for name in names:
        adapter = get_source(name)
        try:
            rows.extend(adapter.list_sessions(
                dir_path=dir_path, all_dirs=all_dirs, limit=scan_limit,
            ))
        except (OSError, sqlite3.Error) as exc:
            failures.append((name, str(exc)))

    for name, message in failures:
        print(f"warning: {name} source unavailable: {message}", file=sys.stderr)

    if needle:
        needle = needle.lower()
        filtered = []
        for row in rows:
            try:
                if get_source(row.source).matches(row.session_id, dir_path, needle):
                    filtered.append(row)
            except (OSError, sqlite3.Error):
                continue
        rows = filtered

    if not rows:
        if needle:
            print(f"No sessions matched '{needle}'.")
        elif failures:
            print("No sessions found (some sources were unavailable).")
        elif source == "claude":
            d = dir_path or os.getcwd()
            print(f"No Claude Code sessions found for directory: {d}")
            print("  Use --all to list from all directories, or --source codex/opencode")
        elif source == "codex":
            print("No Codex sessions found.")
        elif source == "opencode":
            print("No opencode sessions found in the database.")
        else:
            print("No sessions found from any source.")
        return 0

    rows.sort(key=lambda r: r.last_ts, reverse=True)

    if limit is not None and len(rows) > limit:
        rows = rows[:limit]

    print(f"{'Source':<10} {'Session ID':<42} {'Title':<40} {'Lines':>6} {'Prompts':>8} {'Tools':>6} {'Cmp':>4} {'Last Active':<23}")
    print("-" * 146)
    for r in rows:
        title = _truncate(r.title, 33) if len(r.title) > 40 else r.title
        ts = parse_timestamp(r.last_ts)
        print(f"{r.source:<10} {r.session_id:<42} {title:<40} {r.lines:>6} {r.user_prompts:>8} {r.tool_calls:>6} {r.compacts:>4} {ts:<23}")

    print(f"\nTotal: {len(rows)} session(s)")
    return 0


def format_summary(args) -> int:
    resolved = resolve_session(args.session, getattr(args, "source", "auto"), args.dir)
    if not resolved:
        print(f"Error: session '{args.session}' not found.")
        print("  Use 'list' to see available sessions.")
        return 1
    adapter, handle = resolved

    meta = adapter.metadata(handle, args.session)
    prompts = adapter.user_prompts(handle)
    tool_calls = adapter.tool_calls(handle)
    boundaries = adapter.compact_boundaries(handle)
    away = adapter.away_summaries(handle)
    subagents = adapter.subagents(handle)
    reasoning = adapter.reasoning(handle)
    todos = adapter.todos(args.session)
    files = adapter.files_edited(args.session)

    print(f"=== SESSION SUMMARY: {args.session} ===")
    print(f"  Source:       {adapter.name}")
    print(f"  Title:        {meta.title or '(none)'}")
    print(f"  Session ID:   {meta.session_id}")
    print(f"  CWD:          {meta.cwd}")
    print(f"  Model:        {meta.model or '(unknown)'}")
    if meta.version:
        print(f"  Version:      {meta.version}")
    if meta.git_branch:
        print(f"  Git branch:   {meta.git_branch}")
    if meta.agent:
        print(f"  Agent:        {meta.agent}")
    if meta.cost:
        print(f"  Cost:         {meta.cost:.4f}")
    if meta.tokens_input or meta.tokens_output:
        print(f"  Tokens:       {meta.tokens_input:,} in / {meta.tokens_output:,} out")
    if meta.duration_s:
        print(f"  Duration:     {meta.duration_s}s")
    print(f"  Lines/parts:  {meta.line_count}")
    print(f"  First event:  {parse_timestamp(meta.first_ts)}")
    print(f"  Last event:   {parse_timestamp(meta.last_ts)}")
    print(f"  User prompts: {len(prompts)}")
    print(f"  Tool calls:   {len(tool_calls)}")
    if boundaries:
        print(f"  Compacts:     {len(boundaries)}")
    if reasoning:
        print(f"  Reasoning:    {len(reasoning)}")
    if meta.file_size:
        print(f"  File size:    {meta.file_size:,} bytes")

    if subagents:
        print(f"  Subagents:    {len(subagents)}")
        for sa in subagents:
            label = sa.agent_type or sa.nickname or sa.agent_id
            desc = sa.description or sa.nickname or ""
            if desc:
                print(f"    - {label}: {desc[:80]}")
            else:
                print(f"    - {label}")

    print()
    print("--- User Prompts ---")
    print()
    max_prompts = getattr(args, "max_prompts", None)
    if max_prompts is not None and max_prompts < 0:
        max_prompts = 0
    shown = prompts
    if max_prompts is not None and len(prompts) > max_prompts:
        omitted = len(prompts) - max_prompts
        shown = prompts[len(prompts) - max_prompts:] if max_prompts else []
        print(f"  ... {omitted} earlier prompt(s) omitted (use --max-prompts to change)")
        print()
    start = len(prompts) - len(shown) + 1
    for i, p in enumerate(shown, start):
        text = _truncate(p.text, 500)
        text = text.replace("\n", "\n    ")
        print(f"  [{i}] ({parse_timestamp(p.timestamp)})")
        print(f"    {text}")
        print()

    if boundaries:
        print("--- Compaction Boundaries ---")
        print()
        for b in boundaries:
            print(f"  {parse_timestamp(b.timestamp)} — trigger={b.trigger}"
                  f" pre={b.pre_tokens:,} post={b.post_tokens:,} dropped={b.dropped_tokens:,}"
                  f" ({b.duration_ms/1000:.1f}s)")
        print()

    if away:
        print("--- Away Summaries ---")
        print()
        for s in away:
            print(f"  {parse_timestamp(s.timestamp)}:")
            print(f"    {_truncate(s.content, 500)}")
            print()

    if reasoning:
        print("--- Reasoning Summaries ---")
        print()
        for r in reasoning[:20]:
            print(f"  ({parse_timestamp(r.timestamp)}) {_truncate(r.text, 300)}")
        if len(reasoning) > 20:
            print(f"  ... and {len(reasoning) - 20} more")
        print()

    if todos:
        print("--- Todos ---")
        print()
        for t in todos:
            print(f"  [{t.status}] {t.priority} — {_truncate(t.content, 80)}")
        print()

    if files:
        print("--- Files Edited ---")
        print()
        for f in files:
            print(f"  {f}")
        print()
    return 0


def format_timeline(args) -> int:
    resolved = resolve_session(args.session, getattr(args, "source", "auto"), args.dir)
    if not resolved:
        print(f"Error: session '{args.session}' not found.")
        print("  Use 'list' to see available sessions.")
        return 1
    adapter, handle = resolved

    meta = adapter.metadata(handle, args.session)
    events = adapter.timeline_events(handle)

    since = _parse_dt(getattr(args, "since", None))
    until = _parse_dt(getattr(args, "until", None))
    if since or until:
        filtered = []
        for event in events:
            event_dt = _parse_dt(event.timestamp)
            if event_dt is None:
                continue
            if since and event_dt < since:
                continue
            if until and event_dt > until:
                continue
            filtered.append(event)
        events = filtered

    tail = getattr(args, "tail", None)
    if tail is not None and tail < 0:
        tail = 0
    if tail is not None and len(events) > tail:
        events = events[len(events) - tail:] if tail else []

    print(f"=== SESSION TIMELINE: {args.session} ===")
    print(f"  Source:  {adapter.name}")
    print(f"  Title:   {meta.title or '(none)'}")
    print(f"  CWD:     {meta.cwd}")
    print(f"  Model:   {meta.model or '(unknown)'}")
    print()

    for ev in events:
        ts = parse_timestamp(ev.timestamp)
        kind = ev.kind

        if kind == EventKind.USER_MESSAGE:
            text = _truncate(ev.text, 1000)
            print(f"  [{ts}] USER:")
            for line in text.split("\n"):
                print(f"    {line}")
        elif kind == EventKind.ASSISTANT_TEXT:
            text = _truncate(ev.text, 2000)
            print(f"  [{ts}] ASSISTANT:")
            for line in text.split("\n"):
                print(f"    {line}")
        elif kind == EventKind.TOOL_CALL:
            inp = _truncate(ev.tool_input, 300)
            status = f" [{ev.tool_status}]" if ev.tool_status else ""
            print(f"  [{ts}] TOOL_CALL({ev.tool_name}){status}: {inp}")
        elif kind == EventKind.TOOL_RESULT:
            text = _truncate(ev.text, 200).replace("\n", " ")
            tid = ev.tool_id[:20] if ev.tool_id else ""
            print(f"  [{ts}] TOOL_RESULT({tid}): {text}")
        elif kind == EventKind.THINKING:
            text = _truncate(ev.text, 200).replace("\n", " ")
            print(f"  [{ts}] THINKING: {text}")
        elif kind == EventKind.SLASH_COMMAND:
            print(f"  [{ts}] SLASH_COMMAND: /{ev.text}")
        elif kind == EventKind.COMPACT_BOUNDARY:
            m = ev.metadata
            print(f"  [{ts}] COMPACT_BOUNDARY — pre={m.get('pre_tokens',0)} post={m.get('post_tokens',0)} ({m.get('trigger','')})")
        elif kind == EventKind.COMPACTION:
            m = ev.metadata
            auto = "auto" if m.get("auto") else "manual"
            print(f"  [{ts}] COMPACTION ({auto})")
        elif kind == EventKind.AWAY_SUMMARY:
            text = _truncate(ev.text, 300).replace("\n", " ")
            print(f"  [{ts}] AWAY_SUMMARY: {text}")
        elif kind == EventKind.TURN_DURATION:
            dur = ev.metadata.get("duration_ms", 0)
            print(f"  [{ts}] TURN_DURATION: {dur/1000:.1f}s")
        elif kind == EventKind.AI_TITLE:
            print(f"  [{ts}] AI_TITLE: {ev.text}")
        elif kind == EventKind.REASONING:
            text = _truncate(ev.text, 300)
            print(f"  [{ts}] REASONING: {text}")
        elif kind == EventKind.TOKEN_COUNT:
            m = ev.metadata
            print(f"  [{ts}] TOKEN_COUNT: total={m.get('total_tokens',0):,} input={m.get('input_tokens',0):,} output={m.get('output_tokens',0):,}")
        elif kind == EventKind.SESSION_META:
            m = ev.metadata
            print(f"  [{ts}] SESSION_META: id={m.get('session_id','')} cwd={m.get('cwd','')} version={m.get('version','')}")
        elif kind == EventKind.TURN_CONTEXT:
            print(f"  [{ts}] TURN_CONTEXT: cwd={ev.text}")
        elif kind == EventKind.WORLD_STATE:
            print(f"  [{ts}] WORLD_STATE: {ev.text[:200]}")
        elif kind == EventKind.TASK_STARTED:
            m = ev.metadata
            print(f"  [{ts}] TASK_STARTED: turn={m.get('turn_id','')[:20]} model_ctx={m.get('model_ctx',0)}")
        elif kind == EventKind.FILE_EDIT:
            print(f"  [{ts}] FILE_EDIT: {ev.text}")
    return 0


def format_compact(args) -> int:
    resolved = resolve_session(args.session, getattr(args, "source", "auto"), args.dir)
    if not resolved:
        print(f"Error: session '{args.session}' not found.")
        print("  Use 'list' to see available sessions.")
        return 1
    adapter, handle = resolved

    boundaries = adapter.compact_boundaries(handle)
    away = adapter.away_summaries(handle)

    if not boundaries and not away:
        print(f"No compaction boundaries found in this session (source: {adapter.name}).")
        return 0

    print(f"=== COMPACTION BOUNDARIES: {args.session} ===")
    print()
    for i, b in enumerate(boundaries, 1):
        print(f"  Compact #{i}:")
        print(f"    Timestamp:  {parse_timestamp(b.timestamp)}")
        print(f"    Trigger:    {b.trigger}")
        if b.pre_tokens or b.post_tokens:
            print(f"    Pre tokens: {b.pre_tokens:,}")
            print(f"    Post tokens:{b.post_tokens:,}")
            print(f"    Dropped:    {b.dropped_tokens:,}")
        if b.duration_ms:
            print(f"    Duration:   {b.duration_ms/1000:.1f}s")
        print()

    if away:
        print("--- Away Summaries ---")
        print()
        for s in away:
            print(f"  {parse_timestamp(s.timestamp)}:")
            print(f"    {s.content}")
            print()
    return 0


def format_context(args) -> int:
    resolved = resolve_session(args.session, getattr(args, "source", "auto"), args.dir)
    if not resolved:
        print(f"Error: session '{args.session}' not found.")
        print("  Use 'list' to see available sessions.")
        return 1
    adapter, handle = resolved

    meta = adapter.metadata(handle, args.session)
    prompts = adapter.user_prompts(handle)
    assistant_texts = adapter.assistant_texts(handle)
    away = adapter.away_summaries(handle)
    reasoning = adapter.reasoning(handle)

    print("=== CONTINUATION CONTEXT ===")
    print()
    print(f"Source:          {adapter.name}")
    print(f"Original session: {meta.session_id}")
    print(f"CWD: {meta.cwd}")
    print(f"Title: {meta.title or '(none)'}")
    print(f"Model: {meta.model}")
    print(f"Last active: {parse_timestamp(meta.last_ts)}")
    if meta.compact_count:
        print(f"Compacts: {meta.compact_count}")
    print()

    if away:
        latest = away[-1]
        print("--- Auto-generated Recap (from away_summary) ---")
        print()
        print(latest.content)
        print()

    if reasoning and not away:
        latest_reasoning = reasoning[-5:]
        print("--- Recent Reasoning Summaries ---")
        print()
        for r in latest_reasoning:
            print(f"  ({parse_timestamp(r.timestamp)}) {_truncate(r.text, 500)}")
        print()

    print("--- User Prompts (in order) ---")
    print()
    for i, p in enumerate(prompts, 1):
        text = _truncate(p.text, 1000)
        print(f"  [{i}] {parse_timestamp(p.timestamp)}")
        print(f"  {text}")
        print()

    recent_texts = assistant_texts[-5:] if len(assistant_texts) > 5 else assistant_texts
    if recent_texts:
        print("--- Last Assistant Responses ---")
        print()
        for t in recent_texts:
            text = _truncate(t.text, 2000)
            print(f"  ({parse_timestamp(t.timestamp)})")
            for line in text.split("\n"):
                print(f"    {line}")
            print()

    print("--- Continuation Prompt ---")
    print()
    print("Copy the above context into a new session, or use it to formulate")
    print("a prompt that picks up where this session left off.")
    print()
    print("Example continuation prompt:")
    print()
    last_prompt = prompts[-1].text[:200] if prompts else ""
    source_label = adapter.name.capitalize()
    print(f"  \"I'm continuing work from a previous {source_label} session about '{meta.title or 'this topic'}'.")
    print(f"   The last thing I asked was: {last_prompt}...")
    print("   Here's the context: [paste above]\"")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Claude Code, Codex, and opencode session transcripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode")

    # list
    p_list = sub.add_parser("list", help="List sessions")
    p_list.add_argument("--dir", help="Filter by this directory (default: $PWD)")
    p_list.add_argument("--all", action="store_true", help="List sessions from all directories")
    p_list.add_argument("--source", choices=["auto", "claude", "codex", "opencode"], default="auto", help="Filter by source (default: auto)")
    p_list.add_argument("--limit", type=int, default=50, help="Max sessions to list (default: 50)")
    p_list.add_argument("--grep", help="Only show sessions whose title or user prompts contain this text")

    # summary
    p_sum = sub.add_parser("summary", help="One-page summary of a session")
    p_sum.add_argument("session", help="Session ID")
    p_sum.add_argument("--dir", help="Project directory (default: $PWD)")
    p_sum.add_argument("--source", choices=["auto", "claude", "codex", "opencode"], default="auto", help="Filter by source (default: auto)")
    p_sum.add_argument("--max-prompts", type=int, default=None, help="Show only the last N user prompts")

    # timeline
    p_tl = sub.add_parser("timeline", help="Full conversation timeline")
    p_tl.add_argument("session", help="Session ID")
    p_tl.add_argument("--dir", help="Project directory (default: $PWD)")
    p_tl.add_argument("--source", choices=["auto", "claude", "codex", "opencode"], default="auto", help="Filter by source (default: auto)")
    p_tl.add_argument("--since", help="Only show events at or after this ISO timestamp")
    p_tl.add_argument("--until", help="Only show events at or before this ISO timestamp")
    p_tl.add_argument("--tail", type=int, default=None, help="Show only the last N events")

    # compact
    p_comp = sub.add_parser("compact", help="Extract compaction boundaries")
    p_comp.add_argument("session", help="Session ID")
    p_comp.add_argument("--dir", help="Project directory (default: $PWD)")
    p_comp.add_argument("--source", choices=["auto", "claude", "codex", "opencode"], default="auto", help="Filter by source (default: auto)")

    # context
    p_ctx = sub.add_parser("context", help="Build continuation prompt from latest state")
    p_ctx.add_argument("session", help="Session ID")
    p_ctx.add_argument("--dir", help="Project directory (default: $PWD)")
    p_ctx.add_argument("--source", choices=["auto", "claude", "codex", "opencode"], default="auto", help="Filter by source (default: auto)")

    args = parser.parse_args()

    try:
        if args.mode == "list":
            return format_list(args)
        if args.mode == "summary":
            return format_summary(args)
        if args.mode == "timeline":
            return format_timeline(args)
        if args.mode == "compact":
            return format_compact(args)
        if args.mode == "context":
            return format_context(args)
        parser.print_help()
        return 0
    except (OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
