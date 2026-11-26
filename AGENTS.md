# AGENTS.md — clawEASA

clawEASA is a local EASA regulation search tool (SQLite + FTS5 + FAISS) driven by the
`claw-easa` CLI. This file is for coding agents and for agents that use the CLI on a machine.

## Before you touch a database

The database lives in the **data directory** (default `~/.local/share/claw-easa`, override with
`CLAW_EASA_DATA_DIR`). After updating the code, an **existing database with FAQ rows must be
migrated once** (`005_faq_stable_ids`): until then everything reads as before but
`ingest faq` / `ingest faq-all` are blocked. Full guide: `docs/upgrading-the-database.md`.

- Check the state (read-only): `claw-easa status` (last line `FAQ ids: ...`) or
  `claw-easa db faq-ids plan --data-dir DIR`.
- Migration is done in place (no re-download, **no FAISS rebuild**) by the operator who owns the
  machine, following `docs/faq-dedup-migration-runbook.md`: plan, verified backup, apply, verify,
  canary, rollback ready.

## Rules for agents

You may: run `status`, `plan`, `lookup`, `refs`, `snippets`, `hybrid`, `ask`; run `verify` and any
experiment **on a copy** of the data (`verify` opens the database read-write); tell the operator
which case applies and what comes next.

You must not, without an explicit instruction from the operator who owns the machine:

- run `db faq-ids apply` or `rollback`, or use `--force` / `--allow-default-data-dir`, on a real
  data directory;
- re-ingest FAQs to "fix" a blocked message, or delete or rebuild the database or FAISS index;
- run the CLI of a development checkout or git worktree against a real data directory. Always set
  `CLAW_EASA_DATA_DIR` (or `--data-dir`) to a **copy**: read-write commands run `init_schema()` and
  create empty tables in whatever database they open.

## Development

- Tests: `PYTHONPATH=src python -m pytest -rs -p no:cacheprovider` (the editable install resolves
  `claw_easa` to the main checkout, so `PYTHONPATH=.` tests the wrong code in a worktree).
  Check with `python -c "import claw_easa; print(claw_easa.__file__)"`.
- Lint: `ruff check src tests scripts`; `git diff --check` before committing.
- Tests use synthetic fixtures; never commit EASA corpus text, database files or the FAISS index.
- Language: code, comments, commits and docs in English.
- Do not push to `main`; open a pull request from a branch and wait for CI (Python 3.11 and 3.12).

## Where to read more

- `README.md` — install, ingestion, search, FAQ copies and stable FAQ ids.
- `docs/upgrading-the-database.md` — which situation you are in and what to run.
- `docs/faq-dedup-migration-runbook.md` — operator procedure with expected outputs.
- `docs/faq-dedup-design.md` — design and measurements.
- `skill/claw-easa/` — the OpenClaw skill (agent-facing usage of the CLI).
