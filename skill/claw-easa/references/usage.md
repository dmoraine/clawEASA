# claw-easa skill usage

## Repository layout

This repository is both:
- a normal Python project (`src/`, `tests/`, `pyproject.toml`), and
- an OpenClaw AgentSkill package under `skill/claw-easa/`.

The skill package is the installable unit for OpenClaw. The repository root is the development source of truth.

## Typical commands

```bash
# from repository root
python -m claw_easa.cli status
python -m claw_easa.cli lookup ORO.FTL.110
python -m claw_easa.cli refs "split duty"
python -m claw_easa.cli snippets "fatigue management"
python -m claw_easa.cli ask "What are the operator responsibilities for FTL?"
```

## Installing the skill locally for OpenClaw

Copy the packaged skill directory into the OpenClaw workspace:

```bash
mkdir -p ~/.openclaw/workspace/skills/claw-easa
rsync -a --delete skill/claw-easa/ ~/.openclaw/workspace/skills/claw-easa/
```

Or use the guarded helper script:

```bash
./scripts/install-openclaw-skill.sh
```

For a non-default destination during testing:

```bash
OPENCLAW_SKILL_DST=/tmp/claw-easa-skill ./scripts/install-openclaw-skill.sh
```

Do not install via a symlink that points outside the workspace.
