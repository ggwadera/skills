import os
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from session_fixtures import (
    CLAUDE_CLI_CWD,
    TempDirTestCase,
    build_claude_fixture,
    build_codex_fixture,
    build_dsh_fixture,
    build_opencode_fixture,
    write_jsonl,
)
from sources.claude import _encode_cwd


class CliTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.claude_dir = self.root / "claude"
        self.codex_dir = self.root / "codex"
        self.db_path = self.root / "opencode.db"
        self.claude_id = build_claude_fixture(self.claude_dir)
        self.codex_id = build_codex_fixture(self.codex_dir)
        self.opencode_id = build_opencode_fixture(self.db_path)
        self.env = dict(os.environ)
        self.env.update({
            "CLAUDE_DIR": str(self.claude_dir),
            "CODEX_DIR": str(self.codex_dir),
            "OPENCODE_DB": str(self.db_path),
            "DSH_HOME": str(self.root / "empty-dsh"),
        })

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SKILL_ROOT / "session_extract.py"), *args],
            capture_output=True, text=True, env=self.env,
            cwd=str(SKILL_ROOT),
        )

    def test_list_defaults_to_cwd_without_matches(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No sessions found from any source.", result.stdout)

    def test_list_all_three_sources(self):
        result = self.run_cli("list", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.claude_id, result.stdout)
        self.assertIn(self.codex_id, result.stdout)
        self.assertIn(self.opencode_id, result.stdout)
        self.assertIn("Total: 3 session(s)", result.stdout)

    def test_list_stats_columns(self):
        result = self.run_cli("list", "--stats", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Tok in", result.stdout)
        self.assertIn("Tok out", result.stdout)
        self.assertIn("Active", result.stdout)
        self.assertIn("0.2500", result.stdout)
        self.assertIn("Cost is available for opencode sessions only.", result.stdout)

    def test_list_stats_absent_cost_prints_dash(self):
        result = self.run_cli("list", "--stats", "--source", "claude", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("claude", result.stdout)
        self.assertNotIn("0.2500", result.stdout)

    def test_list_source_filter(self):
        result = self.run_cli("list", "--source", "codex", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.codex_id, result.stdout)
        self.assertNotIn(self.claude_id, result.stdout)
        self.assertNotIn(self.opencode_id, result.stdout)
        self.assertIn("Total: 1 session(s)", result.stdout)

    def test_list_grep_matches_prompt_text(self):
        result = self.run_cli("list", "--grep", "flaky")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.codex_id, result.stdout)
        self.assertNotIn(self.claude_id, result.stdout)
        self.assertNotIn(self.opencode_id, result.stdout)
        self.assertIn("Total: 1 session(s)", result.stdout)

    def test_list_grep_no_matches(self):
        result = self.run_cli("list", "--grep", "no-such-text")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No sessions matched 'no-such-text'.", result.stdout)

    def test_summary_unknown_session_exits_1(self):
        result = self.run_cli("summary", "missing-session")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: session 'missing-session' not found.",
                      result.stdout)

    def test_summary_claude_session(self):
        result = self.run_cli("summary", self.claude_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"=== SESSION SUMMARY: {self.claude_id} ===",
                      result.stdout)
        self.assertIn("Source:       claude", result.stdout)
        self.assertIn("Fix the login bug", result.stdout)
        self.assertIn("--- Compaction Boundaries ---", result.stdout)
        self.assertIn("Login bug fixed, tests running", result.stdout)

    def test_summary_opencode_autodetected(self):
        result = self.run_cli("summary", self.opencode_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Source:       opencode", result.stdout)
        self.assertIn("Fix the failing test", result.stdout)

    def test_summary_codex_explicit_source(self):
        result = self.run_cli("summary", self.codex_id, "--source", "codex")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Source:       codex", result.stdout)
        self.assertIn("Fix the flaky test", result.stdout)

    def test_autodetect_prefers_opencode(self):
        encoded = _encode_cwd(CLAUDE_CLI_CWD)
        write_jsonl(
            self.claude_dir / "projects" / encoded / f"{self.opencode_id}.jsonl",
            [{"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
              "sessionId": self.opencode_id, "cwd": CLAUDE_CLI_CWD,
              "message": {"role": "user", "content": "Fix the login bug"}}])
        result = self.run_cli("summary", self.opencode_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Source:       opencode", result.stdout)

    def test_timeline_tail_shows_last_event_only(self):
        result = self.run_cli("timeline", self.claude_id, "--tail", "1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("AI_TITLE: Fix login bug", result.stdout)
        self.assertNotIn("USER:", result.stdout)
        self.assertNotIn("ASSISTANT:", result.stdout)

    def test_timeline_since_filter(self):
        result = self.run_cli("timeline", self.claude_id,
                              "--since", "2026-11-01T10:02:00")
        self.assertEqual(result.returncode, 0)
        self.assertIn("COMPACT_BOUNDARY", result.stdout)
        self.assertIn("AWAY_SUMMARY", result.stdout)
        self.assertIn("AI_TITLE: Fix login bug", result.stdout)
        self.assertNotIn("USER:", result.stdout)
        self.assertNotIn("ASSISTANT:", result.stdout)

    def test_timeline_until_filter(self):
        result = self.run_cli("timeline", self.claude_id,
                              "--until", "2026-11-01T10:00:30")
        self.assertEqual(result.returncode, 0)
        self.assertIn("USER:", result.stdout)
        self.assertIn("Fix the login bug", result.stdout)
        self.assertNotIn("ASSISTANT:", result.stdout)
        self.assertNotIn("COMPACT_BOUNDARY", result.stdout)

    def test_compact_claude_token_counts(self):
        result = self.run_cli("compact", self.claude_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Trigger:    manual", result.stdout)
        self.assertIn("Pre tokens: 1,000", result.stdout)
        self.assertIn("Post tokens:500", result.stdout)
        self.assertIn("Dropped:    500", result.stdout)
        self.assertIn("Login bug fixed, tests running", result.stdout)

    def test_compact_opencode_summary(self):
        result = self.run_cli("compact", self.opencode_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Trigger:    auto", result.stdout)
        self.assertIn("--- Away Summaries ---", result.stdout)
        self.assertIn("Failing test recap", result.stdout)

    def test_context_opencode_recap(self):
        result = self.run_cli("context", self.opencode_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("=== CONTINUATION CONTEXT ===", result.stdout)
        self.assertIn("--- Auto-generated Recap (from away_summary) ---",
                      result.stdout)
        self.assertIn("Failing test recap", result.stdout)
        self.assertIn("Fix the failing test", result.stdout)

    def test_list_missing_sources_warns_and_exits_zero(self):
        self.env.update({
            "CLAUDE_DIR": str(self.root / "none-claude"),
            "CODEX_DIR": str(self.root / "none-codex"),
            "OPENCODE_DB": str(self.root / "none-opencode.db"),
        })
        result = self.run_cli("list", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No sessions found (some sources were unavailable).",
                      result.stdout)
        self.assertIn("opencode source unavailable", result.stderr)


class CliDshTestCase(TempDirTestCase):
    """CLI coverage for the DeepSeek harness source (isolated env)."""

    def setUp(self):
        super().setUp()
        self.dsh_home = self.root / "dsh"
        self.dsh_id = build_dsh_fixture(self.dsh_home)
        self.env = dict(os.environ)
        self.env.update({
            "CLAUDE_DIR": str(self.root / "empty-claude"),
            "CODEX_DIR": str(self.root / "empty-codex"),
            "OPENCODE_DB": str(self.root / "empty-opencode.db"),
            "DSH_HOME": str(self.dsh_home),
        })

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SKILL_ROOT / "session_extract.py"), *args],
            capture_output=True, text=True, env=self.env,
            cwd=str(SKILL_ROOT),
        )

    def test_list_source_filter(self):
        result = self.run_cli("list", "--source", "dsh", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.dsh_id, result.stdout)
        self.assertIn("Fix flaky test", result.stdout)
        self.assertIn("Total: 1 session(s)", result.stdout)

    def test_list_autodetect_includes_dsh(self):
        result = self.run_cli("list", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.dsh_id, result.stdout)

    def test_list_stats_columns(self):
        result = self.run_cli("list", "--stats", "--source", "dsh", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("9,096", result.stdout)
        self.assertIn("765", result.stdout)
        self.assertIn("4s", result.stdout)

    def test_list_grep_matches_edited_prompt(self):
        result = self.run_cli("list", "--grep", "backoff")
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.dsh_id, result.stdout)
        self.assertIn("Total: 1 session(s)", result.stdout)

    def test_list_no_sessions_message(self):
        self.env["DSH_HOME"] = str(self.root / "missing-dsh")
        result = self.run_cli("list", "--source", "dsh", "--all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No DeepSeek harness sessions found.", result.stdout)

    def test_summary_autodetected(self):
        result = self.run_cli("summary", self.dsh_id)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Source:       dsh", result.stdout)
        self.assertIn("Fix flaky test", result.stdout)
        self.assertIn("deepseek-flash", result.stdout)
        self.assertIn("9,096 in / 765 out", result.stdout)
        self.assertIn("--- Compaction Boundaries ---", result.stdout)
        self.assertIn("Recap of earlier work", result.stdout)
        self.assertIn("--- Todos ---", result.stdout)

    def test_timeline(self):
        result = self.run_cli("timeline", self.dsh_id, "--source", "dsh")
        self.assertEqual(result.returncode, 0)
        self.assertIn("USER:", result.stdout)
        self.assertIn("Fix the flaky test", result.stdout)
        self.assertIn("TOOL_CALL(bash)", result.stdout)
        self.assertIn("COMPACT_BOUNDARY", result.stdout)
        self.assertIn("AWAY_SUMMARY", result.stdout)

    def test_compact(self):
        result = self.run_cli("compact", self.dsh_id, "--source", "dsh")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Trigger:    auto", result.stdout)
        self.assertIn("Pre tokens: 1,000", result.stdout)
        self.assertIn("Dropped:    500", result.stdout)
        self.assertIn("Recap of earlier work", result.stdout)

    def test_context_recap_and_label(self):
        result = self.run_cli("context", self.dsh_id, "--source", "dsh")
        self.assertEqual(result.returncode, 0)
        self.assertIn("=== CONTINUATION CONTEXT ===", result.stdout)
        self.assertIn("--- Auto-generated Recap (from away_summary) ---",
                      result.stdout)
        self.assertIn("Recap of earlier work", result.stdout)
        self.assertIn("previous DeepSeek harness session", result.stdout)


if __name__ == "__main__":
    unittest.main()