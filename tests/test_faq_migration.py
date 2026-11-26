"""S1 — schema tables and the explicit migration 005_faq_stable_ids."""

import hashlib
import sqlite3

import pytest

from claw_easa.config import Settings
from claw_easa.db import faq_migration
from claw_easa.db.faq_migration import (
    VERSION,
    MigrationError,
    backup_database,
    down,
    is_applied,
    plan,
    rebuild_faq_groups,
    up,
    verify,
)
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.faq_identity import content_hash
from tests.faq_fixtures import (
    BODIES,
    DUP,
    LEGACY_FAQ_COUNT,
    NEW_TABLES,
    SAME_TITLE,
    SHARED,
    add_domain,
    add_entry,
    legacy_corpus,
    make_legacy_shaped,
    open_db,
)


@pytest.fixture
def db(tmp_path):
    database = open_db(tmp_path)
    yield database
    database.close()


@pytest.fixture
def legacy(tmp_path):
    """A legacy-shaped database (no S1 tables) holding positional FAQ refs."""
    database = open_db(tmp_path)
    make_legacy_shaped(database)
    ids = legacy_corpus(database)
    yield database, ids
    database.close()


def _tables(db):
    return {r["name"] for r in _all(db, "SELECT name FROM sqlite_master WHERE type='table'")}


def _all(db, sql, params=()):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _count(db, sql, params=()):
    return next(iter(db.fetch_one(sql, params).values()))


def _faq_ref(db, entry_id):
    return db.fetch_one("SELECT entry_ref FROM regulation_entries WHERE id = ?", (entry_id,))[
        "entry_ref"
    ]


def _migrate(db):
    MigrationRunner(db).init_schema()
    return up(db)


class TestSchema:
    def test_fresh_db_has_the_three_tables_empty(self, db):
        assert set(NEW_TABLES) <= _tables(db)
        for table in NEW_TABLES:
            assert _count(db, f"SELECT COUNT(*) FROM {table}") == 0

    def test_legacy_shaped_db_gains_the_tables_and_keeps_rows(self, legacy):
        db, _ = legacy
        assert not set(NEW_TABLES) & _tables(db)
        MigrationRunner(db).init_schema()
        assert set(NEW_TABLES) <= _tables(db)
        assert _count(db, "SELECT COUNT(*) FROM regulation_entries WHERE entry_type='FAQ'") == (
            LEGACY_FAQ_COUNT
        )

    def test_init_schema_tolerates_colliding_faq_refs(self, legacy):
        db, ids = legacy
        dom = add_domain(db, "faq-clash")
        add_entry(db, dom, "FAQ-clash-001", "Question one here?", "Answer one here.")
        add_entry(db, dom, "FAQ-clash-001", "Question two here?", "Answer two here.")
        MigrationRunner(db).init_schema()  # must not raise
        assert not _all(
            db, "SELECT 1 FROM sqlite_master WHERE name = 'uix_faq_entries_ref_doc'"
        )


class TestPlan:
    def test_counts_on_legacy_corpus(self, legacy):
        db, _ = legacy
        assert plan(db) == {
            "faq_rows": 14,
            "domains": 5,
            "groups": 10,
            "redundant_copies": 4,
            "multi_domain_groups": 2,
            "aliases_to_create": 14,
            "entry_refs_to_rewrite": 14,
            "ordinal_suffixed": 1,
            "faq_chunks_to_update": 14,
            "stable_id_collisions": 0,
            "hash8_collisions": 0,
            "legacy_pattern_mismatch": 0,
            "duplicate_faq_refs": 0,
            "already_applied": False,
            "faq_tables_present": 0,  # S3: additive plan keys (deliberate edit)
            "faq_tables_rows": 0,
            "would_apply": True,
        }

    def test_fresh_db(self, db):
        counts = plan(db)
        assert counts["faq_rows"] == 0
        assert counts["would_apply"] is True

    def test_precheck_failures_block(self, legacy):
        db, _ = legacy
        dom = add_domain(db, "faq-odd")
        add_entry(db, dom, "FAQ-odd-1", "Odd question here?", "Odd answer here.")
        add_entry(db, dom, "FAQ-odd-002", "Clash question one?", "Clash answer one.")
        add_entry(db, dom, "FAQ-odd-002", "Clash question two?", "Clash answer two.")
        counts = plan(db)
        assert counts["legacy_pattern_mismatch"] == 1
        assert counts["duplicate_faq_refs"] == 1
        assert counts["would_apply"] is False
        MigrationRunner(db).init_schema()
        with pytest.raises(MigrationError, match="legacy_pattern_mismatch"):
            up(db)
        assert _count(db, "SELECT COUNT(*) FROM faq_aliases") == 0

    def test_plan_is_read_only(self, legacy, tmp_path):
        db, _ = legacy
        db.close()
        path = tmp_path / "claw_easa.db"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        ro = Database(settings=Settings(data_dir=str(tmp_path)), read_only=True)
        try:
            assert plan(ro)["faq_rows"] == LEGACY_FAQ_COUNT
            with pytest.raises(sqlite3.OperationalError):
                ro.execute("DELETE FROM regulation_entries")
        finally:
            ro.close()
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before

    def test_after_up_reports_applied(self, legacy):
        db, _ = legacy
        _migrate(db)
        counts = plan(db)
        assert counts["already_applied"] is True
        assert counts["would_apply"] is False
        assert counts["aliases_to_create"] == 0


class TestUp:
    def test_rewrites_refs_to_stable_ids(self, legacy):
        db, ids = legacy
        assert _migrate(db) is True
        h = content_hash(*SHARED)[:8]
        assert _faq_ref(db, ids["FAQ-grand-001"]) == f"FAQ-grand-{h}"
        assert _faq_ref(db, ids["FAQ-child-001"]) == f"FAQ-child-{h}"
        d = content_hash(*DUP)[:8]
        assert _faq_ref(db, ids["FAQ-grand-002"]) == f"FAQ-grand-{d}"
        assert _faq_ref(db, ids["FAQ-grand-003"]) == f"FAQ-grand-{d}-2"
        assert is_applied(db)

    def test_row_count_and_immutable_fingerprint_unchanged(self, legacy):
        db, _ = legacy
        before = verify(db)
        _migrate(db)
        after = verify(db)
        assert after["faq_rows"] == before["faq_rows"] == LEGACY_FAQ_COUNT
        assert after["immutable_fingerprint"] == before["immutable_fingerprint"]
        assert after["restorable_fingerprint"] != before["restorable_fingerprint"]

    def test_every_legacy_ref_has_an_alias(self, legacy):
        db, ids = legacy
        _migrate(db)
        aliases = {
            r["alias_ref"]: r["entry_id"]
            for r in _all(db, "SELECT alias_ref, entry_id FROM faq_aliases "
                              "WHERE alias_kind = 'legacy_positional'")
        }
        assert aliases == ids

    def test_chunk_prefix_rewritten_vectors_untouched(self, legacy):
        db, ids = legacy
        mapping = _all(db, "SELECT * FROM faiss_mapping ORDER BY faiss_position")
        _migrate(db)
        chunk = db.fetch_one(
            "SELECT chunk_text FROM entry_chunks WHERE entry_id = ?", (ids["FAQ-grand-001"],)
        )["chunk_text"]
        assert chunk.startswith(_faq_ref(db, ids["FAQ-grand-001"]) + " (FAQ) — ")
        assert "FAQ-grand-001" not in chunk
        assert _all(db, "SELECT * FROM faiss_mapping ORDER BY faiss_position") == mapping
        ir = db.fetch_one("SELECT chunk_text FROM entry_chunks c JOIN regulation_entries e "
                          "ON e.id = c.entry_id WHERE e.entry_type = 'IR'")
        assert ir["chunk_text"].startswith("ORO.FTL.110 (IR)")

    def test_groups_complete(self, legacy):
        db, _ = legacy
        _migrate(db)
        assert _count(db, "SELECT COUNT(*) FROM faq_groups") == 10
        assert _count(db, "SELECT COUNT(*) FROM faq_entry_groups") == LEGACY_FAQ_COUNT
        shared = db.fetch_one(
            "SELECT * FROM faq_groups WHERE content_hash = ?", (content_hash(*SHARED),)
        )
        assert (shared["member_count"], shared["domain_count"]) == (3, 3)

    def test_same_title_different_body_stay_separate(self, legacy):
        db, _ = legacy
        _migrate(db)
        groups = {
            r["group_id"]
            for r in _all(db, "SELECT g.group_id FROM faq_entry_groups g JOIN "
                              "regulation_entries e ON e.id = g.entry_id WHERE e.title = ?",
                          (SAME_TITLE,))
        }
        assert len(groups) == len(BODIES)

    def test_unique_index_and_version_recorded(self, legacy):
        db, _ = legacy
        _migrate(db)
        assert _all(db, "SELECT 1 FROM sqlite_master WHERE name = 'uix_faq_entries_ref_doc'")
        assert _all(db, "SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,))

    def test_second_up_is_a_no_op(self, legacy):
        db, _ = legacy
        _migrate(db)
        first = verify(db)
        assert up(db) is False
        assert verify(db) == first
        assert _count(db, "SELECT COUNT(*) FROM faq_aliases") == LEGACY_FAQ_COUNT

    def test_fresh_db_records_version(self, db):
        assert up(db) is True
        assert is_applied(db)

    def test_fts_consistent_after_up(self, legacy):
        db, ids = legacy
        _migrate(db)
        assert verify(db)["fts_integrity"] == "ok"
        new_ref = _faq_ref(db, ids["FAQ-parent-002"])
        hit = db.fetch_one(
            "SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?",
            (f'entry_ref:"{new_ref.split("-")[-1]}"',),
        )
        assert hit["rowid"] == ids["FAQ-parent-002"]


class TestCanonicalRule:
    def test_smallest_domain_wins(self, legacy):
        db, ids = legacy
        _migrate(db)
        shared = db.fetch_one(
            "SELECT canonical_entry_id FROM faq_groups WHERE content_hash = ?",
            (content_hash(*SHARED),),
        )
        assert shared["canonical_entry_id"] == ids["FAQ-child-001"]

    def test_tie_on_size_is_alphabetical(self, legacy):
        from tests.faq_fixtures import TIED

        db, ids = legacy
        _migrate(db)
        tied = db.fetch_one(
            "SELECT canonical_entry_id FROM faq_groups WHERE content_hash = ?",
            (content_hash(*TIED),),
        )
        assert tied["canonical_entry_id"] == ids["FAQ-alpha-001"]

    def test_intra_domain_lowest_id(self, legacy):
        db, ids = legacy
        _migrate(db)
        dup = db.fetch_one(
            "SELECT canonical_entry_id FROM faq_groups WHERE content_hash = ?",
            (content_hash(*DUP),),
        )
        assert dup["canonical_entry_id"] == ids["FAQ-grand-002"]

    def test_rebuild_follows_domain_sizes(self, legacy):
        db, ids = legacy
        _migrate(db)
        dom = db.fetch_one(
            "SELECT document_id AS doc, part_id AS part, subpart_id AS subpart, "
            "section_id AS section FROM regulation_entries WHERE id = ?",
            (ids["FAQ-child-001"],),
        )
        for n in range(4):
            add_entry(db, dom, f"FAQ-child-x{n}", f"Extra question {n}?", f"Extra answer {n}.")
        assert rebuild_faq_groups(db) == 14
        shared = db.fetch_one(
            "SELECT canonical_entry_id FROM faq_groups WHERE content_hash = ?",
            (content_hash(*SHARED),),
        )
        assert shared["canonical_entry_id"] == ids["FAQ-parent-001"]


class TestDown:
    def test_restores_restorable_fingerprint(self, legacy):
        db, ids = legacy
        before = verify(db)
        _migrate(db)
        assert down(db) is True
        after = verify(db)
        assert after["immutable_fingerprint"] == before["immutable_fingerprint"]
        assert after["restorable_fingerprint"] == before["restorable_fingerprint"]
        assert _faq_ref(db, ids["FAQ-grand-003"]) == "FAQ-grand-003"
        assert not is_applied(db)
        for table in NEW_TABLES:
            assert _count(db, f"SELECT COUNT(*) FROM {table}") == 0
        assert not _all(db, "SELECT 1 FROM sqlite_master WHERE name = 'uix_faq_entries_ref_doc'")
        assert after["fts_integrity"] == "ok"

    def test_up_after_down_gives_same_ids(self, legacy):
        db, _ = legacy
        _migrate(db)
        first = verify(db)
        down(db)
        up(db)
        assert verify(db) == first

    def test_refuses_with_post_migration_rows(self, legacy):
        db, ids = legacy
        _migrate(db)
        dom = add_domain(db, "faq-late")
        late = add_entry(db, dom, "FAQ-late-0123abcd", "Late question here?", "Late answer here.")
        with pytest.raises(MigrationError, match="1 FAQ row"):
            down(db)
        assert is_applied(db)
        assert down(db, force=True) is True
        assert _faq_ref(db, late) == "FAQ-late-0123abcd"
        assert _faq_ref(db, ids["FAQ-beta-002"]) == "FAQ-beta-002"
        assert _count(db, "SELECT COUNT(*) FROM regulation_entries WHERE entry_type='FAQ'") == 15

    def test_down_when_not_applied_is_a_no_op(self, legacy):
        db, _ = legacy
        assert down(db) is False


class TestVerify:
    def test_detects_fts_drift(self, legacy):
        db, ids = legacy
        db.execute("DROP TRIGGER entries_fts_update")
        db.execute("UPDATE regulation_entries SET title = 'drifted title' WHERE id = ?",
                   (ids["FAQ-alpha-001"],))
        assert verify(db)["fts_integrity"] != "ok"

    def test_integrity_ok(self, legacy):
        db, _ = legacy
        assert verify(db)["integrity_check"] == "ok"


class TestBackup:
    def test_backup_copies_rows(self, legacy, tmp_path):
        db, _ = legacy
        dest = tmp_path / "backup.db"
        backup_database(db, dest)
        copy = sqlite3.connect(dest)
        try:
            assert copy.execute(
                "SELECT COUNT(*) FROM regulation_entries WHERE entry_type='FAQ'"
            ).fetchone()[0] == LEGACY_FAQ_COUNT
        finally:
            copy.close()

    def test_backup_refuses_existing_target(self, legacy, tmp_path):
        db, _ = legacy
        dest = tmp_path / "backup.db"
        dest.write_bytes(b"keep")
        with pytest.raises(FileExistsError):
            backup_database(db, dest)
        assert dest.read_bytes() == b"keep"


class TestVerifyOperatorFields:
    def test_unmigrated_legacy_shaped_db(self, legacy):
        db, _ = legacy
        report = verify(db)
        assert report["faq_groups"] == report["faq_aliases"] == 0
        assert report["faq_entries_ungrouped"] == LEGACY_FAQ_COUNT
        assert report["aliases_unresolved"] == 0
        assert report["faiss_sha256"] == "absent"

    def test_migrated_db(self, legacy):
        db, _ = legacy
        _migrate(db)
        report = verify(db)
        assert report["faq_aliases"] == LEGACY_FAQ_COUNT
        assert report["faq_groups"] == 10
        assert report["faq_entries_ungrouped"] == 0
        assert report["aliases_unresolved"] == 0

    def test_alias_to_a_non_faq_entry_is_unresolved(self, legacy):
        db, _ = legacy
        _migrate(db)
        ir = _count(db, "SELECT id FROM regulation_entries WHERE entry_type = 'IR'")
        db.execute(
            "INSERT INTO faq_aliases (alias_ref, entry_id) VALUES ('FAQ-gone-001', ?)", (ir,)
        )
        assert verify(db)["aliases_unresolved"] == 1

    def test_faiss_sha256(self, legacy, tmp_path):
        db, _ = legacy
        (tmp_path / "claw_easa.faiss").write_bytes(b"vectors")
        assert verify(db)["faiss_sha256"] == hashlib.sha256(b"vectors").hexdigest()

    def test_ref_prefix_counts(self, legacy):
        db, _ = legacy
        before = verify(db, ref_prefix="ORO.FTL")
        assert before["ref_prefix_faq_rows"] == before["ref_prefix_groups"] == 2
        assert "ref_prefix_faq_rows" not in verify(db)
        _migrate(db)
        after = verify(db, ref_prefix="ORO.FTL")
        assert (after["ref_prefix_faq_rows"], after["ref_prefix_groups"]) == (2, 1)
        assert verify(db, ref_prefix="CAT.")["ref_prefix_faq_rows"] == 0


class TestFaqTablesPresence:
    """init_schema creates the three tables empty, even before 005 is applied."""

    def test_empty_tables_present_on_legacy_corpus(self, db):
        legacy_corpus(db)
        counts = plan(db)
        assert (counts["faq_tables_present"], counts["faq_tables_rows"]) == (3, 0)
        assert counts["would_apply"] is True
        keys = list(counts)
        assert keys.index("faq_tables_present") == keys.index("already_applied") + 1
        assert keys.index("faq_tables_rows") == keys.index("already_applied") + 2
        assert up(db) is True
        assert _count(db, "SELECT COUNT(*) FROM faq_groups") == 10
        assert _count(db, "SELECT COUNT(*) FROM faq_aliases") == LEGACY_FAQ_COUNT
        assert _count(db, "SELECT COUNT(*) FROM faq_entry_groups") == LEGACY_FAQ_COUNT

    def test_legacy_shaped_db_has_none(self, legacy):
        db, _ = legacy
        counts = plan(db)
        assert (counts["faq_tables_present"], counts["faq_tables_rows"]) == (0, 0)

    def test_one_table_dropped_up_recreates_it(self, db):
        legacy_corpus(db)
        db.execute("DROP TABLE faq_aliases")
        counts = plan(db)
        assert (counts["faq_tables_present"], counts["faq_tables_rows"]) == (2, 0)
        assert counts["would_apply"] is True
        assert up(db) is True
        assert _count(db, "SELECT COUNT(*) FROM faq_aliases") == LEGACY_FAQ_COUNT
        assert _count(db, "SELECT COUNT(*) FROM faq_groups") == 10

    def test_non_empty_alias_table_blocks(self, db):
        ids = legacy_corpus(db)
        db.execute(
            "INSERT INTO faq_aliases (alias_ref, entry_id) VALUES ('FAQ-stale-001', ?)",
            (ids["FAQ-grand-001"],),
        )
        before = verify(db)
        counts = plan(db)
        assert counts["faq_tables_rows"] == 1
        assert counts["would_apply"] is False
        assert faq_migration.failed_prechecks(counts) == ["faq_tables_not_empty"]
        with pytest.raises(MigrationError, match="faq_tables_not_empty"):
            up(db)
        assert not is_applied(db)
        assert verify(db) == before
        assert _count(db, "SELECT COUNT(*) FROM faq_aliases") == 1
        assert _count(db, "SELECT COUNT(*) FROM faq_groups") == 0

    def test_applied_db_rows_are_not_a_failure(self, db):
        legacy_corpus(db)
        up(db)
        counts = plan(db)
        assert counts["faq_tables_rows"] == 10 + 2 * LEGACY_FAQ_COUNT
        assert faq_migration.failed_prechecks(counts) == []


class TestSnapshot:
    def test_snapshot_of_read_only_backup_matches_verify(self, legacy, tmp_path):
        db, _ = legacy
        dest = tmp_path / "backup.db"
        backup_database(db, dest)
        copy = faq_migration.open_read_only(dest)
        try:
            snap = faq_migration.snapshot(copy)
        finally:
            copy.close()
        live = verify(db)
        assert snap == {key: live[key] for key in snap}
        assert set(snap) == {
            "applied", "faq_rows", "immutable_fingerprint", "restorable_fingerprint",
            "integrity_check",
        }

    def test_file_sha256(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"abc")
        assert faq_migration.file_sha256(path) == hashlib.sha256(b"abc").hexdigest()
