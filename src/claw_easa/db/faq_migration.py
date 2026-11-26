"""Explicit migration 005_faq_stable_ids (docs/faq-dedup-design.md, section 6).

Rewrites positional FAQ refs (``FAQ-{slug}-NNN``) to content-based stable ids
(``FAQ-{slug}-{hash8}``), keeps each old ref as an alias, and fills the derived
FAQ groups.  Never runs implicitly: ``plan`` / ``up`` / ``down`` / ``verify``
are called by ``claw-easa db faq-ids``; ingestion only checks the version.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from claw_easa.config import Settings
from claw_easa.db.sqlite import Database
from claw_easa.ingest.faq_identity import (
    StableIdCollision,
    assign_stable_ids,
    content_hash,
    parse_legacy_ref,
)

VERSION = "005_faq_stable_ids"
LEGACY_KIND = "legacy_positional"
_UNIQUE_INDEX = "uix_faq_entries_ref_doc"
_PRECHECKS = (
    "stable_id_collisions", "hash8_collisions", "legacy_pattern_mismatch", "duplicate_faq_refs",
)
_BACKUP_TABLES = ("regulation_entries", "faq_regulation_refs", "entry_chunks")
_FAQ_TABLES = ("faq_groups", "faq_entry_groups", "faq_aliases")


class MigrationError(RuntimeError):
    """The FAQ id migration refused to run or failed a check."""


# ── Helpers ─────────────────────────────────────────────────────────────


def _conn(db: Database) -> sqlite3.Connection:
    if db._conn is None:
        db.open()
    return db._conn


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    return next(iter(conn.execute(sql, params).fetchone().values()))


def _faq_rows(conn: sqlite3.Connection) -> list[dict]:
    return conn.execute(
        "SELECT e.id, e.document_id, e.entry_ref, e.title, e.body_text, d.slug "
        "FROM regulation_entries e JOIN source_documents d ON d.id = e.document_id "
        "WHERE e.entry_type = 'FAQ' ORDER BY e.id"
    ).fetchall()


def _faq_chunks(conn: sqlite3.Connection) -> list[dict]:
    return conn.execute(
        "SELECT c.id, c.entry_id, c.chunk_text FROM entry_chunks c "
        "JOIN regulation_entries e ON e.id = c.entry_id "
        "WHERE e.entry_type = 'FAQ' ORDER BY c.id"
    ).fetchall()


def is_applied(db: Database) -> bool:
    return _applied(_conn(db))


def _applied(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,)
    ).fetchone() is not None


def _rewrite_chunk_prefix(conn: sqlite3.Connection, entry_id: int, old: str, new: str) -> None:
    """Swap the leading ref of the entry's chunk text; vectors are not touched (D1)."""
    conn.execute(
        "UPDATE entry_chunks SET chunk_text = ? || substr(chunk_text, length(?) + 1) "
        "WHERE entry_id = ? AND substr(chunk_text, 1, length(?)) = ?",
        (new, old, entry_id, old, old),
    )


# ── Assessment (shared by plan and up) ──────────────────────────────────


def _new_ids(rows: list[dict]) -> tuple[dict[int, str], Counter]:
    """Proposed stable id per entry id, and the precheck/ordinal counters."""
    counts: Counter = Counter()
    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        legacy = parse_legacy_ref(row["entry_ref"])
        if legacy is None:
            counts["legacy_pattern_mismatch"] += 1
        else:
            by_prefix[legacy[0]].append(row)

    new_ids: dict[int, str] = {}
    for prefix, members in by_prefix.items():
        hashes = [content_hash(r["title"], r["body_text"]) for r in members]
        distinct = set(hashes)
        counts["ordinal_suffixed"] += len(hashes) - len(distinct)
        try:
            new_ids.update(zip((r["id"] for r in members), assign_stable_ids(prefix, hashes)))
        except StableIdCollision:
            counts["hash8_collisions"] += len(distinct) - len({h[:8] for h in distinct})

    docs = {r["id"]: r["document_id"] for r in rows}
    proposed = [(docs[i], new) for i, new in new_ids.items()]
    counts["stable_id_collisions"] = len(proposed) - len(set(proposed))
    counts["duplicate_faq_refs"] = len(rows) - len({(r["entry_ref"], r["document_id"]) for r in rows})
    return new_ids, counts


def _assess(conn: sqlite3.Connection) -> tuple[dict, list[dict], dict[int, str]]:
    rows = _faq_rows(conn)
    hashes = defaultdict(set)
    for r in rows:
        hashes[content_hash(r["title"], r["body_text"])].add(r["document_id"])
    applied = _applied(conn)
    new_ids, counts = ({}, Counter()) if applied else _new_ids(rows)
    refs = {r["id"]: r["entry_ref"] for r in rows}
    changed = {i for i, new in new_ids.items() if new != refs[i]}
    chunks = sum(
        1 for c in _faq_chunks(conn)
        if c["entry_id"] in changed and c["chunk_text"].startswith(refs[c["entry_id"]])
    )
    report = {
        "faq_rows": len(rows),
        "domains": len({r["document_id"] for r in rows}),
        "groups": len(hashes),
        "redundant_copies": len(rows) - len(hashes),
        "multi_domain_groups": sum(1 for docs in hashes.values() if len(docs) > 1),
        "aliases_to_create": 0 if applied else len(rows),
        "entry_refs_to_rewrite": len(changed),
        "ordinal_suffixed": counts["ordinal_suffixed"],
        "faq_chunks_to_update": chunks,
        **{name: counts[name] for name in _PRECHECKS},
        "already_applied": applied,
    }
    # init_schema creates these tables empty on every open, so their presence
    # alone says nothing; rows in them before 005 would be mixed into its output.
    present = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)", _FAQ_TABLES
    )]
    report["faq_tables_present"] = len(present)
    report["faq_tables_rows"] = sum(_scalar(conn, f"SELECT COUNT(*) FROM {t}") for t in present)
    report["would_apply"] = not applied and not failed_prechecks(report)
    return report, rows, new_ids


def failed_prechecks(report: dict) -> list[str]:
    failed = [name for name in _PRECHECKS if report[name]]
    if not report["already_applied"] and report["faq_tables_rows"]:
        failed.append("faq_tables_not_empty")
    return failed


def plan(db: Database) -> dict:
    """Aggregate counts of what ``up`` would do.  Reads only."""
    return _assess(_conn(db))[0]


# ── Groups ──────────────────────────────────────────────────────────────


def _rebuild_groups(conn: sqlite3.Connection) -> int:
    """Recompute faq_groups / faq_entry_groups inside the caller's transaction.

    Canonical member (D5, data-driven): the member whose domain has the
    smallest (FAQ row count, slug), then the lowest entry id.
    """
    conn.execute("DELETE FROM faq_entry_groups")
    conn.execute("DELETE FROM faq_groups")
    rows = _faq_rows(conn)
    domain_size = Counter(r["document_id"] for r in rows)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[content_hash(r["title"], r["body_text"])].append(r)
    for digest, members in groups.items():
        canonical = min(members, key=lambda r: (domain_size[r["document_id"]], r["slug"], r["id"]))
        cur = conn.execute(
            "INSERT INTO faq_groups (content_hash, canonical_entry_id, member_count, domain_count) "
            "VALUES (?, ?, ?, ?)",
            (digest, canonical["id"], len(members), len({m["document_id"] for m in members})),
        )
        conn.executemany(
            "INSERT INTO faq_entry_groups (entry_id, group_id) VALUES (?, ?)",
            [(m["id"], cur.lastrowid) for m in members],
        )
    return len(groups)


def _in_transaction(db: Database, work):
    conn = _conn(db)
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = work(conn)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def rebuild_faq_groups(db: Database) -> int:
    """Recompute the derived FAQ groups; returns the number of groups."""
    return _in_transaction(db, _rebuild_groups)


# ── up / down ───────────────────────────────────────────────────────────


def _postcheck(conn: sqlite3.Connection, expected_rows: int) -> None:
    faq = _scalar(conn, "SELECT COUNT(*) FROM regulation_entries WHERE entry_type = 'FAQ'")
    aliases = _scalar(conn, "SELECT COUNT(*) FROM faq_aliases WHERE alias_kind = ?", (LEGACY_KIND,))
    ungrouped = _scalar(
        conn,
        "SELECT COUNT(*) FROM regulation_entries e LEFT JOIN faq_entry_groups g "
        "ON g.entry_id = e.id WHERE e.entry_type = 'FAQ' AND g.entry_id IS NULL",
    )
    if (faq, aliases, ungrouped) != (expected_rows, expected_rows, 0):
        raise MigrationError(
            f"post-check failed: faq_rows={faq} aliases={aliases} ungrouped={ungrouped}"
        )


def _create_faq_tables(conn: sqlite3.Connection) -> None:
    """CREATE IF NOT EXISTS the FAQ tables from schema.sql, inside the transaction."""
    from claw_easa.db.migrations import SCHEMA_FILE, _table_ddl

    sql_text = SCHEMA_FILE.read_text()
    for table in _FAQ_TABLES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (\n{_table_ddl(sql_text, table)}\n)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_faq_entry_groups_group ON faq_entry_groups(group_id)"
    )


def _up(conn: sqlite3.Connection) -> None:
    report, rows, new_ids = _assess(conn)
    failed = failed_prechecks(report)
    if failed:
        raise MigrationError("precheck failed: " + ", ".join(failed))
    _create_faq_tables(conn)
    for row in rows:
        old, new = row["entry_ref"], new_ids[row["id"]]
        conn.execute(
            "INSERT INTO faq_aliases (alias_ref, entry_id, alias_kind) VALUES (?, ?, ?)",
            (old, row["id"], LEGACY_KIND),
        )
        conn.execute("UPDATE regulation_entries SET entry_ref = ? WHERE id = ?", (new, row["id"]))
        _rewrite_chunk_prefix(conn, row["id"], old, new)
    _rebuild_groups(conn)
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_INDEX} "
        "ON regulation_entries(entry_ref, document_id) WHERE entry_type = 'FAQ'"
    )
    conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (VERSION,))
    _postcheck(conn, len(rows))


def up(db: Database) -> bool:
    """Apply 005 in one transaction.  False when it was already applied."""
    if is_applied(db):
        return False
    _in_transaction(db, _up)
    return True


def _unaliased(conn: sqlite3.Connection) -> int:
    return _scalar(
        conn,
        "SELECT COUNT(*) FROM regulation_entries e WHERE e.entry_type = 'FAQ' AND NOT EXISTS "
        "(SELECT 1 FROM faq_aliases a WHERE a.entry_id = e.id AND a.alias_kind = ?)",
        (LEGACY_KIND,),
    )


_MAPPING_CHECKS = {
    "duplicate_alias_refs":
        "SELECT COUNT(*) FROM (SELECT alias_ref FROM faq_aliases WHERE alias_kind = ? "
        "GROUP BY alias_ref HAVING COUNT(*) > 1)",
    "entries_with_several_aliases":
        "SELECT COUNT(*) FROM (SELECT entry_id FROM faq_aliases WHERE alias_kind = ? "
        "GROUP BY entry_id HAVING COUNT(*) > 1)",
    "aliases_equal_to_other_entry_ref":
        "SELECT COUNT(*) FROM faq_aliases a WHERE a.alias_kind = ? AND EXISTS "
        "(SELECT 1 FROM regulation_entries e WHERE e.entry_ref = a.alias_ref AND e.id != a.entry_id)",
    "aliases_on_non_faq_entries":
        "SELECT COUNT(*) FROM faq_aliases a WHERE a.alias_kind = ? AND NOT EXISTS "
        "(SELECT 1 FROM regulation_entries e WHERE e.id = a.entry_id AND e.entry_type = 'FAQ')",
}


def _check_restore_mapping(conn: sqlite3.Connection) -> None:
    """Refuse (before any write) a legacy alias set that cannot be restored 1:1."""
    counts = {name: _scalar(conn, sql, (LEGACY_KIND,)) for name, sql in _MAPPING_CHECKS.items()}
    if any(counts.values()):
        raise MigrationError(
            "rollback refused, the legacy alias mapping is invalid: "
            + " ".join(f"{name}={n}" for name, n in counts.items())
        )


def _down(conn: sqlite3.Connection, force: bool) -> None:
    _check_restore_mapping(conn)  # not bypassed by force
    orphans = _unaliased(conn)
    if orphans and not force:
        raise MigrationError(
            f"{orphans} FAQ row(s) have no legacy alias (ingested after the migration); "
            "they would keep their stable ids. Use --force to roll back anyway."
        )
    conn.execute(f"DROP INDEX IF EXISTS {_UNIQUE_INDEX}")
    restored = conn.execute(
        "SELECT a.alias_ref, a.entry_id, e.entry_ref FROM faq_aliases a "
        "JOIN regulation_entries e ON e.id = a.entry_id WHERE a.alias_kind = ? ORDER BY a.entry_id",
        (LEGACY_KIND,),
    ).fetchall()
    for row in restored:
        _rewrite_chunk_prefix(conn, row["entry_id"], row["entry_ref"], row["alias_ref"])
        conn.execute(
            "UPDATE regulation_entries SET entry_ref = ? WHERE id = ?",
            (row["alias_ref"], row["entry_id"]),
        )
    for table in ("faq_aliases", "faq_entry_groups", "faq_groups"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM schema_migrations WHERE version = ?", (VERSION,))


def down(db: Database, force: bool = False) -> bool:
    """Roll 005 back in one transaction.  False when it was not applied.

    Restores the legacy refs and chunk prefixes, drops the unique index and
    unrecords 005.  faq_aliases, faq_entry_groups and faq_groups are emptied
    but kept: they are schema objects (init_schema recreates them anyway), so
    a rolled-back database shows ``faq_tables_present: 3, faq_tables_rows: 0``.
    """
    if not is_applied(db):
        return False
    _in_transaction(db, lambda conn: _down(conn, force))
    return True


# ── verify / backup ─────────────────────────────────────────────────────


def _fingerprint(conn: sqlite3.Connection, *queries: str) -> str:
    digest = hashlib.sha256()
    for sql in queries:
        digest.update(b"--\n")
        for row in conn.execute(sql):
            digest.update(json.dumps(list(row.values()), ensure_ascii=False).encode() + b"\n")
    return digest.hexdigest()


def _fts_integrity(conn: sqlite3.Connection) -> str:
    try:
        conn.execute("INSERT INTO entries_fts(entries_fts, rank) VALUES ('integrity-check', 1)")
        return "ok"
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        conn.rollback()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def open_read_only(path: Path) -> Database:
    """A ``mode=ro`` Database on any SQLite file (e.g. a backup)."""
    path = Path(path).resolve()
    db = Database(settings=Settings(data_dir=str(path.parent), db_file=path.name), read_only=True)
    db.open()
    return db


def snapshot(db: Database) -> dict:
    """Version, row count, fingerprints and integrity_check.  Reads only."""
    conn = _conn(db)
    return {
        "applied": _applied(conn),
        "faq_rows": _scalar(conn, "SELECT COUNT(*) FROM regulation_entries WHERE entry_type='FAQ'"),
        "immutable_fingerprint": _fingerprint(
            conn,
            "SELECT id, document_id, entry_type, title, body_text, body_markdown, source_url "
            "FROM regulation_entries WHERE entry_type = 'FAQ' ORDER BY id",
            "SELECT faq_entry_id, target_ref FROM faq_regulation_refs ORDER BY id",
        ),
        "restorable_fingerprint": _fingerprint(
            conn,
            "SELECT id, entry_ref FROM regulation_entries WHERE entry_type = 'FAQ' ORDER BY id",
            "SELECT c.id, c.chunk_text FROM entry_chunks c JOIN regulation_entries e "
            "ON e.id = c.entry_id WHERE e.entry_type = 'FAQ' ORDER BY c.id",
        ),
        "integrity_check": _scalar(conn, "PRAGMA integrity_check"),
    }


def _count_if_table(conn: sqlite3.Connection, table: str, sql: str) -> int:
    """*sql* (a COUNT) or 0 when a legacy-shaped database lacks *table*."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
        return 0
    return _scalar(conn, sql)


def verify(db: Database, ref_prefix: str | None = None) -> dict:
    """Fingerprints, integrity checks and FAQ id counts (design note, section 6).

    ``faq_entries_ungrouped``: FAQ rows without a ``faq_entry_groups`` row (all
    of them before the migration).  ``aliases_unresolved``: ``faq_aliases`` rows
    whose ``entry_id`` is not a FAQ row of ``regulation_entries``, i.e. the
    legacy ref (``alias_ref``) would resolve to nothing.  With *ref_prefix*:
    distinct FAQ rows citing a ``target_ref`` starting with it, and their
    distinct groups (each row counts as its own group when not applied).
    """
    conn = _conn(db)
    report = {**snapshot(db), "fts_integrity": _fts_integrity(conn)}
    report["faq_groups"] = _count_if_table(conn, "faq_groups", "SELECT COUNT(*) FROM faq_groups")
    report["faq_aliases"] = _count_if_table(
        conn, "faq_aliases", "SELECT COUNT(*) FROM faq_aliases"
    )
    report["faq_entries_ungrouped"] = report["faq_rows"] - _count_if_table(
        conn, "faq_entry_groups",
        "SELECT COUNT(*) FROM faq_entry_groups g JOIN regulation_entries e "
        "ON e.id = g.entry_id WHERE e.entry_type = 'FAQ'",
    )
    report["aliases_unresolved"] = _count_if_table(
        conn, "faq_aliases",
        "SELECT COUNT(*) FROM faq_aliases a WHERE NOT EXISTS (SELECT 1 FROM regulation_entries e "
        "WHERE e.id = a.entry_id AND e.entry_type = 'FAQ')",
    )
    faiss = db.settings.faiss_index_path
    report["faiss_sha256"] = file_sha256(faiss) if faiss.is_file() else "absent"
    if ref_prefix is not None:
        matched = (
            "FROM faq_regulation_refs r JOIN regulation_entries e ON e.id = r.faq_entry_id "
            "AND e.entry_type = 'FAQ' {join} WHERE substr(r.target_ref, 1, length(?)) = ?"
        )
        params = (ref_prefix, ref_prefix)
        rows = _scalar(conn, "SELECT COUNT(DISTINCT r.faq_entry_id) " + matched.format(join=""),
                       params)
        report["ref_prefix_faq_rows"] = rows
        report["ref_prefix_groups"] = _scalar(
            conn,
            "SELECT COUNT(DISTINCT g.group_id) "
            + matched.format(join="JOIN faq_entry_groups g ON g.entry_id = e.id"),
            params,
        ) if report["applied"] else rows
    return report


def compare(current: dict, backup: dict) -> dict:
    """Fingerprint match of *current* against a backup snapshot.

    Restorable ids differ legitimately when either side is migrated: then n/a.
    """
    restorable = "n/a"
    if not current["applied"] and not backup["applied"]:
        restorable = current["restorable_fingerprint"] == backup["restorable_fingerprint"]
    return {
        "against_immutable_match":
            current["immutable_fingerprint"] == backup["immutable_fingerprint"],
        "against_restorable_match": restorable,
    }


def _counts(conn: sqlite3.Connection) -> list[object]:
    return [_scalar(conn, f"SELECT COUNT(*) FROM {table}") for table in _BACKUP_TABLES]


def backup_database(db: Database, dest: Path) -> None:
    """SQLite online backup to *dest* (must not exist), checked before use."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"backup target already exists: {dest}")
    source = _conn(db)
    target = sqlite3.connect(dest)
    target.row_factory = source.row_factory
    try:
        source.backup(target)
        if _scalar(target, "PRAGMA integrity_check") != "ok":
            raise MigrationError(f"backup failed integrity_check: {dest}")
        if _counts(source) != _counts(target):
            raise MigrationError(f"backup row counts differ from the source: {dest}")
    finally:
        target.close()


# ── Ingestion guard ─────────────────────────────────────────────────────


def ensure_ready_for_ingest(db: Database) -> None:
    """Refuse FAQ ingestion into an unmigrated corpus; adopt 005 on an empty one."""
    if is_applied(db):
        return
    if _scalar(_conn(db), "SELECT COUNT(*) FROM regulation_entries WHERE entry_type = 'FAQ'"):
        raise MigrationError(
            "FAQ ingestion is blocked: FAQ rows with positional refs exist but migration "
            "005_faq_stable_ids is not applied (ingesting now would duplicate every FAQ under "
            "new ids). Run 'claw-easa db faq-ids plan --data-dir DIR' to review, then "
            "'claw-easa db faq-ids apply --data-dir DIR --backup-to PATH'; ingestion is blocked "
            "until the migration is applied; see docs/upgrading-the-database.md."
        )
    up(db)


def faq_ids_state(db: Database) -> str:
    """"migrated" | "not_migrated" | "no_faq_rows", with SELECTs only."""
    conn = _conn(db)
    if not _scalar(conn, "SELECT COUNT(*) FROM regulation_entries WHERE entry_type = 'FAQ'"):
        return "no_faq_rows"
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone():
        return "not_migrated"
    return "migrated" if _applied(conn) else "not_migrated"
