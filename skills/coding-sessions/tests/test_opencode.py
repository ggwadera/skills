import sqlite3
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from session_fixtures import BASE_MS, OpencodeDbTestCase, insert, j
from sources.base import EventKind
from sources.opencode import OpencodeHandle, OpencodeSource
from sources.opencode_base import detect_schemas, ms_to_iso
from sources.opencode_v1 import OpencodeV1Source
from sources.opencode_v2 import OpencodeV2Source


def build_v1(conn):
    insert(conn, "session", {
        "id": "ses_v1_a", "title": "V1 session", "directory": "/work/v1",
        "agent": "build", "model": j({"id": "qwen3"}), "version": "1.6.0",
        "cost": 0.5, "tokens_input": 100, "tokens_output": 50,
        "time_created": BASE_MS, "time_updated": BASE_MS + 60000,
        "parent_id": None,
    })
    insert(conn, "session", {
        "id": "ses_v1_child", "title": "Child run", "directory": "/work/v1",
        "agent": "explore", "model": j({"id": "qwen3"}), "version": "1.6.0",
        "cost": 0.0, "tokens_input": 0, "tokens_output": 0,
        "time_created": BASE_MS + 70000, "time_updated": BASE_MS + 80000,
        "parent_id": "ses_v1_a",
    })
    insert(conn, "session", {
        "id": "ses_v1_sum", "title": "Summary session",
        "directory": "/work/v1", "agent": "build",
        "model": j({"id": "qwen3"}), "version": "1.6.0",
        "cost": 0.0, "tokens_input": 0, "tokens_output": 0,
        "time_created": BASE_MS, "time_updated": BASE_MS + 1000,
        "parent_id": None,
    })
    insert(conn, "message", {"id": "m1", "session_id": "ses_v1_a",
                             "time_created": BASE_MS + 1000,
                             "data": j({"role": "user"})})
    insert(conn, "message", {"id": "m2", "session_id": "ses_v1_a",
                             "time_created": BASE_MS + 10000,
                             "data": j({"role": "assistant", "summary": 0,
                                        "time": {"created": BASE_MS + 10000,
                                                 "completed": BASE_MS + 40000}})})
    insert(conn, "message", {"id": "m9", "session_id": "ses_v1_sum",
                             "time_created": BASE_MS + 500,
                             "data": j({"role": "assistant", "summary": 1})})
    parts = [
        ("p1", "m1", 1100, {"type": "text", "text": "Add a unit test"}),
        ("p2", "m1", 1200, {"type": "text", "text": "synthetic context",
                            "synthetic": True}),
        ("p3", "m2", 10100, {"type": "text", "text": "I added it"}),
        ("p4", "m2", 10200, {"type": "tool", "tool": "bash", "callID": "callA",
                             "state": {"status": "completed",
                                       "input": {"command": "pytest"},
                                       "output": "1 passed"}}),
        ("p5", "m2", 10300, {"type": "reasoning", "text": "thinking hard"}),
        ("p6", "m2", 10400, {"type": "file", "source": {"path": "/src/x.py"}}),
        ("p7", "m2", 10500, {"type": "compaction", "auto": True}),
        ("p9", "m9", 600, {"type": "text", "text": "Recap"}),
    ]
    for part_id, message_id, offset, data in parts:
        session_id = "ses_v1_sum" if message_id == "m9" else "ses_v1_a"
        insert(conn, "part", {"id": part_id, "session_id": session_id,
                              "message_id": message_id,
                              "time_created": BASE_MS + offset, "data": j(data)})
    insert(conn, "todo", {"session_id": "ses_v1_a", "content": "Write tests",
                          "status": "completed", "priority": "high",
                          "position": 0})
    insert(conn, "todo", {"session_id": "ses_v1_a", "content": "Run tests",
                          "status": "in_progress", "priority": "medium",
                          "position": 1})
    conn.commit()


def build_v2(conn):
    insert(conn, "session_v2", {
        "id": "ses_v2_a", "title": "V2 session", "directory": "/work/v2",
        "agent": "build", "model": j({"id": "k2"}), "version": "2.1.0",
        "cost": 0.25, "tokens_input": 200, "tokens_output": 100,
        "time_created": BASE_MS, "time_updated": BASE_MS + 60000,
        "parent_id": None,
    })
    insert(conn, "session_v2", {
        "id": "ses_v2_child", "title": "Child run", "directory": "/work/v2",
        "agent": "explore", "model": j({"id": "k2"}), "version": "2.1.0",
        "cost": 0.0, "tokens_input": 0, "tokens_output": 0,
        "time_created": BASE_MS + 70000, "time_updated": BASE_MS + 80000,
        "parent_id": "ses_v2_a",
    })
    insert(conn, "session_v2", {
        "id": "ses_v2_b", "title": "Mixed content", "directory": "/work/other",
        "agent": "build", "model": j({"id": "k2"}), "version": "2.1.0",
        "cost": 0.0, "tokens_input": 0, "tokens_output": 0,
        "time_created": BASE_MS, "time_updated": BASE_MS + 1000,
        "parent_id": None,
    })
    messages = [
        ("m1", "ses_v2_a", "user", 1, 1000,
         {"text": "Fix the failing test", "time": {"created": BASE_MS + 1500}}),
        ("m2", "ses_v2_a", "user", 2, 2000, {"text": "Now run the suite"}),
        ("m3", "ses_v2_a", "assistant", 3, 10000,
         {"time": {"created": BASE_MS + 10000, "completed": BASE_MS + 40000},
          "content": [
             {"type": "reasoning", "text": "planning"},
             {"type": "text", "text": "I will fix it"},
             {"type": "tool", "name": "bash", "id": "call_1",
              "state": {"status": "completed", "input": {"command": "pytest"},
                        "content": "2 passed"}},
         ]}),
        ("m4", "ses_v2_a", "assistant", 4, 20000, {}),
        ("m5", "ses_v2_a", "system", 5, 30000, {"text": "system context"}),
        ("m6", "ses_v2_a", "compaction", 6, 40000,
         {"reason": "auto", "summary": "Work so far recap"}),
        ("m7", "ses_v2_a", "shell", 7, 50000,
         {"command": "ls", "output": "x", "status": "completed"}),
        ("m8", "ses_v2_b", "assistant", 1, 1000,
         {"content": [{"type": "text", "text": "inline tail"}]}),
    ]
    for message_id, session_id, msg_type, seq, offset, data in messages:
        insert(conn, "session_message", {
            "id": message_id, "session_id": session_id, "type": msg_type,
            "seq": seq, "time_created": BASE_MS + offset, "data": j(data)})
    insert(conn, "session_part", {
        "session_id": "ses_v2_a", "message_id": "m4", "ordinal": 1,
        "type": "text", "call_id": None, "data": j({"text": "Done"}),
    })
    insert(conn, "session_part", {
        "session_id": "ses_v2_a", "message_id": "m4", "ordinal": 2,
        "type": "tool", "call_id": "call_2",
        "data": j({"name": "edit",
                   "state": {"status": "error", "input": {"filePath": "a.py"},
                             "error": {"message": "boom"}}}),
    })
    insert(conn, "session_part", {
        "session_id": "ses_v2_b", "message_id": "m8", "ordinal": 1,
        "type": "text", "call_id": None, "data": j({"text": "part head"}),
    })
    conn.commit()


class OpencodeV1Tests(OpencodeDbTestCase):
    def setUp(self):
        super().setUp()
        conn = self.create_db(schemas=("v1",))
        build_v1(conn)
        conn.close()
        self.source = OpencodeV1Source()

    def test_detect_schemas_v1(self):
        self.assertEqual(detect_schemas(), ["v1"])

    def test_list_sessions_row(self):
        rows = self.source.list_sessions(None, True)
        by_id = {r.session_id: r for r in rows}
        self.assertEqual(set(by_id), {"ses_v1_a", "ses_v1_child", "ses_v1_sum"})
        row = by_id["ses_v1_a"]
        self.assertEqual(row.source, "opencode")
        self.assertEqual(row.title, "V1 session")
        self.assertEqual(row.project, "/work/v1")
        self.assertEqual(row.lines, 2)
        self.assertEqual(row.user_prompts, 1)
        self.assertEqual(row.tool_calls, 1)
        self.assertEqual(row.compacts, 1)
        self.assertEqual(row.cost, 0.5)
        self.assertEqual(row.tokens_input, 100)
        self.assertEqual(row.tokens_output, 50)
        self.assertEqual(row.duration_s, 30)
        self.assertEqual(row.last_ts, ms_to_iso(BASE_MS + 60000))
        self.assertEqual(len(self.source.list_sessions("/work/v1", False)), 3)
        self.assertEqual(self.source.list_sessions("/work/other", False), [])

    def test_find(self):
        self.assertEqual(self.source.find("ses_v1_a", None), "ses_v1_a")
        self.assertIsNone(self.source.find("missing", None))

    def test_metadata_fields(self):
        meta = self.source.metadata("ses_v1_a", "ses_v1_a")
        self.assertEqual(meta.session_id, "ses_v1_a")
        self.assertEqual(meta.title, "V1 session")
        self.assertEqual(meta.cwd, "/work/v1")
        self.assertEqual(meta.model, "qwen3")
        self.assertEqual(meta.version, "1.6.0")
        self.assertEqual(meta.agent, "build")
        self.assertEqual(meta.cost, 0.5)
        self.assertEqual(meta.tokens_input, 100)
        self.assertEqual(meta.tokens_output, 50)
        self.assertEqual(meta.duration_s, 30)
        self.assertEqual(meta.compact_count, 1)
        self.assertEqual(meta.line_count, 7)
        self.assertEqual(meta.first_ts, ms_to_iso(BASE_MS))
        self.assertEqual(meta.last_ts, ms_to_iso(BASE_MS + 60000))

    def test_user_prompts_exclude_synthetic(self):
        prompts = self.source.user_prompts("ses_v1_a")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].text, "Add a unit test")
        self.assertEqual(prompts[0].timestamp, ms_to_iso(BASE_MS + 1100))

    def test_assistant_texts(self):
        texts = self.source.assistant_texts("ses_v1_a")
        self.assertEqual([t.text for t in texts], ["I added it"])

    def test_tool_calls_fields(self):
        calls = self.source.tool_calls("ses_v1_a")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call.name, "bash")
        self.assertEqual(call.input, "{\"command\": \"pytest\"}")
        self.assertEqual(call.status, "completed")
        self.assertEqual(call.tool_id, "callA")
        self.assertEqual(call.output, "1 passed")

    def test_compaction_and_away_summaries(self):
        boundaries = self.source.compact_boundaries("ses_v1_a")
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].trigger, "auto")
        self.assertEqual(boundaries[0].timestamp, ms_to_iso(BASE_MS + 10500))
        self.assertEqual(self.source.away_summaries("ses_v1_a"), [])
        away = self.source.away_summaries("ses_v1_sum")
        self.assertEqual(len(away), 1)
        self.assertEqual(away[0].content, "Recap")

    def test_reasoning(self):
        reasoning = self.source.reasoning("ses_v1_a")
        self.assertEqual([r.text for r in reasoning], ["thinking hard"])

    def test_timeline_events(self):
        events = self.source.timeline_events("ses_v1_a")
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            EventKind.USER_MESSAGE,
            EventKind.ASSISTANT_TEXT,
            EventKind.TOOL_CALL,
            EventKind.REASONING,
            EventKind.FILE_EDIT,
            EventKind.COMPACTION,
        ])
        tool = events[2]
        self.assertEqual(tool.tool_name, "bash")
        self.assertEqual(tool.tool_status, "completed")
        self.assertEqual(tool.tool_output, "1 passed")
        file_edit = events[4]
        self.assertEqual(file_edit.text, "/src/x.py")
        compaction = events[5]
        self.assertTrue(compaction.metadata["auto"])

    def test_todos(self):
        todos = self.source.todos("ses_v1_a")
        self.assertEqual([(t.content, t.status, t.priority) for t in todos], [
            ("Write tests", "completed", "high"),
            ("Run tests", "in_progress", "medium"),
        ])

    def test_subagents_from_parent_id(self):
        subagents = self.source.subagents("ses_v1_a")
        self.assertEqual(len(subagents), 1)
        self.assertEqual(subagents[0].agent_id, "ses_v1_child")
        self.assertEqual(subagents[0].agent_type, "explore")
        self.assertEqual(subagents[0].description, "Child run")


class OpencodeV2Tests(OpencodeDbTestCase):
    def setUp(self):
        super().setUp()
        conn = self.create_db(schemas=("v2",))
        build_v2(conn)
        conn.close()
        self.source = OpencodeV2Source()

    def test_detect_schemas_v2(self):
        self.assertEqual(detect_schemas(), ["v2"])

    def test_list_sessions_row(self):
        rows = self.source.list_sessions(None, True)
        by_id = {r.session_id: r for r in rows}
        self.assertEqual(set(by_id), {"ses_v2_a", "ses_v2_child", "ses_v2_b"})
        row = by_id["ses_v2_a"]
        self.assertEqual(row.title, "V2 session")
        self.assertEqual(row.project, "/work/v2")
        self.assertEqual(row.lines, 7)
        self.assertEqual(row.user_prompts, 2)
        self.assertEqual(row.tool_calls, 2)
        self.assertEqual(row.compacts, 1)
        self.assertEqual(len(self.source.list_sessions("/work/v2", False)), 2)
        self.assertEqual(len(self.source.list_sessions("/work/other", False)), 1)

    def test_find(self):
        self.assertEqual(self.source.find("ses_v2_a", None), "ses_v2_a")
        self.assertIsNone(self.source.find("missing", None))

    def test_metadata_fields(self):
        meta = self.source.metadata("ses_v2_a", "ses_v2_a")
        self.assertEqual(meta.session_id, "ses_v2_a")
        self.assertEqual(meta.model, "k2")
        self.assertEqual(meta.version, "2.1.0")
        self.assertEqual(meta.agent, "build")
        self.assertEqual(meta.cost, 0.25)
        self.assertEqual(meta.tokens_input, 200)
        self.assertEqual(meta.tokens_output, 100)
        self.assertEqual(meta.duration_s, 30)
        self.assertEqual(meta.compact_count, 1)
        self.assertEqual(meta.line_count, 7)
        self.assertEqual(meta.cwd, "/work/v2")

    def test_user_prompts_and_timestamps(self):
        prompts = self.source.user_prompts("ses_v2_a")
        self.assertEqual([p.text for p in prompts],
                         ["Fix the failing test", "Now run the suite"])
        self.assertEqual(prompts[0].timestamp, ms_to_iso(BASE_MS + 1500))
        self.assertEqual(prompts[1].timestamp, ms_to_iso(BASE_MS + 2000))

    def test_assistant_texts_inline_parts_and_mixed(self):
        self.assertEqual([t.text for t in self.source.assistant_texts("ses_v2_a")],
                         ["I will fix it", "Done"])
        self.assertEqual([t.text for t in self.source.assistant_texts("ses_v2_b")],
                         ["part head", "inline tail"])

    def test_tool_calls_inline_and_parts(self):
        calls = self.source.tool_calls("ses_v2_a")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].name, "bash")
        self.assertEqual(calls[0].tool_id, "call_1")
        self.assertEqual(calls[0].status, "completed")
        self.assertEqual(calls[0].output, "2 passed")
        self.assertEqual(calls[1].name, "edit")
        self.assertEqual(calls[1].tool_id, "call_2")
        self.assertEqual(calls[1].status, "error")
        self.assertEqual(calls[1].input, "{\"filePath\": \"a.py\"}")
        self.assertEqual(calls[1].output, "boom")

    def test_compaction_and_away_summaries(self):
        boundaries = self.source.compact_boundaries("ses_v2_a")
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].trigger, "auto")
        away = self.source.away_summaries("ses_v2_a")
        self.assertEqual(len(away), 1)
        self.assertEqual(away[0].content, "Work so far recap")

    def test_reasoning(self):
        reasoning = self.source.reasoning("ses_v2_a")
        self.assertEqual([r.text for r in reasoning], ["planning"])

    def test_system_message_excluded(self):
        prompts = self.source.user_prompts("ses_v2_a")
        self.assertNotIn("system context", [p.text for p in prompts])
        events = self.source.timeline_events("ses_v2_a")
        self.assertNotIn("system context", [e.text for e in events])

    def test_timeline_events(self):
        events = self.source.timeline_events("ses_v2_a")
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            EventKind.USER_MESSAGE,
            EventKind.USER_MESSAGE,
            EventKind.REASONING,
            EventKind.ASSISTANT_TEXT,
            EventKind.TOOL_CALL,
            EventKind.ASSISTANT_TEXT,
            EventKind.TOOL_CALL,
            EventKind.COMPACTION,
            EventKind.TOOL_CALL,
        ])
        shell = events[-1]
        self.assertEqual(shell.tool_name, "shell")
        self.assertEqual(shell.tool_input, "ls")
        self.assertEqual(shell.tool_output, "x")
        self.assertEqual(shell.tool_status, "completed")
        compaction = events[7]
        self.assertTrue(compaction.metadata["auto"])

    def test_subagents_from_parent_id(self):
        subagents = self.source.subagents("ses_v2_a")
        self.assertEqual(len(subagents), 1)
        self.assertEqual(subagents[0].agent_id, "ses_v2_child")

    def test_todos_empty(self):
        self.assertEqual(self.source.todos("ses_v2_a"), [])


class OpencodeUnionTests(OpencodeDbTestCase):
    def setUp(self):
        super().setUp()
        conn = self.create_db(schemas=("v1", "v2"))
        build_v1(conn)
        build_v2(conn)
        conn.close()
        self.source = OpencodeSource()

    def test_detect_schemas_both(self):
        self.assertEqual(detect_schemas(), ["v1", "v2"])

    def test_find_across_schemas(self):
        self.assertEqual(self.source.find("ses_v1_a", None),
                         OpencodeHandle("v1", "ses_v1_a"))
        self.assertEqual(self.source.find("ses_v2_a", None),
                         OpencodeHandle("v2", "ses_v2_a"))
        self.assertIsNone(self.source.find("missing", None))

    def test_union_reads_dispatch_to_owning_schema(self):
        handle = self.source.find("ses_v1_a", None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual([p.text for p in prompts], ["Add a unit test"])
        handle = self.source.find("ses_v2_a", None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual([p.text for p in prompts],
                         ["Fix the failing test", "Now run the suite"])

    def test_todos_fall_through_to_v1(self):
        todos = self.source.todos("ses_v1_a")
        self.assertEqual(len(todos), 2)
        self.assertEqual(todos[0].content, "Write tests")


class OpencodeMigratedDedupTests(OpencodeDbTestCase):
    def setUp(self):
        super().setUp()
        conn = self.create_db(schemas=("v1", "v2"))
        for table, sid, updated in (
            ("session", "ses_dup", 1000),
            ("session", "ses_dup2", 90000),
            ("session", "ses_v1_only", 5000),
            ("session_v2", "ses_dup", 20000),
            ("session_v2", "ses_dup2", 80000),
            ("session_v2", "ses_v2_only", 30000),
        ):
            insert(conn, table, {
                "id": sid, "title": f"row {sid}", "directory": "/work/m",
                "agent": "build", "model": j({"id": "k2"}),
                "version": "1.0.0", "cost": 0.0, "tokens_input": 0,
                "tokens_output": 0, "time_created": 0,
                "time_updated": updated, "parent_id": None,
            })
        insert(conn, "todo", {"session_id": "ses_v1_only", "content": "todo",
                              "status": "pending", "priority": "low",
                              "position": 0})
        conn.commit()
        conn.close()
        self.source = OpencodeSource()

    def test_list_dedupes_by_newest_time_updated(self):
        rows = self.source.list_sessions(None, True)
        by_id = {}
        for row in rows:
            by_id.setdefault(row.session_id, []).append(row)
        self.assertEqual(set(by_id), {"ses_dup", "ses_dup2", "ses_v1_only",
                                      "ses_v2_only"})
        self.assertEqual(len(by_id["ses_dup"]), 1)
        self.assertEqual(by_id["ses_dup"][0].last_ts, ms_to_iso(20000))
        self.assertEqual(by_id["ses_dup2"][0].last_ts, ms_to_iso(90000))

    def test_find_prefers_newest_schema(self):
        self.assertEqual(self.source.find("ses_dup", None),
                         OpencodeHandle("v2", "ses_dup"))
        self.assertEqual(self.source.find("ses_dup2", None),
                         OpencodeHandle("v1", "ses_dup2"))
        self.assertEqual(self.source.find("ses_v1_only", None),
                         OpencodeHandle("v1", "ses_v1_only"))
        self.assertEqual(self.source.find("ses_v2_only", None),
                         OpencodeHandle("v2", "ses_v2_only"))

    def test_union_todos_from_v1(self):
        todos = self.source.todos("ses_v1_only")
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0].content, "todo")


class OpencodeMissingDbTests(OpencodeDbTestCase):
    def setUp(self):
        super().setUp()
        self.source = OpencodeSource()

    def test_detect_schemas_empty_when_db_missing(self):
        self.assertEqual(detect_schemas(), [])

    def test_find_returns_none_when_db_missing(self):
        self.assertIsNone(self.source.find("anything", None))

    def test_list_raises_when_db_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.source.list_sessions(None, True)

    def test_empty_database_lists_no_sessions(self):
        conn = sqlite3.connect(self.db_path)
        conn.close()
        self.assertEqual(detect_schemas(), [])
        self.assertEqual(self.source.list_sessions(None, True), [])

    def test_ms_to_iso(self):
        self.assertEqual(ms_to_iso(BASE_MS), "2024-11-12T00:00:00Z")
        self.assertEqual(ms_to_iso(0), "")
        self.assertEqual(ms_to_iso(None), "")


if __name__ == "__main__":
    unittest.main()