"""Non-regression tests for the retrieval gap fix.

Validates sub-entry chunking, source-scoped retrieval, and source-aware
boosting — the three structural fixes for the fatigue/occurrence-reporting
miss documented in docs/retrieval-gap-fatigue-occurrence-reporting.md.
"""

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.sqlite import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import (
    ParsedDocument, ParsedEntry, ParsedPart, ParsedSection, ParsedSubpart,
)
from claw_easa.ingest.repository import upsert_source_document_from_values


ANNEX_I_BODY_LINES = [
    "List of occurrences related to the operation of the aircraft:",
    "(1) A collision or near collision, on the ground or in the air, with another aircraft.",
    "(2) A controlled-flight-into-terrain event or near event.",
    "(3) A take-off or landing incident.",
    "(4) A failure to achieve predicted performance during take-off or initial climb.",
    "(5) A fire, explosion, smoke, or toxic fumes.",
    "(6) An aircraft operation requiring the emergency use of oxygen.",
    "(7) An aircraft structural failure or engine disintegration.",
    "(8) Multiple malfunctions of one or more aircraft systems.",
    "(9) Any flight crew incapacitation.",
    "(10) Any fuel quantity level requiring the declaration of an emergency.",
    "(11) Crew fatigue impacting or potentially impacting their ability to perform safely their flight duties.",
    "(12) A runway incursion classified as severity A.",
    "(13) A take-off or landing from an incorrect runway.",
    "(14) Any use of the emergency evacuation means.",
]


def _seed_two_sources(db: Database) -> tuple[int, int]:
    """Seed occurrence-reporting and air-ops with a few entries each."""
    occ_id = upsert_source_document_from_values(
        db, slug="occurrence-reporting", source_family="rulebook",
        title="EAR for Occurrence Reporting (Regulation (EU) No 376/2014)",
    )

    occ_entries = [
        ParsedEntry(
            entry_ref="Article 4",
            entry_type="IR",
            title="Article 4 — Mandatory reporting",
            body_lines=[
                "1. Occurrences which may represent a significant risk to aviation safety",
                "shall be reported by the persons listed in paragraph 6.",
            ],
            source_locator="0", sort_order=0,
        ),
        ParsedEntry(
            entry_ref="ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT",
            entry_type="INFO",
            title="THE AIRCRAFT",
            body_lines=ANNEX_I_BODY_LINES,
            source_locator="1", sort_order=1,
        ),
    ]
    occ_section = ParsedSection(title="Main", entries=occ_entries, sort_order=0)
    occ_subpart = ParsedSubpart(code="General", title="General", sections=[occ_section], sort_order=0)
    occ_part = ParsedPart(code="Reg376", annex="", title="Regulation 376/2014", subparts=[occ_subpart], sort_order=0)
    occ_doc = ParsedDocument(title="Occurrence Reporting", parts=[occ_part])

    persister = CanonicalPersister(db)
    persister.persist_document(occ_id, occ_doc)

    ops_id = upsert_source_document_from_values(
        db, slug="air-ops", source_family="ear",
        title="EAR for Air Operations (Regulation (EU) No 965/2012)",
    )

    ops_entries = [
        ParsedEntry(
            entry_ref="CAT.GEN.MPA.100",
            entry_type="IR",
            title="CAT.GEN.MPA.100 Crew responsibilities",
            body_lines=[
                "The crew member shall be responsible for the proper execution",
                "of his or her duties that are related to the safety of the aircraft.",
            ],
            source_locator="0", sort_order=0,
        ),
        ParsedEntry(
            entry_ref="ORO.FTL.120",
            entry_type="IR",
            title="ORO.FTL.120 Fatigue risk management (FRM)",
            body_lines=[
                "The operator shall establish, implement and maintain a FRM",
                "as an integral part of its management system.",
            ],
            source_locator="1", sort_order=1,
        ),
    ]
    ops_section = ParsedSection(title="Main", entries=ops_entries, sort_order=0)
    ops_subpart = ParsedSubpart(code="General", title="General", sections=[ops_section], sort_order=0)
    ops_part = ParsedPart(code="CAT", annex="", title="Air Ops", subparts=[ops_subpart], sort_order=0)
    ops_doc = ParsedDocument(title="Air Operations", parts=[ops_part])

    persister.persist_document(ops_id, ops_doc)

    return occ_id, ops_id


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
    _seed_two_sources(database)
    yield database
    database.close()
    reset_settings()


# ── Chunking tests ─────────────────────────────────────────────────────


class TestListItemChunking:
    def test_long_annex_is_split(self):
        from claw_easa.retrieval.chunking import build_list_item_chunks

        entry = {
            "id": 42,
            "entry_ref": "ANNEX I",
            "entry_type": "INFO",
            "title": "OCCURRENCES",
            "body_text": "\n".join(ANNEX_I_BODY_LINES),
            "slug": "occurrence-reporting",
            "part_code": "Reg376",
            "subpart_code": "General",
            "section_title": "Main",
        }
        chunks = build_list_item_chunks(entry)
        assert len(chunks) == 14
        assert all(c["chunk_kind"] == "list_item" for c in chunks)
        assert all(c["entry_id"] == 42 for c in chunks)

        fatigue_chunks = [c for c in chunks if "fatigue" in c["chunk_text"].lower()]
        assert len(fatigue_chunks) == 1
        assert "(11)" in fatigue_chunks[0]["chunk_text"]

    def test_short_entry_not_split(self):
        from claw_easa.retrieval.chunking import build_list_item_chunks

        entry = {
            "id": 1,
            "entry_ref": "ORO.FTL.110",
            "entry_type": "IR",
            "title": "Operator responsibilities",
            "body_text": "The operator shall publish duty rosters.",
            "slug": "air-ops",
            "part_code": "ORO",
            "subpart_code": "FTL",
            "section_title": "Main",
        }
        chunks = build_list_item_chunks(entry)
        assert chunks == []

    def test_whole_chunk_still_created(self):
        from claw_easa.retrieval.chunking import build_whole_entry_chunk

        entry = {
            "id": 42,
            "entry_ref": "ANNEX I",
            "entry_type": "INFO",
            "title": "OCCURRENCES",
            "body_text": "\n".join(ANNEX_I_BODY_LINES),
            "slug": "occurrence-reporting",
            "part_code": "Reg376",
            "subpart_code": "General",
            "section_title": "Main",
        }
        chunk = build_whole_entry_chunk(entry)
        assert chunk["chunk_kind"] == "whole"
        assert "fatigue" in chunk["chunk_text"].lower()


# ── Source-scoped retrieval tests ──────────────────────────────────────


class TestSourceScopedRetrieval:
    def test_refs_unscoped(self, db):
        from claw_easa.retrieval.exact import search_references

        rows = search_references(db, "fatigue")
        slugs = {r["slug"] for r in rows}
        assert len(slugs) >= 1

    def test_refs_scoped_to_occurrence_reporting(self, db):
        from claw_easa.retrieval.exact import search_references

        rows = search_references(db, "fatigue", slug="occurrence-reporting")
        assert len(rows) >= 1
        assert all(r["slug"] == "occurrence-reporting" for r in rows)

    def test_refs_scoped_to_air_ops(self, db):
        from claw_easa.retrieval.exact import search_references

        rows = search_references(db, "fatigue", slug="air-ops")
        assert all(r["slug"] == "air-ops" for r in rows)

    def test_snippets_scoped(self, db):
        from claw_easa.retrieval.snippets import search_snippets

        rows = search_snippets(db, "crew fatigue", slug="occurrence-reporting")
        assert all(r["slug"] == "occurrence-reporting" for r in rows)


# ── Source-aware boosting tests ────────────────────────────────────────


class TestSourceAwareBoosting:
    def test_boost_when_slug_words_in_query(self):
        from claw_easa.retrieval.hybrid import _source_relevance_boost

        query_words = {"occurrence", "reporting", "crew", "fatigue"}
        boost = _source_relevance_boost(query_words, "occurrence-reporting")
        assert boost > 0

    def test_no_boost_when_no_overlap(self):
        from claw_easa.retrieval.hybrid import _source_relevance_boost

        query_words = {"fatigue", "crew"}
        boost = _source_relevance_boost(query_words, "air-ops")
        assert boost == 0

    def test_boost_is_capped(self):
        from claw_easa.retrieval.hybrid import _source_relevance_boost

        query_words = {"a", "b", "c", "d", "e", "f"}
        boost = _source_relevance_boost(query_words, "a-b-c-d-e-f")
        assert boost <= 0.15


# ── Hybrid with slug tests ────────────────────────────────────────────


class TestHybridSlug:
    def test_hybrid_unscoped_returns_both_sources(self, db):
        from claw_easa.retrieval.hybrid import hybrid_search

        rows = hybrid_search(db, "fatigue crew")
        slugs = {r.get("slug") for r in rows}
        assert "occurrence-reporting" in slugs or "air-ops" in slugs

    def test_hybrid_scoped_restricts(self, db):
        from claw_easa.retrieval.hybrid import hybrid_search

        rows = hybrid_search(db, "crew", slug="occurrence-reporting")
        assert len(rows) >= 1
        assert all(r.get("slug") == "occurrence-reporting" for r in rows)


# ── FTS compat edge cases ─────────────────────────────────────────────


class TestFTSCompat:
    def test_empty_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query("")
        assert not result.has_terms

    def test_phrase_query(self):
        from claw_easa.retrieval.fts_compat import to_fts5_query

        result = to_fts5_query('"crew fatigue"')
        assert result.has_terms
        assert '"crew fatigue"' in result.match_expr
