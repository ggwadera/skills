# Claude Code notes

Adapter: `sources/claude.py`. Format notes for Claude Code session
transcripts (`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`).

- Project directories encode the CWD by replacing every non-alphanumeric
  character with `-`.
- Files edited are derived from `Edit`, `Write`, `NotebookEdit`, and `MultiEdit`
  tool calls in the transcript (`file_path`, falling back to `notebook_path`).
- Away summaries come from `system` events with subtype `away_summary`.
- Compaction boundaries come from `system` events with subtype
  `compact_boundary`; token counts are included when present.
