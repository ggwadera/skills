"""Shared helpers for the coding-sessions test suite.

All fixtures are synthetic and are written into temporary directories; the
suite never reads real session data.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from sources.claude import _encode_cwd
from sources import dsh as dsh_module

BASE_MS = 1731369600000


def j(obj):
    return json.dumps(obj)


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def insert(conn, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


V1_DDL = (
    "CREATE TABLE session ("
    " id TEXT PRIMARY KEY, title TEXT, directory TEXT, agent TEXT,"
    " model TEXT, version TEXT, cost REAL, tokens_input INTEGER,"
    " tokens_output INTEGER, time_created INTEGER, time_updated INTEGER,"
    " parent_id TEXT)",
    "CREATE TABLE message ("
    " id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)",
    "CREATE TABLE part ("
    " id TEXT PRIMARY KEY, session_id TEXT, message_id TEXT,"
    " time_created INTEGER, data TEXT)",
    "CREATE TABLE todo ("
    " session_id TEXT, content TEXT, status TEXT, priority TEXT,"
    " position INTEGER)",
)

V2_DDL = (
    "CREATE TABLE session_v2 ("
    " id TEXT PRIMARY KEY, title TEXT, directory TEXT, agent TEXT,"
    " model TEXT, version TEXT, cost REAL, tokens_input INTEGER,"
    " tokens_output INTEGER, time_created INTEGER, time_updated INTEGER,"
    " parent_id TEXT)",
    "CREATE TABLE session_message ("
    " id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,"
    " time_created INTEGER, data TEXT)",
    "CREATE TABLE session_part ("
    " session_id TEXT, message_id TEXT, ordinal INTEGER, type TEXT,"
    " call_id TEXT, data TEXT)",
)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class OpencodeDbTestCase(TempDirTestCase):
    """Points OPENCODE_DB at a temp database file."""

    def setUp(self):
        super().setUp()
        self.db_path = self.root / "opencode.db"
        self._previous_db = os.environ.get("OPENCODE_DB")
        os.environ["OPENCODE_DB"] = str(self.db_path)
        self.addCleanup(self._restore_db)

    def _restore_db(self):
        if self._previous_db is None:
            os.environ.pop("OPENCODE_DB", None)
        else:
            os.environ["OPENCODE_DB"] = self._previous_db

    def use_db(self, path):
        os.environ["OPENCODE_DB"] = str(path)

    def create_db(self, path=None, schemas=("v1",)):
        path = Path(path) if path is not None else self.db_path
        conn = sqlite3.connect(path)
        for schema in schemas:
            for stmt in V1_DDL if schema == "v1" else V2_DDL:
                conn.execute(stmt)
        conn.commit()
        return conn


class DshHomeTestCase(TempDirTestCase):
    """Points DSH_HOME at a temp directory and clears the parse cache."""

    def setUp(self):
        super().setUp()
        self.dsh_home = self.root / "dsh"
        self._previous_home = os.environ.get("DSH_HOME")
        os.environ["DSH_HOME"] = str(self.dsh_home)
        self.addCleanup(self._restore_home)
        dsh_module._PARSE_CACHE.clear()

    def _restore_home(self):
        if self._previous_home is None:
            os.environ.pop("DSH_HOME", None)
        else:
            os.environ["DSH_HOME"] = self._previous_home


CLAUDE_CLI_CWD = "/Users/me/project"
CLAUDE_CLI_SESSION_ID = "sess-claude-1"
CODEX_CLI_UUID = "11111111-2222-3333-4444-555555555555"
OPENCODE_CLI_SESSION_ID = "ses_cli_1"


def build_claude_fixture(claude_dir):
    lines = [
        {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
         "sessionId": CLAUDE_CLI_SESSION_ID, "cwd": CLAUDE_CLI_CWD,
         "version": "1.0.99", "gitBranch": "main",
         "message": {"role": "user", "content": "Fix the login bug"}},
        {"type": "assistant", "timestamp": "2026-11-01T10:01:00.000Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4.5",
                     "content": [{"type": "text", "text": "I will fix it."}]}},
        {"type": "system", "subtype": "compact_boundary",
         "timestamp": "2026-11-01T10:02:00.000Z",
         "compactMetadata": {"trigger": "manual", "preTokens": 1000,
                             "postTokens": 500, "cumulativeDroppedTokens": 500,
                             "durationMs": 2000}},
        {"type": "system", "subtype": "away_summary",
         "timestamp": "2026-11-01T10:03:00.000Z",
         "content": "Login bug fixed, tests running"},
        {"type": "ai-title", "timestamp": "2026-11-01T10:04:00.000Z",
         "aiTitle": "Fix login bug"},
    ]
    encoded = _encode_cwd(CLAUDE_CLI_CWD)
    write_jsonl(Path(claude_dir) / "projects" / encoded / f"{CLAUDE_CLI_SESSION_ID}.jsonl", lines)
    return CLAUDE_CLI_SESSION_ID


def build_codex_fixture(codex_dir):
    codex_dir = Path(codex_dir)
    lines = [
        {"type": "session_meta", "timestamp": "2026-11-12T10:00:00.000Z",
         "payload": {"id": "s-codex-cli", "cwd": "/work/project",
                     "cli_version": "0.45.0", "source": "vscode"}},
        {"type": "event_msg", "timestamp": "2026-11-12T10:00:05.000Z",
         "payload": {"type": "item_completed",
                     "item": {"type": "UserMessage",
                              "content": [{"type": "input_text",
                                           "text": "Fix the flaky test"}]}}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:10.000Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "Running the tests."}]}},
    ]
    name = f"rollout-2026-11-12T10-00-00-{CODEX_CLI_UUID}.jsonl"
    write_jsonl(codex_dir / "sessions" / "2026" / "11" / "12" / name, lines)
    write_jsonl(codex_dir / "session_index.jsonl",
                [{"id": CODEX_CLI_UUID, "thread_name": "Fix flaky tests"}])
    return CODEX_CLI_UUID


def build_opencode_fixture(db_path):
    conn = sqlite3.connect(db_path)
    for stmt in V2_DDL:
        conn.execute(stmt)
    insert(conn, "session_v2", {
        "id": OPENCODE_CLI_SESSION_ID, "title": "CLI opencode session",
        "directory": "/work/cli", "agent": "build", "model": j({"id": "k2"}),
        "version": "2.1.0", "cost": 0.25, "tokens_input": 200,
        "tokens_output": 100, "time_created": BASE_MS,
        "time_updated": BASE_MS + 60000, "parent_id": None,
    })
    insert(conn, "session_message", {
        "id": "m1", "session_id": OPENCODE_CLI_SESSION_ID, "type": "user",
        "seq": 1, "time_created": BASE_MS + 1000,
        "data": j({"text": "Fix the failing test",
                   "time": {"created": BASE_MS + 1500}}),
    })
    insert(conn, "session_message", {
        "id": "m2", "session_id": OPENCODE_CLI_SESSION_ID, "type": "assistant",
        "seq": 2, "time_created": BASE_MS + 10000,
        "data": j({"content": [{"type": "text", "text": "Running the suite"}]}),
    })
    insert(conn, "session_message", {
        "id": "m3", "session_id": OPENCODE_CLI_SESSION_ID,
        "type": "compaction", "seq": 3, "time_created": BASE_MS + 20000,
        "data": j({"reason": "auto", "summary": "Failing test recap"}),
    })
    conn.commit()
    conn.close()
    return OPENCODE_CLI_SESSION_ID


# ---------------------------------------------------------------------------
# DeepSeek harness (dsh) fixtures
# ---------------------------------------------------------------------------

DSH_CWD = "/Users/me/project"
DSH_SESSION_ID = "session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DSH_CHILD_ID = "session-11111111-2222-3333-4444-555555555555"


def dsh_project_key(cwd):
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", cwd.strip("/"))
    return f"--{slug}--"


def dsh_header_events(session_id=DSH_SESSION_ID, cwd=DSH_CWD):
    """Return (header, events) for a synthetic v4 harness session.

    Covers real prompts plus an injected hook message, reasoning, usage
    accounting, a refreshed tool result, an edited prompt, a compaction
    transaction, todos, a subagent catalog entry, an erroring tool call,
    and turn markers for active-time accounting.
    """
    t0 = BASE_MS
    header = {
        "type": "session", "version": 4, "id": session_id,
        "createdAt": t0, "cwd": cwd, "isSeeded": False,
        "delegationDepth": 0, "agentPreset": "standard",
    }
    events = [
        {"type": "permission/preset", "seq": 0, "time": t0 + 100,
         "data": {"preset": "workspace-write"}},
        {"type": "request/context", "seq": 1, "time": t0 + 200,
         "data": {"provider": "deepseek-account", "model": "deepseek-flash",
                  "contextWindow": 1048576}},
        {"type": "turn/start", "seq": 2, "time": t0 + 300, "data": {"turn": 1}},
        {"type": "user/message", "seq": 3, "time": t0 + 400,
         "surfaceOp": "append",
         "data": {"role": "user", "id": "m1",
                  "source": {"kind": "user", "rpcId": "r1"},
                  "content": [{"type": "text", "text": "Fix the flaky test"}]}},
        {"type": "user/message", "seq": 4, "time": t0 + 450,
         "surfaceOp": "append",
         "data": {"role": "user", "id": "m2",
                  "source": {"kind": "hook", "reason": "startup"},
                  "content": [{"type": "text",
                               "text": "<hook>context injection</hook>"}]}},
        {"type": "session/title", "seq": 5, "time": t0 + 500,
         "data": {"title": "Fix flaky test", "messageSeqs": [3],
                  "source": {"kind": "user"}}},
        {"type": "assistant/message", "seq": 6, "time": t0 + 700,
         "data": {"turn": 1, "step": 1,
                  "message": {"role": "assistant", "content": [
                      {"type": "reasoning", "text": "checking logs"},
                      {"type": "text", "text": "Running the tests."}]},
                  "usage": {"inputTokens": 80, "outputTokens": 40,
                            "cacheReadTokens": 0, "cacheWriteTokens": 0,
                            "totalTokens": 120}}},
        {"type": "tool/call", "seq": 7, "time": t0 + 750,
         "data": {"turn": 1, "step": 1, "callId": "fc1", "name": "bash",
                  "arguments": "{\"command\": \"pytest\"}"}},
        {"type": "tool/result", "seq": 8, "time": t0 + 800,
         "surfaceOp": "append", "sourceEventSeqs": [7],
         "data": {"turn": 1, "step": 1,
                  "message": {"role": "tool", "toolCallId": "fc1",
                              "source": {"kind": "tool", "callId": "fc1"},
                              "content": [{"type": "text", "text": "3 passed"}],
                              "isError": False}}},
        {"type": "assistant/message", "seq": 9, "time": t0 + 900,
         "data": {"turn": 1, "step": 2,
                  "message": {"role": "assistant", "content": [
                      {"type": "tool-call", "callId": "fc2", "name": "edit"},
                      {"type": "text", "text": "Applying the fix."}]}}},
        {"type": "tool/call", "seq": 10, "time": t0 + 950,
         "data": {"turn": 1, "step": 2, "callId": "fc2", "name": "edit",
                  "arguments": "{\"file_path\": \"src/app.py\"}"}},
        {"type": "tool/result", "seq": 11, "time": t0 + 1000,
         "surfaceOp": "append", "sourceEventSeqs": [10],
         "data": {"turn": 1, "step": 2,
                  "message": {"role": "tool", "toolCallId": "fc2",
                              "source": {"kind": "tool", "callId": "fc2"},
                              "content": [{"type": "text", "text": "patched"}],
                              "isError": False}}},
        {"type": "turn/end", "seq": 12, "time": t0 + 1400,
         "data": {"turn": 1, "reason": "completed"}},
        {"type": "turn/start", "seq": 13, "time": t0 + 2000,
         "data": {"turn": 2}},
        {"type": "user/message", "seq": 14, "time": t0 + 2100,
         "surfaceOp": "append",
         "data": {"role": "user", "id": "m3",
                  "source": {"kind": "user", "rpcId": "r2"},
                  "content": [{"type": "text", "text": "Add a retry"}]}},
        {"type": "assistant/message", "seq": 15, "time": t0 + 2200,
         "data": {"turn": 2, "step": 1,
                  "message": {"role": "assistant", "content": [
                      {"type": "text", "text": "Adding retry logic."}]},
                  "usage": {"inputTokens": 696, "outputTokens": 725,
                            "cacheReadTokens": 8320, "cacheWriteTokens": 0,
                            "totalTokens": 9741}}},
        {"type": "compaction/start", "seq": 16, "time": t0 + 3000,
         "data": {"compactionId": "c1", "turn": 2}},
        {"type": "compaction/summary", "seq": 17, "time": t0 + 3100,
         "data": {"compactionId": "c1",
                  "summary": [{"type": "text",
                               "text": "Recap of earlier work"}],
                  "shadowedRange": {"start": 3, "end": 12},
                  "shadowedSeqs": [3, 6, 7, 8, 9, 10, 11],
                  "shadowedTokenCount": 500,
                  "provider": "deepseek-account", "model": "deepseek-flash",
                  "usage": {"inputTokens": 1000, "outputTokens": 100,
                            "cacheReadTokens": 0}}},
        {"type": "compaction/end", "seq": 18, "time": t0 + 3200,
         "data": {"compactionId": "c1", "turn": 2}},
        {"type": "todo/write", "seq": 19, "time": t0 + 3300,
         "data": {"todos": [
             {"content": "Fix flaky test", "status": "in_progress"},
             {"content": "Ship the fix", "status": "pending"}]}},
        {"type": "subagent/catalog", "seq": 20, "time": t0 + 3400,
         "data": {"childId": DSH_CHILD_ID, "childCreatedAt": t0,
                  "version": 3, "name": "explorer", "mode": "continuable",
                  "label": "Explore codebase"}},
        {"type": "tool/call", "seq": 21, "time": t0 + 3500,
         "data": {"turn": 2, "step": 2, "callId": "fc3", "name": "bash",
                  "arguments": "{\"command\": \"bad-cmd\"}"}},
        {"type": "tool/result", "seq": 22, "time": t0 + 3600,
         "surfaceOp": "append",
         "data": {"turn": 2, "step": 2,
                  "error": {"name": "ExecError", "code": "exit1",
                            "reason": "command failed"},
                  "message": {"role": "tool", "toolCallId": "fc3",
                              "source": {"kind": "tool", "callId": "fc3"},
                              "content": [{"type": "text",
                                           "text": "command not found"}],
                              "isError": True}}},
        {"type": "working-directory/change", "seq": 23, "time": t0 + 3700,
         "data": {"cwd": "/Users/me/project/sub"}},
        {"type": "tool/result", "seq": 24, "time": t0 + 4000,
         "surfaceOp": {"op": "replace", "startSeq": 11, "endSeq": 11},
         "sourceEventSeqs": [10],
         "data": {"turn": 1, "step": 2,
                  "message": {"role": "tool", "toolCallId": "fc2",
                              "source": {"kind": "tool", "callId": "fc2"},
                              "content": [{"type": "text",
                                           "text": "patched (updated)"}],
                              "isError": False}}},
        {"type": "user/message", "seq": 25, "time": t0 + 4100,
         "surfaceOp": {"op": "replace", "startSeq": 14, "endSeq": 14},
         "sourceEventSeqs": [14],
         "data": {"role": "user", "id": "m3",
                  "source": {"kind": "user", "rpcId": "r3"},
                  "content": [{"type": "text",
                               "text": "Add a retry with backoff"}]}},
        {"type": "turn/end", "seq": 26, "time": t0 + 5100,
         "data": {"turn": 2, "reason": "completed"}},
    ]
    return header, events


def compress_zstd(data):
    """Compress with an available zstd backend, or return None."""
    try:
        from compression import zstd
    except ImportError:
        zstd = None
    if zstd is not None:
        try:
            return zstd.compress(data)
        except Exception:
            pass
    try:
        import zstandard
    except ImportError:
        zstandard = None
    if zstandard is not None:
        try:
            return zstandard.ZstdCompressor().compress(data)
        except Exception:
            pass
    if shutil.which("zstd"):
        proc = subprocess.run(["zstd", "-q", "-c"], input=data,
                              capture_output=True)
        if proc.returncode == 0:
            return proc.stdout
    return None


def zstd_available():
    try:
        import compression.zstd  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return shutil.which("zstd") is not None


def build_dsh_fixture(dsh_home, compressed=False, session_id=DSH_SESSION_ID,
                      cwd=DSH_CWD, lines=None):
    """Write a synthetic harness session under a DSH_HOME directory."""
    home = Path(dsh_home)
    if lines is None:
        header, events = dsh_header_events(session_id, cwd)
        lines = [json.dumps(header)] + [json.dumps(ev) for ev in events]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    session_dir = home / "sessions" / dsh_project_key(cwd) / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    if compressed:
        payload = compress_zstd(data)
        if payload is None:
            raise RuntimeError("no zstd backend available for fixture")
        path = session_dir / "session.v4.jsonl.zstd"
        path.write_bytes(payload)
    else:
        path = session_dir / "session.v4.jsonl"
        path.write_bytes(data)
    return session_id


def encode_cwd(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)