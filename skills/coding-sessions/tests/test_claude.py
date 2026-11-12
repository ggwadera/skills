import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import sources.claude as claude_module
from session_fixtures import TempDirTestCase, write_jsonl
from sources.base import EventKind
from sources.claude import ClaudeSource, _encode_cwd

CLAUDE_CWD = "/Users/me/project"


def main_lines():
    return [
        {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
         "sessionId": "sess-claude-1", "cwd": CLAUDE_CWD,
         "version": "1.0.99", "gitBranch": "main",
         "message": {"role": "user", "content": "Fix the login bug"}},
        {"type": "user", "timestamp": "2026-11-01T10:00:30.000Z",
         "message": {"role": "user",
                     "content": "<command-name>compact</command-name>"
                                "<command-args>keep</command-args>"}},
        {"type": "assistant", "timestamp": "2026-11-01T10:01:00.000Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4.5",
                     "content": [
                         {"type": "text", "text": "I will fix it."},
                         {"type": "tool_use", "id": "tu_1", "name": "Edit",
                          "input": {"file_path": "/src/app/login.py"}},
                         {"type": "thinking", "thinking": "plan first"},
                     ]}},
        {"type": "user", "timestamp": "2026-11-01T10:01:30.000Z",
         "message": {"role": "user",
                     "content": [{"type": "tool_result",
                                  "tool_use_id": "tu_1", "content": "ok"}]}},
        {"type": "system", "subtype": "compact_boundary",
         "timestamp": "2026-11-01T10:02:00.000Z",
         "compactMetadata": {"trigger": "manual", "preTokens": 1000,
                             "postTokens": 500, "cumulativeDroppedTokens": 500,
                             "durationMs": 2000}},
        {"type": "system", "subtype": "away_summary",
         "timestamp": "2026-11-01T10:03:00.000Z", "content": "Login bug recap"},
        {"type": "ai-title", "timestamp": "2026-11-01T10:04:00.000Z",
         "aiTitle": "Fix login bug"},
        {"type": "user", "timestamp": "2026-11-01T10:05:00.000Z",
         "isMeta": True, "message": {"content": "meta prompt"}},
    ]


class ClaudeSourceTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        old = claude_module.PROJECTS_DIR
        claude_module.PROJECTS_DIR = self.projects
        self.addCleanup(setattr, claude_module, "PROJECTS_DIR", old)
        claude_module._LINE_CACHE.clear()
        self.source = ClaudeSource()

    def add_session(self, cwd, session_id, lines):
        return write_jsonl(self.projects / _encode_cwd(cwd) / f"{session_id}.jsonl", lines)

    def add_main_session(self):
        self.add_session(CLAUDE_CWD, "sess-claude-1", main_lines())

    def test_list_sessions_row_counts(self):
        self.add_main_session()
        rows = self.source.list_sessions(CLAUDE_CWD, False)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.source, "claude")
        self.assertEqual(row.session_id, "sess-claude-1")
        self.assertEqual(row.project, CLAUDE_CWD)
        self.assertEqual(row.title, "Fix login bug")
        self.assertEqual(row.lines, 8)
        self.assertEqual(row.user_prompts, 1)
        self.assertEqual(row.tool_calls, 1)
        self.assertEqual(row.assistant_texts, 1)
        self.assertEqual(row.compacts, 1)
        self.assertEqual(row.last_ts, "2026-11-01T10:05:00.000Z")

    def test_list_sessions_all_dirs_and_dir_filter(self):
        self.add_main_session()
        self.add_session("/Users/me/other", "sess-claude-2",
                        [{"type": "user", "timestamp": "2026-11-02T10:00:00.000Z",
                          "message": {"content": "Second session"}}])
        self.assertEqual(len(self.source.list_sessions(None, True)), 2)
        self.assertEqual(len(self.source.list_sessions("/Users/me/other", False)), 1)
        self.assertEqual(len(self.source.list_sessions("/Users/me/missing", False)), 0)

    def test_find_with_dir_and_anywhere(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", CLAUDE_CWD)
        self.assertIsNotNone(handle)
        self.assertEqual(handle.name, "sess-claude-1.jsonl")
        self.assertIsNone(self.source.find("sess-claude-1", "/Users/me/missing"))
        self.add_session("/Users/me/other", "sess-claude-2",
                        [{"type": "user", "timestamp": "2026-11-02T10:00:00.000Z",
                          "message": {"content": "Second session"}}])
        found = self.source.find("sess-claude-2", None)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "sess-claude-2.jsonl")
        self.assertIsNone(self.source.find("missing-session", None))

    def test_metadata_fields(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", None)
        meta = self.source.metadata(handle, "sess-claude-1")
        self.assertEqual(meta.source, "claude")
        self.assertEqual(meta.session_id, "sess-claude-1")
        self.assertEqual(meta.title, "Fix login bug")
        self.assertEqual(meta.cwd, CLAUDE_CWD)
        self.assertEqual(meta.model, "claude-sonnet-4.5")
        self.assertEqual(meta.version, "1.0.99")
        self.assertEqual(meta.git_branch, "main")
        self.assertEqual(meta.line_count, 8)
        self.assertEqual(meta.compact_count, 1)
        self.assertEqual(meta.first_ts, "2026-11-01T10:00:00.000Z")
        self.assertEqual(meta.last_ts, "2026-11-01T10:05:00.000Z")
        self.assertGreater(meta.file_size, 0)

    def test_user_prompts_exclude_meta_commands_and_tool_results(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].text, "Fix the login bug")
        self.assertEqual(prompts[0].timestamp, "2026-11-01T10:00:00.000Z")

    def test_user_prompt_joins_multiple_text_blocks(self):
        self.add_session(CLAUDE_CWD, "sess-multi", [
            {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
             "message": {"role": "user",
                         "content": [{"type": "text", "text": "part one"},
                                     {"type": "text", "text": "part two"}]}},
        ])
        handle = self.source.find("sess-multi", None)
        prompts = self.source.user_prompts(handle)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].text, "part one\npart two")
        events = self.source.timeline_events(handle)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, EventKind.USER_MESSAGE)
        self.assertEqual(events[0].text, "part one\npart two")

    def test_assistant_texts_and_tool_calls(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", None)
        texts = self.source.assistant_texts(handle)
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].text, "I will fix it.")
        self.assertEqual(texts[0].timestamp, "2026-11-01T10:01:00.000Z")
        calls = self.source.tool_calls(handle)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "Edit")
        self.assertEqual(calls[0].input, json.dumps({"file_path": "/src/app/login.py"}))
        self.assertEqual(calls[0].tool_id, "tu_1")
        self.assertEqual(calls[0].timestamp, "2026-11-01T10:01:00.000Z")

    def test_compact_boundaries_and_away_summaries(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", None)
        boundaries = self.source.compact_boundaries(handle)
        self.assertEqual(len(boundaries), 1)
        b = boundaries[0]
        self.assertEqual(b.timestamp, "2026-11-01T10:02:00.000Z")
        self.assertEqual(b.trigger, "manual")
        self.assertEqual(b.pre_tokens, 1000)
        self.assertEqual(b.post_tokens, 500)
        self.assertEqual(b.dropped_tokens, 500)
        self.assertEqual(b.duration_ms, 2000)
        away = self.source.away_summaries(handle)
        self.assertEqual(len(away), 1)
        self.assertEqual(away[0].timestamp, "2026-11-01T10:03:00.000Z")
        self.assertEqual(away[0].content, "Login bug recap")

    def test_timeline_event_kinds_in_order(self):
        self.add_main_session()
        handle = self.source.find("sess-claude-1", None)
        events = self.source.timeline_events(handle)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            EventKind.USER_MESSAGE,
            EventKind.SLASH_COMMAND,
            EventKind.ASSISTANT_TEXT,
            EventKind.TOOL_CALL,
            EventKind.THINKING,
            EventKind.TOOL_RESULT,
            EventKind.COMPACT_BOUNDARY,
            EventKind.AWAY_SUMMARY,
            EventKind.AI_TITLE,
        ])
        slash = events[1]
        self.assertEqual(slash.kind, EventKind.SLASH_COMMAND)
        self.assertEqual(slash.text, "compact")
        tool_result = events[5]
        self.assertEqual(tool_result.kind, EventKind.TOOL_RESULT)
        self.assertEqual(tool_result.tool_id, "tu_1")
        self.assertEqual(tool_result.text, "ok")
        boundary = events[6]
        self.assertEqual(boundary.metadata["trigger"], "manual")
        self.assertEqual(boundary.metadata["pre_tokens"], 1000)
        self.assertEqual(boundary.metadata["post_tokens"], 500)
        ai_title = events[8]
        self.assertEqual(ai_title.kind, EventKind.AI_TITLE)
        self.assertEqual(ai_title.text, "Fix login bug")

    def test_files_edited_dedup_and_tool_scope(self):
        self.add_session(CLAUDE_CWD, "sess-edits", [
            {"type": "assistant", "timestamp": "2026-11-01T10:00:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "t1", "name": "Edit",
                  "input": {"file_path": "/a.py"}},
                 {"type": "tool_use", "id": "t2", "name": "Bash",
                  "input": {"command": "ls"}},
                 {"type": "tool_use", "id": "t3", "name": "Write",
                  "input": {"file_path": "/b.py"}},
                 {"type": "tool_use", "id": "t4", "name": "NotebookEdit",
                  "input": {"notebook_path": "/c.ipynb"}},
                 {"type": "tool_use", "id": "t5", "name": "Edit",
                  "input": {"file_path": "/a.py"}},
             ]}},
        ])
        files = self.source.files_edited("sess-edits")
        self.assertEqual(files, [Path("/a.py"), Path("/b.py"), Path("/c.ipynb")])

    def test_matches_title_and_prompts(self):
        self.add_main_session()
        self.assertTrue(self.source.matches("sess-claude-1", None, "LOGIN"))
        self.assertTrue(self.source.matches("sess-claude-1", None, "login"))
        self.assertFalse(self.source.matches("sess-claude-1", None, "missing"))
        self.assertFalse(self.source.matches("missing-session", None, "login"))

    def test_subagents_metadata(self):
        self.add_main_session()
        subagents = self.projects / _encode_cwd(CLAUDE_CWD) / "sess-claude-1" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-01.json").write_text(json.dumps(
            {"agentType": "explore", "description": "look around"}))
        (subagents / "readme.txt").write_text("not metadata")
        handle = self.source.find("sess-claude-1", None)
        result = self.source.subagents(handle)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].agent_type, "explore")
        self.assertEqual(result[0].description, "look around")

    def test_line_cache_refreshes_on_append(self):
        path = self.add_session(CLAUDE_CWD, "sess-grow", [
            {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
             "message": {"content": "first"}},
            {"type": "user", "timestamp": "2026-11-01T10:01:00.000Z",
             "message": {"content": "second"}},
        ])
        handle = self.source.find("sess-grow", None)
        self.assertEqual(self.source.metadata(handle, "sess-grow").line_count, 2)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user",
                                "timestamp": "2026-11-01T10:02:00.000Z",
                                "message": {"content": "third"}}) + "\n")
        self.assertEqual(self.source.metadata(handle, "sess-grow").line_count, 3)
        self.assertEqual(len(self.source.user_prompts(handle)), 3)

    def test_invalid_json_and_non_dict_lines_skipped(self):
        valid = {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
                 "message": {"content": "Fix the login bug"}}
        good_dir = self.projects / _encode_cwd(CLAUDE_CWD)
        good_dir.mkdir(parents=True)
        (good_dir / "sess-good.jsonl").write_text(
            "not json\n[1, 2]\n" + json.dumps(valid) + "\n")
        (good_dir / "sess-garbage.jsonl").write_text("not json\nnope\n")
        rows = self.source.list_sessions(CLAUDE_CWD, False)
        self.assertEqual([r.session_id for r in rows], ["sess-good"])
        self.assertEqual(rows[0].lines, 1)
        handle = self.source.find("sess-good", None)
        self.assertEqual(len(self.source.user_prompts(handle)), 1)

    def test_local_command_excluded_from_prompts(self):
        self.add_session(CLAUDE_CWD, "sess-cmd", [
            {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
             "message": {"role": "user",
                         "content": "<local-command-stdout>ok</local-command-stdout>"}},
        ])
        handle = self.source.find("sess-cmd", None)
        self.assertEqual(self.source.user_prompts(handle), [])
        events = self.source.timeline_events(handle)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, EventKind.SLASH_COMMAND)
        self.assertEqual(events[0].text, "")


class ClaudeUsageTests(unittest.TestCase):
    def test_usage_sums_tokens_and_turn_durations(self):
        from sources.claude import _usage_from_lines
        lines = [
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 100, "output_tokens": 20,
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 1000}}},
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 50, "output_tokens": 10}}},
            {"type": "system", "subtype": "turn_duration", "durationMs": 4000},
            {"type": "system", "subtype": "turn_duration", "durationMs": 6000},
        ]
        tokens_input, tokens_output, active_ms = _usage_from_lines(lines)
        self.assertEqual(tokens_input, 1450)
        self.assertEqual(tokens_output, 30)
        self.assertEqual(active_ms, 10000)


if __name__ == "__main__":
    unittest.main()