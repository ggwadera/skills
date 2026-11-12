---
name: coding-sessions
description: Inspect and continue from past Claude Code, Codex, opencode, and DeepSeek harness (dsh) session transcripts stored in ~/.claude/, ~/.codex/, ~/.local/share/opencode/opencode.db, and ~/.dsh/. Use when the user asks to read, list, inspect, summarize, extract context from, or continue past sessions. Also use when the user mentions "claude code history", "codex history", "opencode history", "deepseek history", "dsh history", "past sessions", "session transcript", or references a session ID.
license: MIT
compatibility: Requires Python 3.9 or newer and local session data from Claude Code, Codex, opencode, or the DeepSeek harness. Decoding zstd-compressed harness sessions requires Python 3.14+ (compression.zstd), the zstandard package, or the zstd CLI.
metadata:
  author: ggwadera
  version: "1.2.0"
---

# Coding Sessions

Inspect and continue from past session transcripts from four sources:

- **Claude Code** stores one JSONL file per session under
  `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`. Each line is a typed
  event (user prompt, assistant response, tool call, tool result, compact
  boundary, etc.).
- **Codex** stores one JSONL file per session under
  `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl` (active)
  and `~/.codex/archived_sessions/rollout-<timestamp>-<session-id>.jsonl`
  (archived). The session index lives in `~/.codex/session_index.jsonl`.
- **opencode** stores sessions in a SQLite database at
  `~/.local/share/opencode/opencode.db` (overridable via the `OPENCODE_DB`
  environment variable). It supports both storage schemas:
  - **V1** (opencode 1.x): tables `session`, `message`, `part`, `todo`.
  - **V2** (opencode 2.x): tables `session_v2`, `session_message`, and
    optionally `session_part`. Assistant content is stored inline in
    `session_message.data.content` by default, in `session_part` rows when
    `experimental.part_rows` is enabled, or a mix of both.
- **DeepSeek harness (dsh)** stores one JSONL transcript per session under
  `~/.dsh/sessions/<projectKey>/<sessionId>/session.vN.jsonl[.zstd]`
  (overridable via the `DSH_HOME` environment variable), zstd-compressed by
  default. Line 1 is the session header; every following line is a typed
  event envelope with `seq`, `time`, and `data`. The current format
  generation is v4.

When a database contains both schemas (a V1 database that was migrated by an
opencode 2.x run), the script reads both and de-duplicates sessions by ID,
keeping the row with the newest `time_updated`.

Detail commands search all directories unless `--dir` is given; `list`
defaults to `$PWD` unless `--all` or `--grep` is used.

The script auto-detects which source a session ID belongs to. You can also
filter explicitly with `--source claude`, `--source codex`, `--source
opencode`, or `--source dsh`.

## When to use

- User asks to list or browse past sessions (Claude Code, Codex, opencode,
  or the DeepSeek harness)
- User wants a summary of what happened in a specific session
- User wants to continue work from a previous session
- User references a session ID
- User asks "what did I work on?" or similar

## Architecture

The script uses a plug-in interface: each source implements the
`SessionSource` ABC defined in `sources/base.py`.

- `sources/claude.py` — Claude Code JSONL adapter.
- `sources/codex.py` — Codex rollout JSONL adapter.
- `sources/opencode.py` — union adapter that reads both opencode schemas.
- `sources/opencode_v1.py` — opencode V1 table adapter.
- `sources/opencode_v2.py` — opencode V2 table adapter.
- `sources/opencode_base.py` — shared opencode database helpers and schema
  detection.
- `sources/dsh.py` — DeepSeek harness JSONL adapter (plain or zstd).

A registry in `sources/__init__.py` maps source names to adapter instances and
handles auto-detection. Adapters extract structured data (dataclasses); the
CLI's shared formatters render output identically regardless of source. To add
a new source, create a new module in `sources/`, implement the `SessionSource`
interface, and register it in `sources/__init__.py`.

## Helper script

All session extraction is handled by `session_extract.py`, next to this
`SKILL.md`. Always use the script instead of parsing JSONL or the SQLite DB
manually — all formats have many event types and the script handles them all.

Run it from this skill's directory:

```bash
cd <skill-directory>
python3 session_extract.py <command> [args]
```

Or pass its absolute path from any directory:

```bash
python3 <skill-directory>/session_extract.py <command> [args]
```

The script requires Python 3.9 or newer and has no third-party dependencies.

### Commands

**List sessions from all sources (auto-detected):**
```bash
python3 session_extract.py list
```

**List sessions from one source only:**
```bash
python3 session_extract.py list --source claude --dir /Users/me/myproject
python3 session_extract.py list --source codex
python3 session_extract.py list --source opencode --dir /Users/me/myproject
python3 session_extract.py list --source dsh --all
```

**List sessions with token, cost, and active-time columns:**
```bash
python3 session_extract.py list --stats --all --limit 20
python3 session_extract.py list --stats --source opencode --dir /Users/me/myproject
```

`--stats` adds four columns: `Tok in` (all prompt tokens, including cache
reads), `Tok out`, `Cost`, and `Active`. `Active` is the time the agent spent
replying: per-turn model and tool time summed across turns, with idle gaps
between user turns excluded. A dash means the value is not present in that
source's store. Cost is reported for opencode sessions only; Codex, Claude
Code, and the DeepSeek harness do not report it.

**Find sessions by text (case-insensitive; searches titles and user prompts):**
```bash
python3 session_extract.py list --grep ABC-1234
python3 session_extract.py list --source codex --all --grep "merge conflict"
```

**One-page summary of a session (metadata, user prompts, files edited):**
```bash
python3 session_extract.py summary <session-id>
python3 session_extract.py summary <session-id> --source codex
python3 session_extract.py summary <session-id> --source opencode
python3 session_extract.py summary <session-id> --source dsh
python3 session_extract.py summary <session-id> --max-prompts 5
```

**Full conversation timeline (all user prompts, assistant text, tool calls):**
```bash
python3 session_extract.py timeline <session-id>
python3 session_extract.py timeline <session-id> --source codex
python3 session_extract.py timeline <session-id> --tail 100
python3 session_extract.py timeline <session-id> --since 2026-10-14 --until 2026-10-15
```

**Extract compaction boundaries with token counts:**
```bash
python3 session_extract.py compact <session-id>
python3 session_extract.py compact <session-id> --source opencode
```

**Build a continuation prompt from the latest session state:**
```bash
python3 session_extract.py context <session-id>
python3 session_extract.py context <session-id> --source codex
python3 session_extract.py context <session-id> --source opencode
python3 session_extract.py context <session-id> --source dsh
```

### Options

| Flag                | Description                                                             |
| ------------------- | ----------------------------------------------------------------------- |
| `--source <s>`      | Filter by source: `claude`, `codex`, `opencode`, `dsh`, or `auto` (default: `auto`) |
| `--dir <path>`      | Filter by project directory (default: `$PWD` unless `--all` is used; Codex matches the session's recorded working directory) |
| `--all`             | List sessions from all directories (list mode only)                     |
| `--limit <n>`       | Max sessions to list (default: 50, list mode only)                      |
| `--grep <text>`     | Only show sessions whose title or user prompts contain the text; scans all sessions (list mode only) |
| `--stats`           | Add `Tok in`, `Tok out`, `Cost`, and `Active` columns (list mode only)  |
| `--max-prompts <n>` | Show only the last N user prompts (summary mode only)                   |
| `--since <iso>`     | Only show events at or after this timestamp (timeline mode only)        |
| `--until <iso>`     | Only show events at or before this timestamp (timeline mode only)       |
| `--tail <n>`        | Show only the last N events (timeline mode only)                        |

### Auto-detection

When `--source` is `auto` (the default), the script searches for the session
ID in opencode's SQLite database first (indexed exact-match on `ses_` prefixed
IDs), then in Claude Code's `~/.claude/projects/`, then in Codex's
`~/.codex/sessions/` and `~/.codex/archived_sessions/`, and finally in the
DeepSeek harness's `~/.dsh/sessions/`. The first match wins.
Sources that cannot be read (for example a missing database) are skipped. For
the `list` command, `auto` lists sessions from all four sources with a
`Source` column indicating `claude`, `codex`, `opencode`, or `dsh`.

## opencode-specific notes

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

## Codex-specific notes

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

## Claude Code-specific notes

- Project directories encode the CWD by replacing every non-alphanumeric
  character with `-`.
- Files edited are derived from `Edit`, `Write`, `NotebookEdit`, and `MultiEdit`
  tool calls in the transcript (`file_path`, falling back to `notebook_path`).
- Away summaries come from `system` events with subtype `away_summary`.
- Compaction boundaries come from `system` events with subtype
  `compact_boundary`; token counts are included when present.

## DeepSeek harness-specific notes

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

## Workflow

### Step 1: List sessions

Start with `list` to see what is available. Key columns:

- **Source**: `claude`, `codex`, `opencode`, or `dsh`
- **Lines**: total JSONL events (Claude Code, Codex) or message count (opencode) — a rough complexity indicator
- **Prompts**: number of real user prompts
- **Tools**: number of tool calls made by the assistant
- **Cmp**: number of compaction boundaries
- **Tok in / Tok out**: prompt and completion tokens summed for the session (`--stats` only)
- **Cost**: session cost; opencode only (`--stats` only)
- **Active**: agent reply time with idle gaps excluded (`--stats` only)

Use `--grep` to find sessions by ticket ID, feature name, or any text that
appeared in a user prompt.

### Step 2: Summarize

For a quick overview, use `summary <session-id>`. This shows:
- Session metadata (title, model, timestamps, cost, tokens, agent)
- All user prompts in order (truncated to 500 chars; use `--max-prompts` to tail)
- Claude Code: compaction boundaries, away summaries, files edited
- Codex: reasoning summaries, compaction summaries, subagents
- opencode: reasoning summaries, todos, subagent sessions, compaction summaries, cost/tokens
- DeepSeek harness: reasoning summaries, todos, subagents, compaction summaries, tokens

### Step 3: Full timeline (if needed)

Use `timeline <session-id>` for the complete conversation including:
- User prompts (full text, truncated to 1000 chars)
- Assistant text responses (truncated to 2000 chars)
- Tool calls with names and input arguments (truncated to 300 chars)
- Tool results (truncated to 200 chars)
- Claude Code: compact boundaries, away summaries, slash commands
- Codex: reasoning summaries (encrypted content is skipped), token counts
- opencode: reasoning summaries, file edits, compaction events
- DeepSeek harness: reasoning, tool results, compaction/prune events, turn durations, working-directory changes

This is verbose — prefer `summary` first and only use `timeline` when you
need to see the actual assistant reasoning or tool call details. Use
`--tail`, `--since`, and `--until` to page through long sessions.

Detail commands exit 1 when a session ID is not found or its store cannot be
read. `list` prints a warning to stderr for unavailable sources and exits 0.

### Step 4: Continue from a session

Use `context <session-id>` to build a continuation prompt. This extracts:
1. The latest compaction summary (away summary) when one exists
2. Recent reasoning summaries (when no summary exists)
3. All user prompts in order (the conversation arc)
4. The last 5 assistant responses (recent context)

Present the output to the user so they can formulate a continuation prompt
for a new opencode session. Do NOT automatically start a new session or
assume the user wants to continue — ask first.

### Step 5: Subagent transcripts

- **Claude Code**: subagent transcripts are stored in
  `~/.claude/projects/<encoded-cwd>/<session-id>/subagents/`. The `summary`
  command lists subagent metadata. To inspect subagent transcripts in detail,
  read the `agent-*.jsonl` files directly — they use the same JSONL format as
  the main session.
- **Codex**: subagents are listed inline from `world_state` events (agent ID
  and nickname). Subagent rollouts can also exist as separate rollout files;
  these are listed as their own sessions titled `subagent <nickname>`.
- **opencode**: subagent sessions are stored as child rows in the session
  table (linked via `parent_id`). The `summary` command lists them; use
  `timeline` or `summary` with the child session ID to inspect them.
- **DeepSeek harness**: subagent children are separate session directories
  whose header carries `parentSession` with `origin: "subagent"` (or a
  positive `delegationDepth`); the parent's `subagent/catalog` events give
  each child's name, mode, and label. The `summary` command lists them;
  use `timeline` or `summary` with the child session ID to inspect them.
