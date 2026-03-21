"""Phase 2 tests — Ingestion pipeline with SQLite."""

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.sqlite import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.repository import (
    get_document_by_slug,
    get_latest_source_file,
    list_documents,
    record_download,
    reference_exists,
    upsert_source_document_from_values,
)


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


class TestRepository:
    def test_upsert_source_document(self, db):
        doc_id = upsert_source_document_from_values(
            db, slug="air-ops", source_family="ear",
            title="Easy Access Rules for Air Operations",
        )
        assert doc_id > 0

        doc_id_2 = upsert_source_document_from_values(
            db, slug="air-ops", source_family="ear",
            title="Easy Access Rules for Air Operations (updated)",
        )
        assert doc_id_2 == doc_id

    def test_get_document_by_slug(self, db):
        upsert_source_document_from_values(
            db, slug="aircrew", source_family="ear", title="Aircrew",
        )
        doc = get_document_by_slug(db, "aircrew")
        assert doc is not None
        assert doc["slug"] == "aircrew"

    def test_get_document_not_found(self, db):
        doc = get_document_by_slug(db, "nonexistent")
        assert doc is None

    def test_record_download(self, db):
        doc_id = upsert_source_document_from_values(
            db, slug="test-dl", source_family="ear", title="Test",
        )
        file_id = record_download(
            db, document_id=doc_id, checksum="abc123",
            local_path="/tmp/test.docx",
        )
        assert file_id > 0

        source_file = get_latest_source_file(db, doc_id)
        assert source_file is not None
        assert source_file["checksum"] == "abc123"

    def test_list_documents(self, db):
        upsert_source_document_from_values(
            db, slug="doc-a", source_family="ear", title="Doc A",
        )
        upsert_source_document_from_values(
            db, slug="doc-b", source_family="ear", title="Doc B",
        )
        docs = list_documents(db)
        assert len(docs) >= 2
        slugs = [d["slug"] for d in docs]
        assert "doc-a" in slugs
        assert "doc-b" in slugs

    def test_reference_exists(self, db):
        assert not reference_exists(db, "NONEXISTENT.001")


class TestCanonicalPersister:
    def _make_parsed_document(self):
        from claw_easa.ingest.parser import (
            ParsedEntry, ParsedSection, ParsedSubpart, ParsedPart, ParsedDocument,
        )

        entry = ParsedEntry(
            entry_ref="ORO.FTL.110",
            entry_type="IR",
            title="ORO.FTL.110 Operator responsibilities",
            body_lines=["The operator shall:", "(a) publish duty rosters;"],
            source_locator="section-1",
            sort_order=0,
        )
        section = ParsedSection(
            title="Section I — General",
            entries=[entry],
            sort_order=0,
        )
        subpart = ParsedSubpart(
            code="Subpart FTL",
            title="Flight and duty time limitations",
            sections=[section],
            sort_order=0,
        )
        part = ParsedPart(
            code="ORO",
            annex="Annex III",
            title="Organisation Requirements for Air Operations",
            subparts=[subpart],
            sort_order=0,
        )
        return ParsedDocument(title="Air Ops", parts=[part])

    def test_canonical_persister_round_trip(self, db):
        doc_id = upsert_source_document_from_values(
            db, slug="persist-test", source_family="ear", title="Persist Test",
        )
        parsed = self._make_parsed_document()
        persister = CanonicalPersister(db)
        summary = persister.persist_document(doc_id, parsed)

        assert summary.parts == 1
        assert summary.subparts == 1
        assert summary.sections == 1
        assert summary.entries == 1
        assert reference_exists(db, "ORO.FTL.110")

    def test_canonical_persister_destructive_update(self, db):
        doc_id = upsert_source_document_from_values(
            db, slug="destruct-test", source_family="ear", title="Destruct Test",
        )
        parsed = self._make_parsed_document()
        persister = CanonicalPersister(db)
        persister.persist_document(doc_id, parsed)
        persister.persist_document(doc_id, parsed)

        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM regulation_entries "
                    "WHERE document_id = ?",
                    (doc_id,),
                )
                assert cur.fetchone()["cnt"] == 1
