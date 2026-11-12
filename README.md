# skills

[![skills.sh](https://skills.sh/b/ggwadera/skills)](https://skills.sh/ggwadera/skills)

Personal agent skills, packaged in the [Agent Skills](https://agentskills.io)
format and installable with the [skills](https://www.skills.sh/docs) CLI.

## Install

```bash
npx skills add ggwadera/skills
```

Install one skill:

```bash
npx skills add ggwadera/skills --skill coding-sessions
```

## Skills

| Skill | Description |
| ----- | ----------- |
| [coding-sessions](skills/coding-sessions) | Inspect and continue from past Claude Code, Codex, and opencode session transcripts. |

## Layout

Each skill is a directory under `skills/` containing a `SKILL.md` with YAML
frontmatter and any scripts or references it needs.
