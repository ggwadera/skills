---
name: self-reflect
description: Analyse past coding-agent sessions (opencode, Codex, and Claude Code) to improve instruction files, update skills, and find new skill opportunities that raise one-shot success rates. Use when the user asks to reflect on past sessions, review what went wrong or took too many steps, improve an AGENTS.md or CLAUDE.md file, or optimise their agent configuration. Depends on the coding-sessions skill for transcript extraction.
license: MIT
compatibility: Requires the coding-sessions skill installed as a sibling directory, because this skill runs ../coding-sessions/session_extract.py (Python 3.9 or newer, standard library only) relative to its own root. Claude Code reads ~/.claude/skills, so both skills must also be present there.
metadata:
  author: ggwadera
  version: "1.0.0"
---

# Self-Reflection

Turn past session transcripts into concrete changes to instruction files,
skills, and agent configuration.

## When to use

- The user asks to reflect on past sessions or review a session history
- One-shot success rate is low: tasks take several corrections to finish
- The user asks why a task took many tool calls or went off track
- An instruction file needs evidence-based additions
- A skill triggered late or gave incomplete guidance
- The same workflow keeps repeating across sessions

## Prerequisites

This skill has no scripts of its own. All extraction comes from the
`coding-sessions` skill.

Both skills must sit side by side in the same skills directory. The script is
resolved from this skill's own root, never from the caller's working
directory:

```bash
python3 <skill-root>/../coding-sessions/session_extract.py list --stats --dir <project>
```

Install them together:

```bash
npx skills add ggwadera/skills --skill self-reflect --skill coding-sessions
```

If the sibling directory is missing, the command fails with a file-not-found
error. Report that to the user instead of parsing transcripts by hand.

Never read raw JSONL, SQLite, or `~/.codex/sessions/` files directly. The
store formats are large and version-dependent; the script handles them.

## Read the numbers correctly

`list --stats` returns these columns:

| Column | Meaning |
| --- | --- |
| `Prompts` | Real user prompts. 1 means one attempt; 2-3 minor steering; 4 or more significant iteration |
| `Tools` | Assistant tool calls. High counts relative to task size mean excess exploration or failed attempts |
| `Lines` | Raw transcript events or messages, a rough complexity proxy, not a quality signal |
| `Cmp` | Compaction boundaries. Compaction means the task generated more context than the session could hold |
| `Tok in` | Prompt tokens including cache reads. Cross-check with `Cmp` before treating it as bloat |
| `Tok out` | Completion tokens |
| `Cost` | Session cost. opencode only; a dash means the source does not persist it |
| `Active` | Agent reply time. Idle gaps between user turns are excluded, so it measures work, not wall clock |

Dashes are missing data, not zeros. Never sum a dash into a total and never
treat a zero cost as a free session.

## Which agent owns which files

Both opencode and Codex read the shared `~/.agents` tree. Claude Code is
separate.

| Agent | Instruction files | Skills directory | Config |
| --- | --- | --- | --- |
| opencode | `AGENTS.md` in project and parent directories; `~/.config/opencode/AGENTS.md` | `~/.agents/skills` | `opencode.json` |
| Codex | `AGENTS.md` in project and parent directories; `~/.codex/AGENTS.md` | `~/.agents/skills` | `~/.codex/config.toml` |
| Claude Code | `CLAUDE.md` in project and parent directories; `~/.claude/CLAUDE.md` | `~/.claude/skills` | `.claude/settings.json` |

The `Source` column in the listing tells you which agent produced a session.
A recommendation must target that agent's files. Do not tell a Codex user to
edit `opencode.json`, and do not assume the current agent is the one that ran
the session.

## Workflow

### Step 1: List sessions

```bash
python3 <skill-root>/../coding-sessions/session_extract.py list --stats --dir <project> --limit 30
```

`list` defaults to `$PWD`. Always pass `--dir <project>` when analysing a
project that is not the current directory, or `--all` to survey everything.

Add `--source claude`, `--source codex`, or `--source opencode` to narrow the
scan. Add `--grep <text>` to find sessions by ticket ID, feature, or prompt
text.

### Step 2: Classify sessions

Sort into three buckets:

1. **One-shot successes**: 1-2 prompts, completed, moderate tool calls. Record
   what context made them work.
2. **Multi-iteration sessions**: 3 or more prompts, or 1 prompt with many tool
   calls. These are the analysis targets.
3. **Abandoned or stuck**: no tool calls, or a large tool count with almost no
   output. These failed outright.

Before classifying a session as stuck, check `Active` against the timestamp
span. A long wall clock with a short `Active` is idle time, not a stuck agent.

### Step 3: Read the failures

```bash
python3 <skill-root>/../coding-sessions/session_extract.py summary <session-id>
python3 <skill-root>/../coding-sessions/session_extract.py timeline <session-id> --tail 100
```

Start with `summary`. Use `timeline` only when you need the actual reasoning
or tool arguments. Look for:

- **Repeated failed tool calls**: the same search pattern tried with different
  spelling, or repeated wrong globs. Usually missing layout context in the
  instruction file.
- **User corrections**: a follow-up that contradicts the agent's direction.
  The first prompt's intent was ambiguous or the agent lacked a convention.
- **Excessive exploration**: many file reads before the first change. A layout
  and conventions section would have removed most of them.
- **Wrong assumptions**: a library, pattern, or config that does not exist in
  the codebase. This is a direct instruction-file gap.
- **Late skill discovery**: manual work that an installed skill would have
  covered. The skill's trigger description lacks the relevant keywords.
- **Compaction and cache-heavy turns**: high `Cmp` or `Tok in` without matching
  tool work. The agent re-read the same context instead of narrowing it.

### Step 4: Check existing configuration

1. Read the instruction files the analysed agent actually loads, at project
   level and user level, using the table above.
2. List installed skills in that agent's skills directory to avoid proposing a
   duplicate. Both opencode and Codex use `~/.agents/skills`.
3. Read the relevant `SKILL.md` descriptions before proposing changes to them.
4. Note which skill source each directory belongs to (`~/.agents/.skill-lock.json`
   records repository-installed skills) so you do not propose editing a
   vendored copy that an update would overwrite.

### Step 5: Build the report

Every recommendation needs evidence. Use this shape:

**A. Instruction-file changes**

- Evidence: session ID and the observable cost (for example, 12 tool calls
  spent locating the rejection flow).
- Change: the exact section or paragraph to add.
- Expected effect: what gets cheaper next time.

**B. Skill changes**

- Evidence: session ID and the point where the skill should have triggered.
- Change: description keywords, or instructions in the body.
- Expected effect: earlier activation, fewer manual steps.

**C. New skills**

- Evidence: the session IDs that repeat the same workflow.
- Proposal: name, description, scope.
- Expected effect: steps and tool calls saved per session.

### Step 6: Stop for approval

Present the report and stop. Do not edit any file before the user approves
specific items. Applying unapproved changes creates rework and can break
working configuration.

### Step 7: Apply only what was approved

- Instruction files: read first, then edit, keeping the existing format and
  tone.
- Skills: edit the `SKILL.md` description or body. Keep frontmatter inside the
  schema that the target agent accepts.
- New skills: create `<skills-dir>/<name>/SKILL.md` with valid frontmatter.

Then tell the user to restart the target agent or start a new session,
because skills and instruction files are read at session start. State which
agent must restart; it is not necessarily the one running this analysis.

## Limitations

- Cost exists only for opencode sessions. Do not estimate it from tokens.
- Claude Code transcripts without `turn_duration` events report no active
  time, so older sessions show a dash.
- Codex token totals can come from a context-window estimate after
  compaction; treat them as approximate for accounting, valid for comparison.
- A session that the user abandoned for reasons unrelated to the agent is not
  evidence of a configuration gap. Ask before drawing conclusions from it.
