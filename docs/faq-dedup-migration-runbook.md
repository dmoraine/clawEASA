# FAQ dedup — migration runbook (`005_faq_stable_ids`)

Operator procedure to apply, verify and, if needed, roll back migration `005_faq_stable_ids` on a
real data directory. Design: `docs/faq-dedup-design.md` (section 6). The numbers below were
measured on a fresh copy of the 2026-11-26 snapshot (7 630 entries, 2 722 FAQ rows, 192 FAQ
domains, FAISS 13 033 chunks) with the exact commands shown; the data directory in the examples is
the rehearsal copy, so replace `$DATA` for the real run.

## 0. What the migration does and does not do

- Adds a content hash, a stable id `FAQ-{slug}-{hash8}` (ordinal suffix for repeated text inside one
  domain), a legacy alias for every old `FAQ-{slug}-NNN`, and FAQ groups (one per distinct
  normalised question+answer text).
- Deletes nothing: all FAQ rows stay (2 722 before and after). Question and answer text are not
  modified.
- **The FAISS index is not touched and must not be rebuilt.** Vectors and `faiss_mapping` are
  unchanged; only the leading ref in `entry_chunks.chunk_text` of FAQ chunks is rewritten (text,
  not vectors). Prove it with the sha256 of `claw_easa.faiss` before and after (step 6).
- Needs exclusive use of the database: no other `claw-easa` process during apply/rollback.
- Runtime: each of apply and rollback is a single SQLite transaction on a 54 MB database
  (not timed separately in the rehearsal; the whole rehearsal script, including two model-loading
  canary runs, finished in a few minutes).

### Bootstrap side effect to expect before you start

`MigrationRunner.init_schema()` runs `schema.sql` (`CREATE ... IF NOT EXISTS`) every time the new
code opens a database, **without** recording migration 005. As soon as any command of the
feature branch (even `lookup`) touches a database, the three **empty** tables `faq_groups`,
`faq_entry_groups` and `faq_aliases` appear. This is expected, inert (no rows, no data change) and
tolerated by `plan` and `apply`:

- `plan` prints `faq_tables_present` (0–3) and `faq_tables_rows`; empty tables are fine.
- If one of the tables holds rows while 005 is not recorded, `plan` fails with
  `faq_tables_not_empty` and `apply` refuses (stop condition).
- `rollback` empties the three tables but **keeps** them (they are part of the schema). Dropping
  them is a manual decision, not part of this procedure.

Always pass `--data-dir` (every `db faq-ids` command requires it, with no environment fallback).
Other commands (`lookup`, `hybrid`, ...) must be run with `CLAW_EASA_DATA_DIR=$DATA`.

## 1. Preflight

```bash
export DATA=~/.local/share/claw-easa            # real data dir (rehearsal: a copy)
export BK=~/claw-easa-backups/faq-dedup-$(date +%Y%m%d)   # backup directory, must not be inside $DATA
mkdir -p "$BK" && chmod 700 "$BK"
df -h "$BK"                                      # need > 2x the size of claw_easa.db (about 54 MB)
sha256sum "$DATA/claw_easa.faiss" | tee "$BK/faiss.sha256"     # FAISS reference
cp -a "$DATA/claw_easa.faiss" "$BK/claw_easa.faiss.copy"       # belt and braces
```

Stop if another claw-easa process is running (`pgrep -af claw-easa`).

On the real data dir the CLI asks for two explicit confirmations: `--allow-default-data-dir` and,
for the commands that open the database for writing (`apply`, `rollback`, `verify`), a typed
confirmation of the full database path on a terminal (it refuses without a TTY). `plan` and
`backup` open the source read-only. Add `--allow-default-data-dir` to every command below when
`$DATA` is the default directory.

## 2. Plan (dry run, read-only)

```bash
claw-easa db faq-ids plan --data-dir "$DATA"
```

Expected on the snapshot (rehearsal output):

```
faq_rows: 2722        domains: 192          groups: 1609        redundant_copies: 1113
multi_domain_groups: 782    aliases_to_create: 2722    entry_refs_to_rewrite: 2722
ordinal_suffixed: 6   faq_chunks_to_update: 2807   stable_id_collisions: 0   hash8_collisions: 0
legacy_pattern_mismatch: 0   duplicate_faq_refs: 0   already_applied: no
faq_tables_present: 0 (or 3 after the bootstrap side effect)   faq_tables_rows: 0   would_apply: yes
```

(One `key: value` per line in the real output.) Exit code 0 = go. The real database may have
grown since the snapshot (ingestion): then `faq_rows` and `groups` differ; what must hold is
`stable_id_collisions: 0`, `hash8_collisions: 0`, `legacy_pattern_mismatch: 0`,
`duplicate_faq_refs: 0`, `faq_tables_rows: 0` and `would_apply: yes`.

**Stop conditions:** exit code 1 or any `precheck_failed:` line; `faq_tables_rows` > 0;
`already_applied: yes` unexpectedly.

## 3. Baseline: verify, backup, canary BEFORE

```bash
claw-easa db faq-ids verify --data-dir "$DATA" --ref-prefix ORO.FTL
claw-easa db faq-ids backup --data-dir "$DATA" --backup-to "$BK/pre-005.db"
CLAW_EASA_S2_SNAPSHOT_COPY="$DATA" python scripts/faq_s2_snapshot_check.py   # canary BEFORE
```

Expected `verify` (rehearsal): `applied: no`, `faq_rows: 2722`, `integrity_check: ok`,
`fts_integrity: ok`, `faq_groups: 0`, `faq_aliases: 0`, `faq_entries_ungrouped: 2722`,
`aliases_unresolved: 0`, `ref_prefix_faq_rows: 158`, `ref_prefix_groups: 158`, and fingerprints:

```
immutable_fingerprint:   90b064c990fccee74f5a1a62dd7b8b0f15394fe089a5c67b6216bb88e58a07dc
restorable_fingerprint:  cf2741c1c8cd35fe38f99b5c790107216fd7072de3347d6787153ebeef3a7729
faiss_sha256:            491c16a18fa54a2eccf64d77c00533d0a8c65bcb0540d3156caa26ab4022262e
```

(The fingerprints and the FAISS hash are specific to the snapshot; on the real DB they are your
own reference values. Record them.) `backup` prints `backup_sha256`, re-opens the backup read-only
and prints its `integrity_check` (must be `ok`) and the same two fingerprints, which must equal
those of `verify` above. Rehearsal backup sha256: `2b285fff...41f6` (file-level, changes per run).

The canary BEFORE is the reference query `FDP extension commander discretion fatigue` (hybrid,
top-10). Expected on an unmigrated database (exit code 1 is the expected "duplicates present"):
8 FAQ + 2 non-FAQ results, 5 duplicated texts, ORO.FTL FAQ rows 158 / groups 0.

**Stop conditions:** `integrity_check` is not `ok`; backup fingerprints differ from `verify`;
backup file missing or empty.

## 4. Apply

```bash
claw-easa db faq-ids apply --data-dir "$DATA" --backup-to "$BK/apply-backup.db"
```

`apply` takes its own verified backup first (the target must not exist), then migrates in one
transaction (all-or-nothing). Expected: `backup: <path>`, `already_applied: no`, `applied: yes`.
If 005 is already recorded, it prints `backup: none`, `already_applied: yes`, `applied: yes` and
changes nothing (idempotent; verified by a second run in the rehearsal).

## 5. Verify and canary AFTER

```bash
claw-easa db faq-ids verify --data-dir "$DATA" --ref-prefix ORO.FTL --against "$BK/pre-005.db"
CLAW_EASA_S2_SNAPSHOT_COPY="$DATA" python scripts/faq_s2_snapshot_check.py   # canary AFTER
for r in FAQ-oroftl-028 FAQ-air-operations-057 FAQ-part-oro-036; do
  CLAW_EASA_DATA_DIR="$DATA" claw-easa lookup "$r" --json | jq -c '.matches[0] | {entry_ref, canonical_ref, is_canonical, resolved_from_alias, group_size}'
done
```

Expected (rehearsal): `applied: yes`, `faq_rows: 2722` (unchanged), `faq_groups: 1609`,
`faq_aliases: 2722`, `faq_entries_ungrouped: 0`, `aliases_unresolved: 0`,
`integrity_check: ok`, `fts_integrity: ok`, `ref_prefix_faq_rows: 158`, `ref_prefix_groups: 53`,
`against_immutable_match: yes`, `against_restorable_match: n/a` (refs legitimately changed),
`immutable_fingerprint` equal to the BEFORE value, `faiss_sha256` equal to the BEFORE value,
`restorable_fingerprint: 611732ba1b4f02b13421c16bf03fd2164f4f0a4101b351a8832b5fdc943afdac`.

Canary AFTER: ORO.FTL FAQ rows 158 → groups 53 → 53 visible after collapse; the hybrid top-10 holds
4 FAQ + 6 non-FAQ results (IR/AMC/GM no longer evicted), 0 duplicated texts, exit code 0.
The three reference refs each resolve to their own copy, which reports `canonical_ref`
`FAQ-oroftl-590f5a9e`, `group_size: 3`, `resolved_from_alias` set to the old ref
(`is_canonical` true only for the oroftl copy).

**Stop conditions (then roll back, section 6):** `faq_rows` differs from the planned count;
`immutable_fingerprint` differs from BEFORE (verify exits 1 on `against_immutable_match: no`);
`aliases_unresolved` or `faq_entries_ungrouped` > 0; `integrity_check`/`fts_integrity` not `ok`;
`faiss_sha256` differs; canary shows duplicated texts or a lookup of an old ref fails.

## 6. Rollback

Preconditions: 005 recorded and no FAQ row ingested after the migration (those cannot be restored
positionally; `rollback` refuses and prints their count unless `--force`, which loses only those
rows' positional refs, never rows).

```bash
claw-easa db faq-ids rollback --data-dir "$DATA" --against "$BK/pre-005.db"
claw-easa db faq-ids verify  --data-dir "$DATA" --ref-prefix ORO.FTL --against "$BK/pre-005.db"
sha256sum "$DATA/claw_easa.faiss"; cat "$BK/faiss.sha256"
```

Expected (rehearsal): `rolled_back: yes`, `against_immutable_match: yes`,
`against_restorable_match: yes`; `verify`: `applied: no`, `faq_rows: 2722`, both fingerprints equal
the BEFORE values, `faq_groups: 0`, `faq_aliases: 0`, `ref_prefix_groups: 158`, FAISS sha256
unchanged. The empty tables remain. A mismatch makes the command exit 1 (the rollback is already
committed in that case: restore from the backup, see below).

Last resort (rollback itself failing): stop all claw-easa processes and copy
`$BK/pre-005.db` over `$DATA/claw_easa.db` (remove `claw_easa.db-wal`/`-shm` first); the FAISS file
needs nothing because it was never modified.

## 7. Rehearsal log (copy `work-s3` of the snapshot, this branch)

Sequence executed with the commands above: plan → verify → backup → canary BEFORE → apply →
verify `--against` → canary AFTER → 3 alias lookups → second apply (no-op, `backup: none`) →
rollback `--against` → verify `--against` → apply again → verify `--against`.

| Check | Result |
|---|---|
| FAQ rows before / after apply / after rollback / after re-apply | 2 722 / 2 722 / 2 722 / 2 722 |
| groups after apply (re-apply) | 1 609 (1 609); 0 after rollback |
| aliases after apply (re-apply) | 2 722 (2 722); 0 after rollback |
| immutable fingerprint (all four states) | identical (`90b064c9...07dc`) |
| restorable fingerprint after rollback vs before | identical (`cf2741c1...7729`) |
| ORO.FTL FAQ rows / groups | 158 / 158 before, 158 / 53 after, 158 / 158 after rollback |
| `integrity_check`, `fts_integrity` | ok in every state |
| FAISS sha256 before / after (apply, rollback, re-apply) | identical (`491c16a1...262e`) |
| hybrid top-10 reference query | 8 FAQ + 2 non-FAQ, 5 duplicated texts → 4 FAQ + 6 non-FAQ, 0 duplicated texts |
| snapshot directory `sha256sum -c SHA256SUMS` | OK |

## 8. After the migration

- Re-ingestion is guarded: `ingest` refuses to run on a database with FAQ rows but without 005.
- Old citations keep working through aliases; new citations should use the stable id
  (`FAQ-{slug}-{hash8}`), which does not change when EASA reorders its pages.
- No FAISS rebuild is needed or wanted.
