"""Phase 1 tests — SQLite foundation layer."""

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.sqlite import Database
from claw_easa.db.migrations import MigrationRunner


@pytest.fixture
def tmp_settings(tmp_path):
    reset_settings()
    return Settings(data_dir=str(tmp_path), db_file="test.db")


@pytest.fixture
def db(tmp_settings):
    database = Database(settings=tmp_settings)
    database.open()
    runner = MigrationRunner(database)
    runner.init_schema()
    yield database
    database.close()
    reset_settings()


class TestSQLiteConnection:
    def test_open_creates_db_file(self, tmp_settings):
        database = Database(settings=tmp_settings)
        database.open()
        assert database.db_path.exists()
        database.close()

    def test_foreign_keys_enabled(self, db):
        row = db.fetch_one("PRAGMA foreign_keys")
        assert row["foreign_keys"] == 1

    def test_wal_mode(self, db):
        row = db.fetch_one("PRAGMA journal_mode")
        assert row["journal_mode"] == "wal"


class TestSchemaCreation:
    def test_schema_creates_all_tables(self, db):
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
                tables = {row["name"] for row in cur.fetchall()}

        expected = {
            "source_documents",
            "source_files",
            "regulation_parts",
            "regulation_subparts",
            "regulation_sections",
            "regulation_entries",
            "entry_chunks",
            "faiss_mapping",
            "faq_regulation_refs",
            "entries_fts",
            "entries_fts_data",
            "entries_fts_idx",
            "entries_fts_config",
            "entries_fts_docsize",
            "schema_migrations",
        }
        assert expected.issubset(tables), f"Missing: {expected - tables}"

    def test_schema_idempotent(self, db):
        runner = MigrationRunner(db)
        runner.init_schema()
        runner.init_schema()
        row = db.fetch_one("SELECT COUNT(*) AS cnt FROM schema_migrations")
        assert row["cnt"] >= 1


class TestFTS5:
    def test_fts_insert_trigger(self, db):
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO source_documents (slug, source_family, title) "
                    "VALUES ('test-doc', 'ear', 'Test Document')"
                )
                doc_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_parts "
                    "(document_id, part_code, annex, title, sort_order) "
                    "VALUES (?, 'PART-TEST', '', 'Test Part', 0)",
                    (doc_id,),
                )
                part_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_subparts "
                    "(part_id, subpart_code, title, sort_order) "
                    "VALUES (?, 'SUBPART-A', 'Test Subpart', 0)",
                    (part_id,),
                )
                subpart_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_sections "
                    "(subpart_id, section_code, title, sort_order) "
                    "VALUES (?, NULL, 'Test Section', 0)",
                    (subpart_id,),
                )
                section_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_entries "
                    "(document_id, part_id, subpart_id, section_id, "
                    " entry_ref, entry_type, title, body_text, sort_order) "
                    "VALUES (?, ?, ?, ?, 'TEST.001', 'IR', "
                    " 'Flight time limitations', "
                    " 'Maximum daily flight time is 13 hours', 0)",
                    (doc_id, part_id, subpart_id, section_id),
                )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM entries_fts "
                    "WHERE entries_fts MATCH 'flight time'"
                )
                results = cur.fetchall()
                assert len(results) >= 1

    def test_fts_delete_trigger(self, db):
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO source_documents (slug, source_family, title) "
                    "VALUES ('del-doc', 'ear', 'Delete Test')"
                )
                doc_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_parts "
                    "(document_id, part_code, annex, title, sort_order) "
                    "VALUES (?, 'P', '', 'P', 0)",
                    (doc_id,),
                )
                part_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_subparts "
                    "(part_id, subpart_code, title, sort_order) "
                    "VALUES (?, 'S', 'S', 0)",
                    (part_id,),
                )
                subpart_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_sections "
                    "(subpart_id, section_code, title, sort_order) "
                    "VALUES (?, NULL, 'S', 0)",
                    (subpart_id,),
                )
                section_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO regulation_entries "
                    "(document_id, part_id, subpart_id, section_id, "
                    " entry_ref, entry_type, title, body_text, sort_order) "
                    "VALUES (?, ?, ?, ?, 'DEL.001', 'IR', 'Deletable', "
                    " 'unique_deletable_content', 0)",
                    (doc_id, part_id, subpart_id, section_id),
                )
                entry_id = cur.lastrowid
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM entries_fts "
                    "WHERE entries_fts MATCH 'unique_deletable_content'"
                )
                assert len(cur.fetchall()) == 1

                cur.execute(
                    "DELETE FROM regulation_entries WHERE id = ?",
                    (entry_id,),
                )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM entries_fts "
                    "WHERE entries_fts MATCH 'unique_deletable_content'"
                )
                assert len(cur.fetchall()) == 0


class TestFTSCompat:
    def test_basic_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("flight time limitations")
        assert result.has_terms
        assert "flight" in result.match_expr
        assert "time" in result.match_expr

    def test_phrase_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query('"split duty"')
        assert result.has_terms
        assert '"split duty"' in result.match_expr

    def test_negation(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("flight -helicopter")
        assert result.has_terms
        assert "NOT helicopter" in result.match_expr

    def test_empty_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("")
        assert not result.has_terms

    def test_dotted_reference(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("ORO.FTL.110")
        assert result.has_terms
        assert "ORO" in result.match_expr
        assert "FTL" in result.match_expr


class TestMigrationRunner:
    def test_current_version(self, db):
        runner = MigrationRunner(db)
        version = runner.current_version()
        assert version == "001_initial"
