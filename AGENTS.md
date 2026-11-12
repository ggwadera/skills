# Agent instructions

This repository hosts personal Agent Skills. Each skill is a self-contained
directory under `skills/`, follows the Agent Skills format
(https://agentskills.io/specification), and is installable with the skills CLI
(https://www.skills.sh/docs).

## Layout

```
skills/
  <skill-name>/
    SKILL.md          # required: YAML frontmatter plus instructions
    README.md         # optional: human-facing documentation
    LICENSE           # optional: skill-specific license
    <scripts, references, assets>
```

- One directory per skill. The directory name is the skill name.
- Discovery uses the standard `skills/` container. Do not add a
  `skills.sh.json` unless skills must live outside `skills/`.
- Do not add a `SKILL.md` at the repository root; it shadows nested skills.

## SKILL.md frontmatter

Use only these fields:

- `name` — required. 1-64 characters, lowercase letters, digits, and hyphens.
  No leading or trailing hyphen, no consecutive hyphens. Must equal the parent
  directory name.
- `description` — required. At most 1024 characters. State what the skill does
  and when to use it, including keywords agents match on.
- `license` — optional. Short license name or a reference to a bundled file.
- `compatibility` — optional, at most 500 characters. Only for real
  environment requirements such as runtime version or required data.
- `metadata` — optional map of string keys to string values, for example
  `author` and `version`.

## Content rules

- Keep `SKILL.md` under 500 lines. Move detailed material into `references/`.
- Reference other files with paths relative to the skill root, one level deep
  where possible.
- Scripts must work when run from the skill directory and by absolute path.
  Never depend on the caller's working directory.
- Prefer Python 3.9+ with the standard library only. Document any other
  dependency in `compatibility`.
- Name scripts and files with lowercase words and hyphens.

## Customization

- A skill may ship its own README, LICENSE, and tests. Keep them inside the
  skill directory.
- If a skill needs configuration, document defaults and environment variables
  in `SKILL.md`; never hardcode personal paths.

## Validation

Run before committing:

```bash
npx skills add ggwadera/skills -l          # every skill is discovered once
npx skills use ggwadera/skills@<skill>     # SKILL.md resolves end to end
```

If `skills-ref` is available, run `skills-ref validate skills/<skill>`.

## Publish safety

- Never commit secrets, tokens, internal hostnames, company data, or real user
  data. Test fixtures must be synthetic.
- Personal attribution is limited to the MIT copyright line and
  `metadata.author`.
- `.gitignore` covers `__pycache__/`, `*.pyc`, and `.DS_Store`. Do not commit
  build artifacts.

## Git

- Stage explicit paths; never use `git add .`.
- Commit only files related to the change.
- Do not change global git configuration; the repository-local identity is
  already set.
- Match the concise message style of the existing history.

## Adding a skill

1. Create `skills/<name>/SKILL.md` with valid frontmatter.
2. Add scripts and references beside `SKILL.md`, using relative paths.
3. Add a `README.md` when human-facing documentation helps.
4. Add a row to the skills table in the root `README.md`.
5. Validate discovery and end-to-end use.
6. Commit the new skill files only.
