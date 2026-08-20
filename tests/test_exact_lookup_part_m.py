"""Behaviour 2 — ``lookup M.A.201`` must find the Part-M requirement.

``ARTICLE_IR_PATTERN`` requires at least two leading uppercase letters, so
Part-M references such as ``M.A.201`` never match it.  The heading falls
through to the ``INFO`` catch-all, which stores the *whole heading text* as
the reference: ``"M.A.201 Responsibilities"``.  ``lookup_reference`` compares
``entry_ref`` for equality, so ``claw-easa lookup M.A.201`` returns nothing
for a rule that is present in the corpus.

Both halves are pinned here: the reference must be extracted without its
title suffix, and exact lookup must still resolve ``M.A.201`` for corpora
already stored with the suffix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import (
    EASAOfficeXMLParser,
    ParsedDocument,
    ParsedEntry,
    ParsedPart,
    ParsedSection,
    ParsedSubpart,
)
from claw_easa.ingest.repository import reference_exists, upsert_source_document_from_values
from claw_easa.retrieval.exact import lookup_reference

FIXTURE = Path(__file__).parent / "fixtures" / "continuing_airworthiness_part_m_camo.xml"
DOC_TITLE = "Easy Access Rules for Continuing Airworthiness"


@pytest.fixture
def db(tmp_path):
    reset_settings()
    database = Database(settings=Settings(data_dir=str(tmp_path), db_file="test.db"))
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()
    reset_settings()


def _entries(doc: ParsedDocument) -> list[ParsedEntry]:
    return [
        entry
        for part in doc.parts
        for subpart in part.subparts
        for section in subpart.sections
        for entry in section.entries
    ]


def _parse_fixture() -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(FIXTURE, DOC_TITLE)


def _ingest_fixture(db: Database) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug="continuing-airworthiness", source_family="ear", title=DOC_TITLE,
    )
    CanonicalPersister(db).persist_document(doc_id, _parse_fixture())
    return doc_id


def test_part_m_reference_is_extracted_without_its_title_suffix():
    refs = [entry.entry_ref for entry in _entries(_parse_fixture())]

    assert "M.A.201" in refs, (
        f"M.A.201 was not extracted as a reference; parsed refs were {refs}"
    )


def test_part_m_requirement_is_typed_as_an_implementing_rule():
    entry = next(
        e for e in _entries(_parse_fixture()) if e.entry_ref.startswith("M.A.201")
    )

    assert entry.entry_type == "IR", (
        f"M.A.201 is an implementing rule but was typed {entry.entry_type!r}"
    )


def test_exact_lookup_finds_m_a_201_after_ingest(db):
    _ingest_fixture(db)

    rows = lookup_reference(db, "M.A.201")

    assert rows, "'claw-easa lookup M.A.201' found nothing after ingesting Part-M"


def test_exact_lookup_finds_m_a_201_stored_with_title_suffix(db):
    """Corpora already persisted by the current parser keep the suffix."""
    doc_id = upsert_source_document_from_values(
        db, slug="continuing-airworthiness", source_family="ear", title=DOC_TITLE,
    )
    stored = ParsedEntry(
        entry_ref="M.A.201 Responsibilities",
        entry_type="INFO",
        title="M.A.201 Responsibilities",
        body_lines=["The owner shall be responsible for the continuing airworthiness."],
        sort_order=0,
        source_locator="paragraphs:1-2",
    )
    section = ParsedSection(title="General", sort_order=1, entries=[stored])
    subpart = ParsedSubpart(code="B", title="Accountability", sort_order=1, sections=[section])
    part = ParsedPart(code="M", title="ANNEX I (Part-M)", annex="I", sort_order=1, subparts=[subpart])
    CanonicalPersister(db).persist_document(
        doc_id, ParsedDocument(title=DOC_TITLE, parts=[part]),
    )

    rows = lookup_reference(db, "M.A.201")

    assert rows, (
        "exact lookup of M.A.201 misses the entry stored as "
        "'M.A.201 Responsibilities'"
    )


def test_reference_exists_reports_m_a_201(db):
    _ingest_fixture(db)

    assert reference_exists(db, "M.A.201"), (
        "M.A.201 is ingested but reference_exists() denies it, so strict "
        "ref-only answering treats it as out of corpus"
    )
