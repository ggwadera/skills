import json
import os
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from session_fixtures import (
    BASE_MS,
    DSH_CHILD_ID,
    DSH_CWD,
    DSH_SESSION_ID,
    DshHomeTestCase,
    build_dsh_fixture,
    dsh_header_events,
    zstd_available,
)
from sources.base import EventKind
from sources.dsh import DshSource, _ms_to_iso
from sources import detect_source


class DshSourceTestCase(DshHomeTestCase):
    def setUp(self):
        super().setUp()
        self.source = DshSource()
        self.session_id = build_dsh_fixture(self.dsh_home)

    def handle(self, session_id=None, dir_path=None):
        found = self.source.find(session_id or self.session_id, dir_path)
        self.assertIsNotNone(found, "fixture session not found")
        return found

    # -- discovery -------------------------------------------------------

    def test_list_sessions_row(self):
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.source, "dsh")
        self.assertEqual(row.session_id, DSH_SESSION_ID)
        self.assertEqual(row.project, DSH_CWD)
        self.assertEqual(row.title, "Fix flaky test")
        self.assertEqual(row.lines, 27)
        self.assertEqual(row.user_prompts, 2)
        self.assertEqual(row.tool_calls, 3)
        self.assertEqual(row.assistant_texts, 3)
        self.assertEqual(row.compacts, 1)
        self.assertEqual(row.tokens_input, 9096)
        self.assertEqual(row.tokens_output, 765)
        self.assertEqual(row.duration_s, 4)
        self.assertEqual(row.last_ts, _ms_to_iso(BASE_MS + 5100))
        self.assertGreater(row.file_size, 0)

    def test_list_dir_filter(self):
        rows = self.source.list_sessions(DSH_CWD, all_dirs=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.source.list_sessions("/other/dir", all_dirs=False), [])

    def test_list_limit(self):
        self.assertEqual(self.source.list_sessions(None, True, limit=0), [])
        self.assertEqual(len(self.source.list_sessions(None, True, limit=1)), 1)

    def test_find_and_dir_filter(self):
        path = self.handle()
        self.assertTrue(path.name.startswith("session.v4.jsonl"))
        self.assertIsNone(self.source.find(DSH_SESSION_ID, "/other/dir"))
        self.assertIsNone(self.source.find("session-missing", None))

    def test_find_ignores_lock_and_tmp_files(self):
        session_dir = self.handle().parent
        (session_dir / "session.lock").write_text("", encoding="utf-8")
        (session_dir / "session.v4.jsonl.zstd.abc123.tmp").write_bytes(b"junk")
        found = self.source.find(DSH_SESSION_ID, None)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "session.v4.jsonl")

    def test_newest_generation_wins(self):
        session_dir = self.handle().parent
        old_header, _old_events = dsh_header_events()
        old_header = dict(old_header, version=3, id=DSH_SESSION_ID)
        (session_dir / "session.v3.jsonl").write_text(
            json.dumps(old_header) + "\n" +
            json.dumps({"type": "session/title", "seq": 0,
                        "time": BASE_MS, "data": {"title": "OLD"}}) + "\n",
            encoding="utf-8",
        )
        rows = self.source.list_sessions(None, True)
        self.assertEqual(rows[0].title, "Fix flaky test")

    def test_missing_home_is_empty_not_an_error(self):
        os.environ["DSH_HOME"] = str(self.root / "does-not-exist")
        self.assertEqual(self.source.list_sessions(None, True), [])
        self.assertIsNone(self.source.find(DSH_SESSION_ID, None))

    def test_autodetect_resolves_dsh(self):
        resolved = detect_source(DSH_SESSION_ID)
        self.assertIsNotNone(resolved)
        adapter, _handle = resolved
        self.assertEqual(adapter.name, "dsh")

    # -- metadata --------------------------------------------------------

    def test_metadata(self):
        meta = self.source.metadata(self.handle(), DSH_SESSION_ID)
        self.assertEqual(meta.source, "dsh")
        self.assertEqual(meta.session_id, DSH_SESSION_ID)
        self.assertEqual(meta.title, "Fix flaky test")
        self.assertEqual(meta.cwd, DSH_CWD)
        self.assertEqual(meta.model, "deepseek-flash")
        self.assertEqual(meta.agent, "standard")
        self.assertEqual(meta.line_count, 27)
        self.assertEqual(meta.first_ts, _ms_to_iso(BASE_MS))
        self.assertEqual(meta.last_ts, _ms_to_iso(BASE_MS + 5100))
        self.assertEqual(meta.compact_count, 1)
        self.assertEqual(meta.tokens_input, 9096)
        self.assertEqual(meta.tokens_output, 765)
        self.assertEqual(meta.duration_s, 4)
        self.assertFalse(meta.is_subagent)

    # -- extraction ------------------------------------------------------

    def test_user_prompts_filter_and_edit(self):
        prompts = self.source.user_prompts(self.handle())
        self.assertEqual(
            [p.text for p in prompts],
            ["Fix the flaky test", "Add a retry with backoff"],
        )

    def test_assistant_texts_and_reasoning(self):
        texts = self.source.assistant_texts(self.handle())
        self.assertEqual(
            [t.text for t in texts],
            ["Running the tests.", "Applying the fix.", "Adding retry logic."],
        )
        reasoning = self.source.reasoning(self.handle())
        self.assertEqual([r.text for r in reasoning], ["checking logs"])

    def test_tool_calls_outputs_and_statuses(self):
        calls = self.source.tool_calls(self.handle())
        self.assertEqual([c.name for c in calls], ["bash", "edit", "bash"])
        self.assertEqual(calls[0].output, "3 passed")
        self.assertEqual(calls[0].status, "completed")
        # the refreshed tool/result supersedes the original output
        self.assertEqual(calls[1].output, "patched (updated)")
        self.assertEqual(calls[1].status, "completed")
        self.assertEqual(calls[2].output, "command not found")
        self.assertEqual(calls[2].status, "error")

    def test_compact_boundaries(self):
        boundaries = self.source.compact_boundaries(self.handle())
        self.assertEqual(len(boundaries), 1)
        b = boundaries[0]
        self.assertEqual(b.trigger, "auto")
        self.assertEqual(b.pre_tokens, 1000)
        self.assertEqual(b.post_tokens, 500)
        self.assertEqual(b.dropped_tokens, 500)
        self.assertEqual(b.duration_ms, 200)

    def test_away_summaries(self):
        away = self.source.away_summaries(self.handle())
        self.assertEqual([s.content for s in away], ["Recap of earlier work"])

    def test_todos(self):
        todos = self.source.todos(DSH_SESSION_ID)
        self.assertEqual(
            [(t.content, t.status) for t in todos],
            [("Fix flaky test", "in_progress"), ("Ship the fix", "pending")],
        )

    def test_subagents_from_catalog_and_child_scan(self):
        # The catalog child has its own session dir: same ID dedupes to one
        # entry that keeps the catalog metadata.
        catalog_child_header = {
            "type": "session", "version": 4, "id": DSH_CHILD_ID,
            "createdAt": BASE_MS, "cwd": DSH_CWD, "isSeeded": True,
            "delegationDepth": 1, "origin": "subagent",
            "parentSession": DSH_SESSION_ID, "agentPreset": "standard",
        }
        build_dsh_fixture(self.dsh_home, session_id=DSH_CHILD_ID,
                          lines=[json.dumps(catalog_child_header)])
        # A second child known only from its header (no catalog entry).
        scanned_id = "session-99999999-8888-7777-6666-555555555555"
        scanned_header = dict(catalog_child_header, id=scanned_id)
        build_dsh_fixture(self.dsh_home, session_id=scanned_id,
                          lines=[json.dumps(scanned_header)])

        infos = self.source.subagents(self.handle())
        self.assertEqual(len(infos), 2)
        by_id = {info.agent_id: info for info in infos}
        cataloged = by_id[DSH_CHILD_ID]
        self.assertEqual(cataloged.nickname, "explorer")
        self.assertEqual(cataloged.agent_type, "continuable")
        self.assertEqual(cataloged.description, "Explore codebase")
        self.assertEqual(by_id[scanned_id].agent_id, scanned_id)
        # the scanned child itself reports as a subagent
        child_meta = self.source.metadata(self.handle(scanned_id), scanned_id)
        self.assertTrue(child_meta.is_subagent)

    def test_tool_result_source_call_id_fallback(self):
        # tool/result events may carry the call ID under message.source.callId
        session_id = "session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        t0 = BASE_MS
        header = {"type": "session", "version": 4, "id": session_id,
                  "createdAt": t0, "cwd": DSH_CWD}
        lines = [
            json.dumps(header),
            json.dumps({"type": "tool/call", "seq": 1, "time": t0 + 100,
                        "data": {"name": "bash", "callId": "call-1",
                                 "arguments": "ls"}}),
            json.dumps({"type": "tool/result", "seq": 2, "time": t0 + 200,
                        "data": {"message": {
                            "source": {"callId": "call-1"},
                            "content": [{"type": "text", "text": "ok"}]}}}),
        ]
        build_dsh_fixture(self.dsh_home, session_id=session_id, lines=lines)
        handle = self.source.find(session_id, None)
        self.assertIsNotNone(handle)
        calls = self.source.tool_calls(handle)
        self.assertEqual(calls[0].tool_id, "call-1")
        self.assertEqual(calls[0].output, "ok")
        self.assertEqual(calls[0].status, "completed")
        events = self.source.timeline_events(handle)
        results = [ev for ev in events if ev.kind == EventKind.TOOL_RESULT]
        self.assertEqual(results[0].tool_id, "call-1")

    def test_grep_matches(self):
        self.assertTrue(self.source.matches(DSH_SESSION_ID, None, "flaky"))
        self.assertTrue(self.source.matches(DSH_SESSION_ID, None, "backoff"))
        self.assertFalse(self.source.matches(DSH_SESSION_ID, None, "no-such"))

    # -- timeline --------------------------------------------------------

    def test_timeline_kinds(self):
        events = self.source.timeline_events(self.handle())
        kinds = [ev.kind for ev in events]
        self.assertEqual(kinds[0], EventKind.SESSION_META)
        self.assertEqual(kinds.count(EventKind.USER_MESSAGE), 2)
        self.assertEqual(kinds.count(EventKind.ASSISTANT_TEXT), 3)
        self.assertEqual(kinds.count(EventKind.REASONING), 1)
        self.assertEqual(kinds.count(EventKind.TOOL_CALL), 3)
        self.assertEqual(kinds.count(EventKind.TOOL_RESULT), 3)
        self.assertEqual(kinds.count(EventKind.COMPACT_BOUNDARY), 1)
        self.assertEqual(kinds.count(EventKind.AWAY_SUMMARY), 1)
        self.assertEqual(kinds.count(EventKind.AI_TITLE), 1)
        self.assertEqual(kinds.count(EventKind.TURN_DURATION), 2)
        self.assertEqual(kinds.count(EventKind.TURN_CONTEXT), 1)
        texts = [ev.text for ev in events]
        self.assertIn("Fix the flaky test", texts)
        self.assertIn("Add a retry with backoff", texts)
        self.assertNotIn("<hook>context injection</hook>", texts)
        self.assertIn("patched (updated)", texts)
        self.assertNotIn("patched", texts)

    def test_timeline_tool_call_status(self):
        events = self.source.timeline_events(self.handle())
        calls = [ev for ev in events if ev.kind == EventKind.TOOL_CALL]
        self.assertEqual(calls[0].tool_status, "completed")
        self.assertEqual(calls[2].tool_status, "error")

    def test_timeline_paging_helpers(self):
        events = self.source.timeline_events(self.handle())
        tail = events[-1:]
        self.assertEqual(tail[0].kind, EventKind.TURN_DURATION)
        self.assertEqual(tail[0].metadata["duration_ms"], 3100)


class DshZstdTestCase(DshHomeTestCase):
    def setUp(self):
        super().setUp()
        if not zstd_available():
            self.skipTest("no zstd backend available")
        self.source = DshSource()
        self.session_id = build_dsh_fixture(self.dsh_home, compressed=True)

    def test_compressed_session_reads_identically(self):
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, DSH_SESSION_ID)
        self.assertEqual(rows[0].user_prompts, 2)
        handle = self.source.find(self.session_id, None)
        self.assertIsNotNone(handle)
        self.assertTrue(handle.name.endswith(".jsonl.zstd"))
        prompts = self.source.user_prompts(handle)
        self.assertEqual(prompts[0].text, "Fix the flaky test")
        meta = self.source.metadata(handle, self.session_id)
        self.assertEqual(meta.model, "deepseek-flash")
        self.assertEqual(meta.tokens_input, 9096)


if __name__ == "__main__":
    unittest.main()
