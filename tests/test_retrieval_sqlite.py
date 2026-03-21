"""Phase 3 tests — Retrieval layer with SQLite + FAISS."""

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.sqlite import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.repository import upsert_source_document_from_values


def _seed_entries(db):
    from claw_easa.ingest.parser import (
        ParsedEntry, ParsedSection, ParsedSubpart, ParsedPart, ParsedDocument,
    )

    doc_id = upsert_source_document_from_values(
        db, slug="air-ops", source_family="ear",
        title="Easy Access Rules for Air Operations",
    )

    entries = [
        ParsedEntry(
            entry_ref="ORO.FTL.110",
            entry_type="IR",
            title="ORO.FTL.110 Operator responsibilities",
            body_lines=[
                "The operator shall:",
                "(a) publish duty rosters sufficiently in advance;",
                "(b) ensure compliance with flight time limitations;",
            ],
            source_locator="s1",
            sort_order=0,
        ),
        ParsedEntry(
            entry_ref="ORO.FTL.120",
            entry_type="IR",
            title="ORO.FTL.120 Flight time specification schemes",
            body_lines=[
                "The operator shall establish flight time specification schemes.",
                "These shall comply with Regulation (EU) No 83/2014.",
            ],
            source_locator="s2",
            sort_order=1,
        ),
        ParsedEntry(
            entry_ref="AMC1 ORO.FTL.110",
            entry_type="AMC",
            title="AMC1 ORO.FTL.110 Operator responsibilities",
            body_lines=[
                "Acceptable means of compliance for operator responsibilities.",
                "The operator should publish rosters 14 days in advance.",
            ],
            source_locator="s3",
            sort_order=2,
        ),
    ]

    section = ParsedSection(
        title="Section I — General", entries=entries, sort_order=0,
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
    parsed = ParsedDocument(title="Air Ops", parts=[part])

    persister = CanonicalPersister(db)
    persister.persist_document(doc_id, parsed)
    return doc_id


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
    _seed_entries(database)
    yield database
    database.close()
    reset_settings()


class TestExactLookup:
    def test_lookup_existing_ref(self, db):
        from claw_easa.retrieval.exact import lookup_reference

        rows = lookup_reference(db, "ORO.FTL.110")
        assert len(rows) >= 1
        assert rows[0]["entry_ref"] == "ORO.FTL.110"

    def test_lookup_missing_ref(self, db):
        from claw_easa.retrieval.exact import lookup_reference

        rows = lookup_reference(db, "NONEXISTENT.999")
        assert len(rows) == 0


class TestSearchReferences:
    def test_search_references_fts(self, db):
        from claw_easa.retrieval.exact import search_references

        rows = search_references(db, "operator responsibilities")
        assert len(rows) >= 1
        refs = [r["entry_ref"] for r in rows]
        assert any("ORO.FTL.110" in ref for ref in refs)

    def test_search_references_like_fallback(self, db):
        from claw_easa.retrieval.exact import search_references

        rows = search_references(db, "ORO.FTL")
        assert len(rows) >= 1


class TestSnippets:
    def test_search_snippets(self, db):
        from claw_easa.retrieval.snippets import search_snippets

        rows = search_snippets(db, "flight time")
        assert len(rows) >= 1


class TestHybrid:
    def test_hybrid_exact_ref(self, db):
        from claw_easa.retrieval.hybrid import hybrid_search

        rows = hybrid_search(db, "ORO.FTL.110")
        assert len(rows) >= 1
        assert rows[0]["entry_ref"] == "ORO.FTL.110"
        assert rows[0]["match_source"] == "exact"

    def test_hybrid_text_query(self, db):
        from claw_easa.retrieval.hybrid import hybrid_search

        rows = hybrid_search(db, "operator responsibilities")
        assert len(rows) >= 1


class TestFTSCompat:
    def test_or_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("flight OR duty")
        assert "OR" in result.match_expr

    def test_mixed_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query('"flight time" limitations -helicopter')
        assert result.has_terms
        assert '"flight time"' in result.match_expr
        assert "NOT helicopter" in result.match_expr
