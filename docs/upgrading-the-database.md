# Upgrading the database after updating claw-easa

> **TL;DR** — After updating claw-easa, an **existing** database that holds FAQ rows must be
> migrated **once** (`005_faq_stable_ids`). Until then everything reads as before, but
> `ingest faq` and `ingest faq-all` are **blocked**. The migration is done in place with
> `claw-easa db faq-ids …`: no re-download, **no FAISS rebuild**, a few minutes.
> Check where you stand with one read-only command:
> `claw-easa db faq-ids plan --data-dir DIR` (or `claw-easa status`, last line).

Audience: operators and the agents connected to this repository. Full operator procedure and
rehearsal evidence: [faq-dedup-migration-runbook.md](faq-dedup-migration-runbook.md). Design:
[faq-dedup-design.md](faq-dedup-design.md).

## 1. Which situation are you in?

Read-only check (changes nothing, opens the database read-only):

```bash
claw-easa db faq-ids plan --data-dir DIR      # DIR holds claw_easa.db; --data-dir is mandatory
claw-easa status                              # last line: "FAQ ids: ..." (uses CLAW_EASA_DATA_DIR)
```

Look at `already_applied`, `faq_rows` and `would_apply` in the `plan` output.

| | Situation | `plan` shows | What to do |
|---|---|---|---|
| **A** | Existing database **with FAQ rows**, not migrated | `already_applied: no`, `faq_rows` > 0, `would_apply: yes` | **Migrate in place** (section 2). Recommended: no re-download, no FAISS rebuild. |
| **B** | Existing database **already migrated** | `already_applied: yes`, `would_apply: no` | Nothing to do. |
| **C** | Existing database with EAR sources but **no FAQ rows** | `already_applied: no`, `faq_rows: 0` | Nothing to migrate. The first `ingest faq` / `ingest faq-all` records 005 automatically. |
| **D** | Empty / new data directory, or deliberate full rebuild from scratch | (no database yet) | Build from scratch (section 2b). |

`claw-easa status` prints one extra line when the database has FAQ rows:
`FAQ ids:         migrated (005)` or `FAQ ids:         NOT migrated — run 'claw-easa db faq-ids plan --data-dir <DIR>' (see docs/upgrading-the-database.md)`.
It is read-only (the database is opened `mode=ro`) and prints nothing when there are no FAQ rows.

## 2. Case A — migrate in place (checklist)

Full commands, expected values and stop conditions: [runbook](faq-dedup-migration-runbook.md)
sections 1–6. Short form (replace `DIR`; `BK` is a backup directory **outside** `DIR`):

- [ ] Stop every other `claw-easa` process (`pgrep -af claw-easa`); the migration needs exclusive use.
- [ ] `mkdir -p "$BK" && chmod 700 "$BK"` and check free space (more than twice the size of `claw_easa.db`).
- [ ] Record the FAISS hash: `sha256sum DIR/claw_easa.faiss | tee "$BK/faiss.sha256"`.
- [ ] `claw-easa db faq-ids plan --data-dir DIR` — go only if exit code 0, no `precheck_failed:` line and `would_apply: yes`.
- [ ] `claw-easa db faq-ids backup --data-dir DIR --backup-to "$BK/pre-005.db"` (verified SQLite backup).
- [ ] `claw-easa db faq-ids apply --data-dir DIR --backup-to "$BK/apply-backup.db"` (takes its own backup first, then one all-or-nothing transaction; prints `applied: yes`).
- [ ] `claw-easa db faq-ids verify --data-dir DIR --against "$BK/pre-005.db"` — expect `against_immutable_match: yes`, `aliases_unresolved: 0`, `faq_entries_ungrouped: 0`, `integrity_check: ok`, same `faiss_sha256` as before.
- [ ] Canary: `CLAW_EASA_DATA_DIR=DIR claw-easa lookup FAQ-<slug>-NNN --json` on an old ref still resolves (`resolved_from_alias` set), and `claw-easa status` ends with `FAQ ids:         migrated (005)`.
- [ ] If anything is off: roll back (section 6).

Facts to keep in mind:

- The **FAISS index is not rebuilt or modified**. Only the leading text prefix of FAQ chunks in
  `entry_chunks.chunk_text` changes; vectors and `faiss_mapping` stay as they are.
- Nothing is deleted: every FAQ row keeps its own row. Old `FAQ-<slug>-NNN` refs keep resolving as aliases.
- `--data-dir` is **mandatory** on every `db faq-ids` command (no environment fallback).
- On the **default** data directory (`~/.local/share/claw-easa`) the CLI additionally needs
  `--allow-default-data-dir`, and the commands that write (`apply`, `rollback`) ask for a typed
  confirmation of the full database path **on a terminal** (they refuse without a TTY).

## 2b. Case D — build from scratch

Use this for a new machine, or when you deliberately discard the old data directory. The
commands (network steps need Internet access to easa.europa.eu):

```bash
export CLAW_EASA_DATA_DIR=/path/to/new/data-dir    # optional; default is ~/.local/share/claw-easa
claw-easa init
for s in air-ops aircrew basic-regulation occurrence-reporting; do   # the EAR sources you need (see `claw-easa ear-list`)
  claw-easa ingest fetch "$s" && claw-easa ingest parse "$s"
done
claw-easa ingest faq-all                           # all FAQ domains
claw-easa index build                              # REQUIRED in this path (chunks + embeddings + FAISS)
claw-easa status
```

- FAQ ids are **stable from the first ingestion**: on a database with no FAQ rows,
  `ingest faq*` records migration 005 automatically (test
  `test_first_ingestion_on_empty_db_records_the_migration` in `tests/test_faq_ingest_stable_ids.py`).
  There is nothing to migrate afterwards.
- `index build` is required here, because there is no FAISS file yet. Its duration and memory
  use are **not measured** (the "about 15 min" figure in circulation is marked UNVERIFIED in the
  [design note](faq-dedup-design.md), table row 15).
- Starting from scratch is only worth it if you want fresh data; for a populated database
  case A is faster and keeps the existing index.

## 3. What changes for users after migration

- Every FAQ has a stable id `FAQ-<slug>-<hash8>` (content based, unaffected when EASA reorders pages).
- `refs`, `snippets`, `hybrid` and `ask` show each FAQ text **once**, followed by ` [also in: slugA, slugB]` in text output.
- Old positional refs `FAQ-<slug>-NNN` still resolve (aliases) in `lookup`, `refs`, `snippets`, `hybrid`, `ask`.
- `refs --json` gains `also_in`, `content_hash`, `legacy_refs`, `group_size`; `lookup --json` also gains
  `canonical_ref`, `is_canonical`, `resolved_from_alias`. All additive; existing keys and `schema_version` are unchanged.
- Non-FAQ results are no longer pushed out of the top-k by duplicate FAQ copies.
- Details: README, section "FAQ copies and stable FAQ ids" ([README.md](../README.md)).

## 4. Behaviour before migration (new code, unmigrated database with FAQ rows)

| Command | Behaviour |
|---|---|
| `lookup`, `refs`, `snippets`, `hybrid`, `ask`, `status`, `sources-list` | Work as before; ranking is identical to the old code (every FAQ is its own group). |
| `ingest fetch`, `ingest parse` (EAR sources) | Not affected. |
| `ingest faq`, `ingest faq-all` | **Blocked**; nothing is written. |
| `db faq-ids plan`, `verify`, `backup` | Read-only; available. |

Exact error of the blocked commands (as printed, ending a Python traceback, exit code 1):

```
claw_easa.db.faq_migration.MigrationError: FAQ ingestion is blocked: FAQ rows with positional refs exist but migration 005_faq_stable_ids is not applied (ingesting now would duplicate every FAQ under new ids). Run 'claw-easa db faq-ids plan --data-dir DIR' to review, then 'claw-easa db faq-ids apply --data-dir DIR --backup-to PATH'; ingestion is blocked until the migration is applied; see docs/upgrading-the-database.md.
```

## 5. Troubleshooting

| Message | Meaning / what to do |
|---|---|
| `Error: Missing option '--data-dir'.` (exit 2) | Every `db faq-ids` command needs `--data-dir DIR`; there is no environment fallback. |
| `Error: <dir> is the default data directory; pass --allow-default-data-dir to use it.` | Deliberate guard on the real data directory. Only the operator who owns the machine adds the flag. |
| `<db> is the default database: confirmation must be typed on a terminal.` / `Confirmation did not match the database path; aborted.` | Writes on the default directory need a typed full database path on a TTY. Run from an interactive terminal. |
| `Error: No database at <dir>/claw_easa.db` | Wrong `--data-dir`, or case D (nothing to migrate). |
| `Error: Backup target already exists: <path>` | `--backup-to` must name a file that does not exist yet; choose a new name. |
| `precheck_failed: <name>` + exit 1 (from `plan`), `precheck failed: …` (from `apply`) | A stop condition (for example `stable_id_collisions`, `legacy_pattern_mismatch`, `duplicate_faq_refs`). Do not force anything; keep the output and escalate to the maintainer. |
| `precheck_failed: faq_tables_not_empty` | One of `faq_groups`, `faq_entry_groups`, `faq_aliases` holds rows while 005 is not recorded (partial/foreign state). `apply` refuses. Do not edit tables by hand; restore from a backup or escalate. Empty tables alone are harmless (see below). |
| `FAQ ingestion is blocked: …` | Case A: run the migration (section 2). |
| `N FAQ row(s) have no legacy alias (ingested after the migration); they would keep their stable ids. Use --force to roll back anyway.` | `rollback` refuses because rows were ingested after 005. `--force` rolls back anyway: the old schema is restored and only those post-migration rows keep their stable ids (no row is lost). Operator decision only. |
| `rollback refused, the legacy alias mapping is invalid: …` | Alias data is inconsistent; `rollback` (even with `--force`) changes nothing. Restore from the backup (section 6) and escalate. |
| `note: migration 005 was not applied; nothing was rolled back …` (exit 1) | `rollback --against` on an unmigrated database. Use `verify --against` instead. |
| `The rollback has been committed, but the database does not match <backup>.` | Restore from the backup (section 6). |

Side effect to expect: any command of the new code that opens a database read-write
(for example `lookup`) runs `init_schema()` and creates the three **empty** tables `faq_groups`,
`faq_entry_groups`, `faq_aliases`. They are inert and tolerated by `plan` and `apply`.
`plan`, `verify`, `backup` and `status` open the database read-only and create nothing.

## 6. Rollback, and what is not reversible

```bash
claw-easa db faq-ids rollback --data-dir DIR --against "$BK/pre-005.db"
claw-easa db faq-ids verify   --data-dir DIR --against "$BK/pre-005.db"
```

- Expected: `rolled_back: yes`, `against_immutable_match: yes`, `against_restorable_match: yes`.
- **Not reversible:** FAQ rows ingested **after** the migration cannot get a positional ref back.
  `rollback` refuses (and prints their count) unless `--force`, which keeps their stable ids.
  Nothing is ever deleted.
- The FAISS file is never modified, so rollback never needs an index rebuild.
- The three empty tables remain after a rollback (part of the schema).
- Last resort when `rollback` itself fails: stop all claw-easa processes, remove
  `claw_easa.db-wal` / `-shm`, copy `$BK/pre-005.db` over `DIR/claw_easa.db`
  ([runbook](faq-dedup-migration-runbook.md) section 6).

## 7. Rules for agents

Agents may:

- run `claw-easa status` and `claw-easa db faq-ids plan --data-dir DIR` (read-only);
- run `verify` and other read checks **on a copy** of the data;
- tell the operator which case (A–D) applies and which command comes next.

Agents **must not**, without an explicit instruction from the operator who owns the machine:

- run `apply` or `rollback` on a real data directory; use `--force` or `--allow-default-data-dir`;
- re-ingest FAQs (`ingest faq*`) to "fix" a blocked message, or delete/rebuild the database or FAISS index;
- run the CLI of a development checkout or git worktree against a real data directory. Without
  `CLAW_EASA_DATA_DIR` (or `--data-dir`) pointing at a **copy**, read-write commands such as
  `lookup` run `init_schema()` and silently create the three empty FAQ tables.

For tests in a worktree use `PYTHONPATH=src python -m pytest …`: an editable install resolves
`claw_easa` to the main checkout, not to the worktree.
