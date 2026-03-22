# Installing clawEASA for OpenClaw users

This repository contains two related pieces:

1. the **Python runtime** (`src/claw_easa`, dependencies, CLI), and
2. the **OpenClaw skill package** (`skill/claw-easa/`).

Installing the skill alone is **not enough**. The skill tells OpenClaw when and how to use
`claw-easa`, but the local machine still needs the Python runtime and CLI installed.

## Recommended installation flow (GitHub → OpenClaw)

```bash
git clone https://github.com/dmoraine/clawEASA.git
cd clawEASA
./scripts/bootstrap-local-runtime.sh
./scripts/install-openclaw-skill.sh
./scripts/check-openclaw-runtime.sh
```

This does four things:
- creates a local virtualenv if needed,
- installs a **CPU-only** torch build by default to avoid pulling huge CUDA wheels on typical OpenClaw hosts,
- installs the Python package and CLI,
- installs the OpenClaw skill into `~/.openclaw/workspace/skills/claw-easa/`,
- verifies that OpenClaw can see the skill files and that the CLI responds.

## First ingestion test

From the repository root:

```bash
. .venv/bin/activate
python -m claw_easa.cli init
python -m claw_easa.cli ingest fetch air-ops
python -m claw_easa.cli ingest parse air-ops
python -m claw_easa.cli lookup ORO.FTL.110
```

Expected result:
- `fetch` downloads a ZIP archive from EASA,
- `parse` extracts the XML and persists the regulation hierarchy,
- `lookup ORO.FTL.110` returns the IR text for operator responsibilities.

## Why the repository and the skill are separate

The OpenClaw skill package under `skill/claw-easa/` contains:
- `SKILL.md`
- lightweight reference files

It intentionally does **not** duplicate the full Python source tree. The repository root remains
source-of-truth for development, packaging, tests, and runtime installation.

## Local paths used by default

- repository runtime: `./.venv`
- OpenClaw skill install: `~/.openclaw/workspace/skills/claw-easa/`
- data dir (default): `./data/`

## Useful overrides

### Custom virtualenv path

```bash
CLAW_EASA_VENV_DIR=/opt/claw-easa-venv ./scripts/bootstrap-local-runtime.sh
```

### Custom Python binary

```bash
CLAW_EASA_PYTHON_BIN=python3.12 ./scripts/bootstrap-local-runtime.sh
```

### Custom pip arguments

Default bootstrap uses `--no-cache-dir`. Override if needed:

```bash
CLAW_EASA_PIP_ARGS="" ./scripts/bootstrap-local-runtime.sh
```

### Install skill somewhere else for testing

```bash
OPENCLAW_SKILL_DST=/tmp/claw-easa-skill ./scripts/install-openclaw-skill.sh
```

### Healthcheck against a non-default skill location

```bash
OPENCLAW_SKILL_DIR=/tmp/claw-easa-skill ./scripts/check-openclaw-runtime.sh
```

## Publishing note

This repository is ready for GitHub-based installation. If you want true one-command user
installation from the OpenClaw ecosystem, the next step is packaging/publishing through ClawHub.
eady for GitHub-based installation. If you want true one-command user
installation from the OpenClaw ecosystem, the next step is packaging/publishing through ClawHub.
