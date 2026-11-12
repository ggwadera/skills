import os
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import sources.codex as codex_module
from session_fixtures import TempDirTestCase, write_jsonl
from sources.base import EventKind
from sources.codex import CodexSource

UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ARCHIVED_UUID = "99999999-8888-7777-6666-555555555555"
DUP_UUID = "12345678-abcd-ef01-2345-6789abcdef01"
SUBAGENT_UUID = "22222222-3333-4444-5555-666666666666"


def main_lines():
    return [
        {"type": "session_meta", "timestamp": "2026-11-12T10:00:00.000Z",
         "payload": {"id": "s-codex-1", "cwd": "/work/project",
                     "cli_version": "0.45.0", "source": "vscode",
                     "model_provider": "openai",
                     "git": {"branch": "feature/fix"}}},
        {"type": "event_msg", "timestamp": "2026-11-12T10:00:05.000Z",
         "payload": {"type": "item_completed",
                     "item": {"type": "UserMessage",
                              "content": [{"type": "input_text",
                                           "text": "Fix the flaky test"}]}}},
        {"type": "event_msg", "timestamp": "2026-11-12T10:00:06.000Z",
         "payload": {"type": "item_completed",
                     "item": {"type": "UserMessage",
                              "content": [{"type": "input_text",
                                           "text": "<environment_context># env"
                                                   "</environment_context>"}]}}},
        {"type": "event_msg", "timestamp": "2026-11-12T10:00:06.500Z",
         "payload": {"type": "task_started", "turn_id": "turn_abc",
                     "model_context_window": 272000}},
        {"type": "turn_context", "timestamp": "2026-11-12T10:00:07.000Z",
         "payload": {"model": "gpt-5.2-codex", "cwd": "/work/project"}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:10.000Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "Running the tests."}]}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:11.000Z",
         "payload": {"type": "reasoning",
                     "summary": [{"type": "summary_text",
                                  "text": "checking logs"}]}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:12.000Z",
         "payload": {"type": "function_call", "namespace": "shell",
                     "name": "bash",
                     "arguments": "{\"command\": \"pytest\"}",
                     "call_id": "fc1", "status": "completed"}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:13.000Z",
         "payload": {"type": "function_call_output", "call_id": "fc1",
                     "output": "3 passed"}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:14.000Z",
         "payload": {"type": "custom_tool_call", "name": "apply_patch",
                     "input": "*** Begin Patch", "call_id": "ct1",
                     "status": "completed"}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:15.000Z",
         "payload": {"type": "custom_tool_call_output", "call_id": "ct1",
                     "output": "patched"}},
        {"type": "response_item", "timestamp": "2026-11-12T10:00:16.000Z",
         "payload": {"type": "local_shell_call",
                     "action": {"command": ["ls"]}, "call_id": "ls1",
                     "status": "completed"}},
        {"type": "compacted", "timestamp": "2026-11-12T10:00:20.000Z",
         "payload": {"message": "Summary of earlier work"}},
        {"type": "world_state", "timestamp": "2026-11-12T10:00:21.000Z",
         "payload": {"state": {"environments": {
             "subagents": "- agent_1: reviewer\n<agent name=\"explorer\"/>"}}}},
        {"type": "event_msg", "timestamp": "2026-11-12T10:00:22.000Z",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": {
                         "total_tokens": 120, "input_tokens": 80,
                         "output_tokens": 40}}}},
    ]


class CodexSourceTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.sessions_dir = self.root / "sessions"
        self.archived_dir = self.root / "archived_sessions"
        self.index_file = self.root / "session_index.jsonl"
        self.sessions_dir.mkdir()
        self.archived_dir.mkdir()
        for attr, value in (("CODEX_SESSIONS_DIR", self.sessions_dir),
                            ("CODEX_ARCHIVED_DIR", self.archived_dir),
                            ("CODEX_INDEX_FILE", self.index_file)):
            old = getattr(codex_module, attr)
            setattr(codex_module, attr, value)
            self.addCleanup(setattr, codex_module, attr, old)
        codex_module._LINE_CACHE.clear()
        self.source = CodexSource()

    def add_rollout(self, path, lines):
        return write_jsonl(path, lines)

    def add_main_session(self):
        self.add_rollout(
            self.sessions_dir / "2026" / "11" / "12" / f"rollout-2026-11-12T10-00-00-{UUID}.jsonl",
            main_lines())
        write_jsonl(self.index_file,
                    [{"id": UUID, "thread_name": "Fix flaky tests"}])

    def test_list_sessions_row(self):
        self.add_main_session()
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.source, "codex")
        self.assertEqual(row.session_id, UUID)
        self.assertEqual(row.project, "/work/project")
        self.assertEqual(row.title, "Fix flaky tests")
        self.assertEqual(row.lines, 15)
        self.assertEqual(row.user_prompts, 1)
        self.assertEqual(row.tool_calls, 3)
        self.assertEqual(row.compacts, 1)

    def test_list_sessions_dir_filter(self):
        self.add_main_session()
        self.assertEqual(len(self.source.list_sessions("/work/project", False)), 1)
        self.assertEqual(len(self.source.list_sessions("/work/other", False)), 0)

    def test_find_by_thread_id(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        self.assertIsNotNone(handle)
        self.assertIn(UUID, handle.name)
        self.assertIsNotNone(self.source.find(UUID, "/work/project"))
        self.assertIsNone(self.source.find(UUID, "/work/other"))
        self.assertIsNone(self.source.find("missing-id", None))

    def test_metadata_fields(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        meta = self.source.metadata(handle, UUID)
        self.assertEqual(meta.source, "codex")
        self.assertEqual(meta.session_id, "s-codex-1")
        self.assertEqual(meta.title, "Fix flaky tests")
        self.assertEqual(meta.cwd, "/work/project")
        self.assertEqual(meta.model, "gpt-5.2-codex")
        self.assertEqual(meta.version, "0.45.0")
        self.assertEqual(meta.git_branch, "feature/fix")
        self.assertEqual(meta.source_label, "vscode")
        self.assertEqual(meta.compact_count, 1)
        self.assertEqual(meta.first_ts, "2026-11-12T10:00:00.000Z")
        self.assertEqual(meta.last_ts, "2026-11-12T10:00:22.000Z")
        self.assertFalse(meta.is_subagent)

    def test_user_prompts_exclude_injected_fragments(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].text, "Fix the flaky test")
        self.assertEqual(prompts[0].timestamp, "2026-11-12T10:00:05.000Z")

    def test_legacy_user_message_fallback_and_marker_strip(self):
        self.add_rollout(
            self.sessions_dir / f"rollout-2026-11-12T11-00-00-{DUP_UUID}.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-11-12T11:00:00.000Z",
                 "payload": {"id": "s-legacy", "cwd": "/work/legacy"}},
                {"type": "event_msg", "timestamp": "2026-11-12T11:00:01.000Z",
                 "payload": {"type": "user_message",
                             "message": "## My request for Codex: please review"
                                        " the diff"}},
            ])
        handle = self.source.find(DUP_UUID, None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].text, "please review the diff")

    def test_provenance_fallback_prompts(self):
        self.add_rollout(
            self.sessions_dir / f"rollout-2026-11-12T12-00-00-{SUBAGENT_UUID}.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-11-12T12:00:00.000Z",
                 "payload": {"id": "s-prov", "cwd": "/work/prov"}},
                {"type": "response_item",
                 "timestamp": "2026-11-12T12:00:01.000Z",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text",
                                          "text": "Provenance prompt"}],
                             "internal_chat_message_metadata_passthrough": {
                                 "content_item_kinds": ["user.text"]}}},
                {"type": "response_item",
                 "timestamp": "2026-11-12T12:00:02.000Z",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text",
                                          "text": "<environment_context>"
                                                  "injected</environment_context>"}],
                             "internal_chat_message_metadata_passthrough": {
                                 "content_item_kinds": ["user.text"]}}},
            ])
        handle = self.source.find(SUBAGENT_UUID, None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual([p.text for p in prompts], ["Provenance prompt"])

    def test_approval_transcript_prompts_excluded(self):
        self.add_rollout(
            self.sessions_dir / f"rollout-2026-11-12T13-00-00-{ARCHIVED_UUID}.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-11-12T13:00:00.000Z",
                 "payload": {"id": "s-approval", "cwd": "/work/approval"}},
                {"type": "event_msg", "timestamp": "2026-11-12T13:00:01.000Z",
                 "payload": {"type": "user_message",
                             "message": "The following is the Codex agent history"
                                        " for review"}},
            ])
        handle = self.source.find(ARCHIVED_UUID, None)
        self.assertEqual(self.source.user_prompts(handle), [])

    def test_assistant_texts_and_reasoning(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        texts = self.source.assistant_texts(handle)
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].text, "Running the tests.")
        reasoning = self.source.reasoning(handle)
        self.assertEqual(len(reasoning), 1)
        self.assertEqual(reasoning[0].text, "checking logs")

    def test_tool_calls_names_outputs_and_ids(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        calls = self.source.tool_calls(handle)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].name, "shell.bash")
        self.assertEqual(calls[0].input, "{\"command\": \"pytest\"}")
        self.assertEqual(calls[0].output, "3 passed")
        self.assertEqual(calls[0].tool_id, "fc1")
        self.assertEqual(calls[1].name, "apply_patch")
        self.assertEqual(calls[1].input, "*** Begin Patch")
        self.assertEqual(calls[1].output, "patched")
        self.assertEqual(calls[1].tool_id, "ct1")
        self.assertEqual(calls[2].name, "local_shell")
        self.assertEqual(calls[2].input, "{\"command\": [\"ls\"]}")
        self.assertEqual(calls[2].output, "")
        self.assertEqual(calls[2].tool_id, "ls1")

    def test_compaction_boundaries_and_away_summaries(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        boundaries = self.source.compact_boundaries(handle)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].timestamp, "2026-11-12T10:00:20.000Z")
        self.assertEqual(boundaries[0].trigger, "compacted")
        away = self.source.away_summaries(handle)
        self.assertEqual(len(away), 1)
        self.assertEqual(away[0].content, "Summary of earlier work")

    def test_subagents_legacy_and_tag_formats(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        subagents = self.source.subagents(handle)
        self.assertEqual(len(subagents), 2)
        self.assertEqual(subagents[0].agent_id, "agent_1")
        self.assertEqual(subagents[0].nickname, "reviewer")
        self.assertEqual(subagents[1].agent_id, "explorer")
        self.assertEqual(subagents[1].nickname, "explorer")

    def test_timeline_event_kinds_in_order(self):
        self.add_main_session()
        handle = self.source.find(UUID, None)
        events = self.source.timeline_events(handle)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            EventKind.SESSION_META,
            EventKind.USER_MESSAGE,
            EventKind.TASK_STARTED,
            EventKind.TURN_CONTEXT,
            EventKind.ASSISTANT_TEXT,
            EventKind.REASONING,
            EventKind.TOOL_CALL,
            EventKind.TOOL_RESULT,
            EventKind.TOOL_CALL,
            EventKind.TOOL_RESULT,
            EventKind.TOOL_CALL,
            EventKind.COMPACT_BOUNDARY,
            EventKind.WORLD_STATE,
            EventKind.TOKEN_COUNT,
        ])
        task = events[2]
        self.assertEqual(task.metadata["turn_id"], "turn_abc")
        self.assertEqual(task.metadata["model_ctx"], 272000)
        turn_context = events[3]
        self.assertEqual(turn_context.text, "/work/project")
        session_meta = events[0]
        self.assertEqual(session_meta.metadata["session_id"], "s-codex-1")
        self.assertEqual(session_meta.metadata["cwd"], "/work/project")
        self.assertEqual(session_meta.metadata["version"], "0.45.0")
        boundary = events[11]
        self.assertEqual(boundary.metadata["summary_chars"],
                         len("Summary of earlier work"))
        token_count = events[13]
        self.assertEqual(token_count.metadata["total_tokens"], 120)
        self.assertEqual(token_count.metadata["input_tokens"], 80)
        self.assertEqual(token_count.metadata["output_tokens"], 40)

    def test_archived_sessions_listed_and_found(self):
        self.add_rollout(
            self.archived_dir / f"rollout-2026-10-01T09-00-00-{ARCHIVED_UUID}.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-10-01T09:00:00.000Z",
                 "payload": {"id": "s-archived", "cwd": "/work/archived"}},
                {"type": "event_msg", "timestamp": "2026-10-01T09:00:01.000Z",
                 "payload": {"type": "user_message",
                             "message": "Archived prompt"}},
            ])
        write_jsonl(self.index_file,
                    [{"id": ARCHIVED_UUID, "thread_name": "Archived work"}])
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, ARCHIVED_UUID)
        self.assertEqual(rows[0].title, "Archived work")
        handle = self.source.find(ARCHIVED_UUID, None)
        self.assertIsNotNone(handle)
        self.assertEqual(handle.parent, self.archived_dir)

    def test_reverted_rollouts_collapse_in_list(self):
        def dup_lines(extra):
            lines = [{"type": "session_meta",
                      "timestamp": "2026-11-12T09:00:00.000Z",
                      "payload": {"id": "s-dup", "cwd": "/work/dup"}}]
            for i in range(extra):
                lines.append({"type": "event_msg",
                              "timestamp": "2026-11-12T09:00:01.000Z",
                              "payload": {"type": "token_count",
                                          "info": {"total_token_usage": {
                                              "total_tokens": i}}}})
            return lines

        short = self.add_rollout(
            self.sessions_dir / "2026" / "11" / "12" / f"rollout-2026-11-12T09-00-00-{DUP_UUID}.jsonl",
            dup_lines(2))
        long = self.add_rollout(
            self.sessions_dir / "2026" / "11" / "12" / f"rollout-2026-11-12T11-00-00-{DUP_UUID}.jsonl",
            dup_lines(5))
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, DUP_UUID)
        self.assertEqual(rows[0].lines, 6)
        self.assertEqual(rows[0].user_prompts, 0)

        now = 1_000_000_000
        os.utime(short, (now - 2000, now - 2000))
        os.utime(long, (now - 1000, now - 1000))
        self.assertEqual(self.source.find(DUP_UUID, None), long)
        os.utime(short, (now, now))
        self.assertEqual(self.source.find(DUP_UUID, None), short)

    def test_subagent_rollout_flag_and_title(self):
        self.add_rollout(
            self.sessions_dir / "2026" / "11" / "12" / f"rollout-2026-11-12T14-00-00-{SUBAGENT_UUID}.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-11-12T14:00:00.000Z",
                 "payload": {"id": "s-sub", "cwd": "/work",
                             "source": {"subagent": {"agent_id": "x"}},
                             "agent_nickname": "reviewer"}},
            ])
        handle = self.source.find(SUBAGENT_UUID, None)
        meta = self.source.metadata(handle, SUBAGENT_UUID)
        self.assertTrue(meta.is_subagent)
        self.assertEqual(meta.nickname, "reviewer")
        self.assertEqual(meta.title, "subagent reviewer")
        rows = self.source.list_sessions(None, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "subagent reviewer")


class CodexUsageTests(unittest.TestCase):
    def test_usage_prefers_thread_record_and_sums_turn_durations(self):
        from sources.codex import _usage_from_lines
        lines = [
            {"type": "event_msg", "payload": {"type": "token_count",
             "info": {"total_token_usage": {"input_tokens": 10,
                                            "output_tokens": 1}}}},
            {"type": "token_usage_record", "payload": {
                "thread_token_usage": {"input_tokens": 900, "output_tokens": 90}}},
            {"type": "event_msg", "payload": {"type": "task_complete",
                                              "duration_ms": 4000}},
            {"type": "event_msg", "payload": {"type": "turn_aborted",
                                              "duration_ms": 1000}},
        ]
        tokens_input, tokens_output, active_ms = _usage_from_lines(lines)
        self.assertEqual(tokens_input, 900)
        self.assertEqual(tokens_output, 90)
        self.assertEqual(active_ms, 5000)

    def test_usage_falls_back_to_token_count(self):
        from sources.codex import _usage_from_lines
        lines = [
            {"type": "event_msg", "payload": {"type": "token_count",
             "info": {"total_token_usage": {"input_tokens": 10,
                                            "output_tokens": 2}}}},
        ]
        tokens_input, tokens_output, active_ms = _usage_from_lines(lines)
        self.assertEqual((tokens_input, tokens_output, active_ms), (10, 2, 0))


if __name__ == "__main__":
    unittest.main()