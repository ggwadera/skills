# self-reflect

[![skills.sh](https://skills.sh/b/ggwadera/skills)](https://skills.sh/ggwadera/skills)

Analyse past **opencode**, **Codex**, and **Claude Code** sessions, then turn
what went wrong into changes to instruction files, skills, and configuration.

The skill reads the numbers that
[coding-sessions](../coding-sessions) extracts, so it needs that skill
installed as a sibling directory. It ships no scripts of its own.

## What it does

- Classifies sessions into one-shot successes, multi-iteration work, and
  abandoned runs.
- Reads failed sessions for repeated tool calls, user corrections, wrong
  assumptions, late skill discovery, and context bloat.
- Reports instruction-file gaps, skill description gaps, and repeated
  workflows that deserve a new skill, each with session evidence.
- Edits only what the user approves, in the files of the agent that produced
  the session.

## Requirements

- Python 3.9 or newer for the `coding-sessions` script.
- The `coding-sessions` skill in the same skills directory:

```bash
npx skills add ggwadera/skills --skill self-reflect --skill coding-sessions
```

opencode and Codex both read `~/.agents/skills`. Claude Code reads
`~/.claude/skills`, so install or link both skills there as well if you use
Claude Code.

## Usage

Ask the agent to reflect on past sessions for a project. It runs:

```bash
python3 <skill-root>/../coding-sessions/session_extract.py list --stats --dir <project>
```

and then summarises and inspects the sessions that needed the most
iteration.

`Active` in that listing is agent reply time with idle gaps excluded. `Cost`
is available for opencode sessions only, because Codex and Claude Code do not
persist it.

## License

MIT. See `LICENSE`.
