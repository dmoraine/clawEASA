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
python -m claw_easa.cli ear-discover       # list EARs available on EASA website
python -m claw_easa.cli ear-list           # list built-in known sources
python -m claw_easa.cli ingest fetch air-ops   # download ZIP archive
python -m claw_easa.cli ingest parse air-ops   # extract XML + parse
python -m claw_easa.cli lookup ORO.FTL.110
python -m claw_easa.cli refs "split duty"
python -m claw_easa.cli snippets "fatigue management"
python -m claw_easa.cli ask "What are the operator responsibilities for FTL?"
```

## Source format

EASA distributes Easy Access Rules as ZIP archives containing a flat Office Open XML file.
The ingestion pipeline handles extraction automatically: `ingest fetch` downloads the archive,
and `ingest parse` extracts the XML before parsing it into the regulatory hierarchy.

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
