# coding-sessions

Inspect and continue from past **Claude Code**, **Codex**, and **opencode**
session transcripts.

The tool reads the native session stores of each coding agent and renders
them in a common format: session lists, one-page summaries, full timelines,
compaction boundaries, and continuation context that can be pasted into a new
session.

## Features

- **One CLI, three sources.** Auto-detects which agent produced a session ID.
- **Session discovery.** List sessions per directory or across all
  directories, with prompt and tool-call counts.
- **Text search.** Find sessions by ticket ID, feature name, or any text from
  a user prompt (`--grep` scans all sessions, not only the first page).
- **Summaries.** Metadata, model, timestamps, cost, tokens, user prompts,
  edited files, subagents, and reasoning summaries.
- **Timelines.** Ordered user prompts, assistant text, tool calls, tool
  results, and compaction events, with `--tail`, `--since`, and `--until`
  paging.
- **Continuation context.** Builds a recap from the latest compaction summary
  plus the conversation arc, for continuing work in a new session.
- **Both opencode schemas.** Reads opencode V1 (`session`, `message`, `part`)
  and V2 (`session_v2`, `session_message`, plus `session_part` when the table
  exists), including databases migrated from V1 to V2.

## Requirements

- Python 3.9 or newer.
- No third-party packages.
- macOS or Linux. The default paths follow the tools' own conventions.

## Install

This skill lives in the `skills` repository under `skills/coding-sessions`.

### As an opencode skill

Clone the repository and copy the skill directory into your opencode skills
folder:

```bash
git clone git@github.com:ggwadera/skills.git
cp -R skills/skills/coding-sessions ~/.config/opencode/skills/coding-sessions
```

opencode loads `SKILL.md` from that directory. Invoke the skill when you ask
about past sessions, session transcripts, or continuation context.

### As a standalone CLI

```bash
git clone git@github.com:ggwadera/skills.git
cd skills/skills/coding-sessions
python3 session_extract.py list
```

No installation step is required. Run the script from its directory; the
`sources` package is imported relative to it.

## Usage

```bash
# List recent sessions from every source
python3 session_extract.py list

# List sessions for one source and directory
python3 session_extract.py list --source opencode --dir /Users/me/myproject
python3 session_extract.py list --source claude --all

# Find sessions by ticket or prompt text
python3 session_extract.py list --grep ABC-1234

# Summarize a session
python3 session_extract.py summary <session-id>
python3 session_extract.py summary <session-id> --source codex --max-prompts 5

# Full timeline, optionally paged
python3 session_extract.py timeline <session-id>
python3 session_extract.py timeline <session-id> --tail 100
python3 session_extract.py timeline <session-id> --since 2026-10-14

# Compaction boundaries
python3 session_extract.py compact <session-id>

# Continuation context
python3 session_extract.py context <session-id>
```

Run `python3 session_extract.py --help` for all commands and options.

## Supported stores

| Source      | Location | Formats |
| ----------- | -------- | ------- |
| Claude Code | `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` | Session JSONL; away summaries; compaction boundaries; subagent metadata |
| Codex       | `~/.codex/sessions/`, `~/.codex/archived_sessions/` | Rollout JSONL, both legacy and paginated histories; `compacted` items; `world_state` subagents; `session_index.jsonl` titles |
| opencode    | `~/.local/share/opencode/opencode.db` | V1 and V2 SQLite schemas; inline and split assistant content |

Environment overrides:

- `CLAUDE_DIR` — root of the Claude Code data directory.
- `CODEX_DIR` — root of the Codex data directory.
- `OPENCODE_DB` — path to the opencode database file.

## Notes and limitations

- Codex reverted sessions leave several rollout files that share a thread ID.
  The newest file is used; duplicates are collapsed by session ID.
- Codex conversations can leave checkpoint rollout files that share one
  payload session ID. The most complete file represents the conversation;
  side transcripts with their own indexed title (for example review tasks)
  are listed separately.
- Codex injected context fragments (environment, instruction files, skill
  prompts, notifications, both approval-assessment transcript variants) are
  excluded from user prompts by marker.
- Codex subagent rollouts can be separate files; they are listed as their own
  sessions rather than folded into the parent conversation.
- Codex `--dir` matches the session's recorded working directory.
- opencode `todo` rows exist only in V1. Migrated databases keep them, so
  todos appear for any session that still has V1 todo rows; V2-only sessions
  have none.
- opencode channel builds can use `opencode-<channel>.db`. Point `OPENCODE_DB`
  at the right file when you do not use the default channel.
- Claude Code project directories encode the working directory by replacing
  non-alphanumeric characters with `-`.
- Claude Code files edited are derived from edit tool calls in the transcript,
  not from the file-history directory.

## Exit status

Detail commands exit 1 when a session ID is not found or its store cannot be
read. `list` prints a warning to stderr for unavailable sources and exits 0.

## License

MIT. See `LICENSE`.
