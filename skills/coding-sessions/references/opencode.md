# opencode notes

Adapter: `sources/opencode.py` (V1: `sources/opencode_v1.py`, V2:
`sources/opencode_v2.py`). Format notes for the opencode SQLite database.

- The `OPENCODE_DB` environment variable overrides the default database path
  (`~/.local/share/opencode/opencode.db`).
- `--dir` filters by the session directory column (exact match).
- V1 and V2 schemas are detected per database. A migrated database contains
  both; sessions that exist in both are de-duplicated by newest
  `time_updated`, and sessions that exist only in V2 are still listed.
- Synthetic text parts are excluded, so prompt counts reflect real user
  prompts. In V1 this filters `synthetic` and `ignored` text parts; in V2 the
  message type itself distinguishes `user` from `synthetic`, `system`, and
  `skill` messages.
- Compaction summaries are surfaced as away summaries: V1 reads assistant
  messages flagged `summary: true`; V2 reads `compaction` messages and their
  `summary` field. `context` uses the latest summary as the recap.
- V2 normally includes `session_part` (created by migrations); when the
  table is absent, assistant content is read inline. Content may be inline,
  split across both, or a mix.
- `todo` rows exist only in V1. Migrated databases keep them, so todos appear
  for any session with V1 todo rows; V2-only sessions have none.
- Subagent sessions are detected via the `parent_id` column in both schemas.
