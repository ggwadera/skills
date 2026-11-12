# Codex notes

Adapter: `sources/codex.py`. Format notes for Codex rollout JSONL
(`~/.codex/sessions/`, `~/.codex/archived_sessions/`, index in
`~/.codex/session_index.jsonl`).

- Rollout files use one JSON line per item. Real user prompts are recovered
  from `event_msg` items whose payload is `item_completed` with an item type of
  `UserMessage`; this works for both legacy and current (paginated) histories.
  Response items carrying `internal_chat_message_metadata_passthrough`
  provenance are used as a fallback, and legacy `event_msg/user_message`
  payloads as the last fallback.
- Injected context fragments (`<environment_context>`, `# AGENTS.md
  instructions`, `<skills_instructions>`, notifications, plugin lists) are
  excluded by marker, so they never appear as user prompts.
- Tool calls cover `custom_tool_call`, `function_call`, `local_shell_call`,
  `web_search_call`, and `tool_search_call`, with outputs joined from their
  result items. Namespaced function calls are rendered as `<namespace>.<name>`.
- Compaction is read from `compacted` items. The preserved summary text, when
  present, is surfaced as an away summary and used by `context`. Encrypted
  `response_item/compaction` mirrors are shown only when no `compacted` item
  exists.
- The model comes from the latest `turn_context`; `session_meta` contributes
  CWD, CLI version, source, and git branch.
- Subagents are read from `world_state`; both the legacy `- <id>: <nickname>`
  format and the current `<agent name="..."/>` format are supported.
- Reverted sessions leave several rollout files that share a thread ID. The
  newest file is used and duplicate rows are collapsed by session ID.
- `session_index.jsonl` supplies titles only; archived rollouts are found by
  scanning `archived_sessions/` directly.
- Sessions are identified by the thread UUID in the rollout filename. When
  several files share a thread (reverted rollouts), the newest file by
  modification time is used. Checkpoint files of one conversation (shared
  payload session ID) collapse to the most complete rollout; side transcripts
  with their own indexed title (for example review tasks) stay separate.
