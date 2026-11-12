"""Shared helpers for the coding-sessions test suite.

All fixtures are synthetic and are written into temporary directories; the
suite never reads real session data.
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from sources.claude import _encode_cwd

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


def encode_cwd(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)