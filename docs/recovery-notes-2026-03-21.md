# clawEASA recovery notes — 2026-03-21

## What happened

A local OpenClaw workspace skill symlink pointed from:

- `~/.openclaw/workspace/skills/claw-easa`

to the source repository:

- `/home/openclaw/dev/clawEASA`

A later `rsync --delete` install test targeted the workspace skill path while that symlink was still present, which caused deletion inside the source repository.

## What was recovered with direct evidence

Recovered from local traces or prior verified reads:
- `.gitignore`
- `pyproject.toml`
- `README.md`
- `manifest.json`
- `src/claw_easa/__init__.py`
- `src/claw_easa/cli/__init__.py` (reconstructed minimal placeholder)
- `src/claw_easa/cli/__main__.py`
- `src/claw_easa/ingest/normalize.py` (real recovered code)
- `skill/claw-easa/SKILL.md`
- `skill/claw-easa/references/*`
- `scripts/install-openclaw-skill.sh` (rewritten safely)

## What is not yet recovered

Most original source files under:
- `src/claw_easa/db/`
- `src/claw_easa/retrieval/`
- `src/claw_easa/answering/`
- `tests/`
- `docs/` (except recreated notes)
- `migrations/`

These need either:
1. recovery from other local caches/transcripts if available, or
2. reimplementation.

## Safety fix

The old symlink was removed. Future local install tests must copy into the workspace, not symlink out of it.
