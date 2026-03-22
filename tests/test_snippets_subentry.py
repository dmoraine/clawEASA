"""Tests for the snippets sub-entry attribution fix.

Validates:
  - query-aware compact_snippet
  - subheading chunking (CS/AMC/GM split)
  - chunk enrichment in search_snippets
  - matched_subref extraction
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


BODY_WITH_SUBHEADINGS = (
    "This entry covers multiple CS items.\n"
    "\n"
    "CS FTL.1.200 Home base\n"
    "(a) The operator shall assign a home base to each crew member.\n"
    "(b) In the case of a change of home base, the operator shall give\n"
    "    the crew member at least 72 hours advance notice.\n"
    "\n"
    "CS FTL.1.205 Duty period\n"
    "(a) A flight duty period shall be calculated from the reporting time.\n"
    "(b) The maximum daily flight duty period shall be 13 hours.\n"
    "\n"
    "CS FTL.1.210 Rest periods\n"
    "(a) The minimum rest period shall be at least as long as the\n"
    "    preceding duty period, or 12 hours, whichever is greater.\n"
)


def _seed_entry_with_subheadings(db: Database) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug="air-ops-test", source_family="ear",
        title="Test Air Ops",
    )
    entry = ParsedEntry(
        entry_ref="AMC1 ORO.FTL.250",
        entry_type="AMC",
        title="AMC1 ORO.FTL.250 Fatigue management training",
        body_lines=BODY_WITH_SUBHEADINGS.strip().split("\n"),
        source_locator="0", sort_order=0,
    )
    section = ParsedSection(title="Main", entries=[entry], sort_order=0)
    subpart = ParsedSubpart(code="FTL", title="FTL", sections=[section], sort_order=0)
    part = ParsedPart(code="ORO", annex="", title="ORO", subparts=[subpart], sort_order=0)
    doc = ParsedDocument(title="Test Air Ops", parts=[part])

    persister = CanonicalPersister(db)
    persister.persist_document(doc_id, doc)
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
    _seed_entry_with_subheadings(database)
    yield database
    database.close()
    reset_settings()


# ── Query-aware compact_snippet ───────────────────────────────────────


class TestQueryAwareSnippet:
    def test_finds_matching_window(self):
        from claw_easa.retrieval.formatting import compact_snippet

        text = (
            "Line 1 about apples.\n"
            "Line 2 about bananas.\n"
            "Line 3 about cherries.\n"
            "Line 4 about dates.\n"
            "Line 5 about fatigue and crew safety.\n"
            "Line 6 about elderberries.\n"
            "Line 7 about figs.\n"
        )
        snippet = compact_snippet(text, max_lines=3, max_chars=500, query="fatigue crew")
        assert "fatigue" in snippet.lower()

    def test_no_query_takes_first_lines(self):
        from claw_easa.retrieval.formatting import compact_snippet

        text = "First line.\nSecond line.\nThird line.\nFourth line.\nFifth line.\n"
        snippet = compact_snippet(text, max_lines=2, max_chars=500)
        assert "First line" in snippet
        assert "Fourth line" not in snippet

    def test_short_text_unchanged_with_query(self):
        from claw_easa.retrieval.formatting import compact_snippet

        text = "Short text about fatigue."
        snippet = compact_snippet(text, max_lines=3, max_chars=500, query="fatigue")
        assert snippet == "Short text about fatigue."

    def test_empty_query_preserves_behavior(self):
        from claw_easa.retrieval.formatting import compact_snippet

        text = "Line 1.\nLine 2.\nLine 3.\nLine 4.\n"
        without_query = compact_snippet(text, max_lines=2, max_chars=500)
        with_empty_query = compact_snippet(text, max_lines=2, max_chars=500, query="")
        assert without_query == with_empty_query


# ── Subheading chunking ───────────────────────────────────────────────


class TestSubheadingChunking:
    def test_splits_on_cs_subheadings(self):
        from claw_easa.retrieval.chunking import build_subheading_chunks

        entry = {
            "id": 42,
            "entry_ref": "AMC1 ORO.FTL.250",
            "entry_type": "AMC",
            "title": "Fatigue management training",
            "body_text": BODY_WITH_SUBHEADINGS,
            "slug": "air-ops",
            "part_code": "ORO",
            "subpart_code": "FTL",
            "section_title": "Main",
        }
        chunks = build_subheading_chunks(entry)
        assert len(chunks) == 3
        assert all(c["chunk_kind"] == "subheading" for c in chunks)
        assert all(c["entry_id"] == 42 for c in chunks)

        home_base_chunks = [c for c in chunks if "Home base" in c["chunk_text"]]
        assert len(home_base_chunks) == 1
        assert "72 hours" in home_base_chunks[0]["chunk_text"]

        duty_chunks = [c for c in chunks if "FTL.1.205" in c["chunk_text"]]
        assert len(duty_chunks) == 1

    def test_short_body_not_split(self):
        from claw_easa.retrieval.chunking import build_subheading_chunks

        entry = {
            "id": 1,
            "entry_ref": "AMC1 ORO.FTL.250",
            "entry_type": "AMC",
            "title": "Short entry",
            "body_text": "CS FTL.1.200 Home base\nShort text.",
            "slug": "air-ops",
            "part_code": "ORO",
            "subpart_code": "FTL",
            "section_title": "Main",
        }
        chunks = build_subheading_chunks(entry)
        assert chunks == []

    def test_breadcrumbs_include_subheading(self):
        from claw_easa.retrieval.chunking import build_subheading_chunks

        entry = {
            "id": 42,
            "entry_ref": "AMC1 ORO.FTL.250",
            "entry_type": "AMC",
            "title": "Fatigue management training",
            "body_text": BODY_WITH_SUBHEADINGS,
            "slug": "air-ops",
            "part_code": "ORO",
            "subpart_code": "FTL",
            "section_title": "Main",
        }
        chunks = build_subheading_chunks(entry)
        for chunk in chunks:
            assert " > CS FTL" in chunk["breadcrumbs_text"]


# ── Chunk enrichment in search_snippets ───────────────────────────────


class TestChunkEnrichment:
    def _build_chunks(self, db: Database) -> None:
        from claw_easa.retrieval.indexing import RetrievalIndexer
        indexer = RetrievalIndexer(db)
        indexer.rebuild_chunks()

    def test_enrichment_attaches_chunk_text(self, db):
        self._build_chunks(db)
        from claw_easa.retrieval.snippets import search_snippets

        rows = search_snippets(db, "home base 72 hours")
        assert len(rows) >= 1
        enriched = [r for r in rows if r.get("chunk_text")]
        assert len(enriched) >= 1
        assert "Home base" in enriched[0]["chunk_text"]

    def test_enrichment_sets_matched_subref(self, db):
        self._build_chunks(db)
        from claw_easa.retrieval.snippets import search_snippets

        rows = search_snippets(db, "home base 72 hours")
        subref_rows = [r for r in rows if r.get("matched_subref")]
        assert len(subref_rows) >= 1
        assert "FTL.1.200" in subref_rows[0]["matched_subref"]

    def test_no_chunks_returns_original(self, db):
        from claw_easa.retrieval.snippets import search_snippets

        rows = search_snippets(db, "fatigue")
        assert len(rows) >= 1
        for row in rows:
            assert "body_text" in row

    def test_snippets_query_aware_display(self, db):
        self._build_chunks(db)
        from claw_easa.retrieval.snippets import search_snippets
        from claw_easa.retrieval.formatting import compact_snippet

        rows = search_snippets(db, "home base 72 hours")
        assert len(rows) >= 1
        text = rows[0].get("chunk_text") or rows[0].get("body_text", "")
        snippet = compact_snippet(text, max_lines=3, max_chars=500, query="home base 72 hours")
        assert "home base" in snippet.lower() or "72 hours" in snippet.lower()


# ── Subref extraction ─────────────────────────────────────────────────


class TestSubrefExtraction:
    def test_extracts_cs_subref(self):
        from claw_easa.retrieval.snippets import _extract_subref

        chunk_text = (
            "AMC1 ORO.FTL.250 (AMC) — Fatigue management training\n"
            "CS FTL.1.200 Home base\n"
            "(a) The operator shall assign a home base.\n"
        )
        subref = _extract_subref(chunk_text)
        assert subref is not None
        assert "CS FTL.1.200" in subref

    def test_no_subref_in_plain_text(self):
        from claw_easa.retrieval.snippets import _extract_subref

        chunk_text = (
            "AMC1 ORO.FTL.250 (AMC) — Fatigue management training\n"
            "(1) The operator shall provide training.\n"
        )
        subref = _extract_subref(chunk_text)
        assert subref is None
