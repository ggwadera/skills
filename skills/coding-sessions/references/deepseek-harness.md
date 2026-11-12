# DeepSeek harness (dsh) notes

Adapter: `sources/dsh.py`. Format notes for DeepSeek harness session
transcripts (`~/.dsh/sessions/<projectKey>/<sessionId>/session.vN.jsonl[.zstd]`).

- Home is `~/.dsh` (overridable via `DSH_HOME`). Each session is a directory
  `sessions/<projectKey>/<sessionId>/` holding `session.vN.jsonl[.zstd]`; the
  newest generation in the directory is read, older generations are kept by
  the harness and ignored here (v0–v3 parse best-effort when they are the
  newest). `.lock` and `*.tmp` files are skipped.
- zstd decoding falls back through stdlib `compression.zstd` (Python 3.14+),
  the `zstandard` package, then the `zstd` CLI. Plain `.jsonl` files
  (`compression: none`) always work. A missing backend surfaces as a
  `dsh source unavailable` warning on `list`.
- Real user prompts are `user/message` events with `source.kind == "user"`.
  Injected context (`hook`, `parent`, `runtime-context`, `skill-catalog`,
  `time-context`, `disposed` sources, plus `system/message` and
  `developer/message` events) is excluded, matching the harness's own
  Markdown export.
- Surface edits honor `surfaceOp: {op: "replace"}`: an edited user message
  or a refreshed `tool/result` supersedes the earlier version (the edit
  keeps its original position). Compaction checkpoints replace the compacted
  range with a `compact-checkpoint` message but do **not** erase transcript
  history, so pre-compaction prompts and assistant messages still appear.
- Model comes from the latest `request/context` (falling back to
  `request/header`); `agentPreset` is reported as the agent. Tokens sum
  `assistant/message` usage (input + cache read/write = prompt tokens).
  Active time sums `turn/start`–`turn/end` spans, so idle gaps between user
  turns are excluded. Cost is not derived (shown as a dash).
- Compaction boundaries pair `compaction/start|summary|end` by
  `compactionId` (trigger is `command` when a `sourceCommandId` is present,
  else `auto`; pre/post/dropped tokens come from the summary's usage and
  `shadowedTokenCount`). Model-free `compaction/prune` bursts outside any
  compaction are aggregated into a single `prune` boundary. Summary text is
  surfaced as the away summary and used by `context`.
- Todos come from the last `todo/write` (priority is not stored, so it is
  blank). Subagents come from `subagent/catalog` entries plus a header scan
  for child sessions (`parentSession` with `origin: "subagent"` or
  `delegationDepth > 0`); forked/resumed sessions that merely carry
  `parentSession` are listed as regular sessions, not subagents.
- Seeded forks inherit earlier events, so the first event can predate the
  header's `createdAt`; the summary reports the earliest event time.
