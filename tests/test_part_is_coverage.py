"""Behaviour 4 — Part-IS / IS.I.OR must be representable, and its absence explicit.

Two gaps are pinned here.

*Representable*: the Easy Access Rules for Information Security (Regulations
(EU) 2023/203 and (EU) 2022/1645) have no slug alias, and their annexes are
titled ``ANNEX I (Part-IS.I.OR)`` / ``ANNEX II (Part-IS.AR)``.  The dotted
part code does not match ``PART_PATTERN``, so the document parses to zero
parts and zero entries — Part-IS cannot enter the corpus at all.

*Absence detected*: with Part-IS missing, ``search_references`` still answers
``IS.I.OR.200`` with unrelated Air Operations entries (the FTS query degrades
to the tokens ``IS I OR 200``), and nothing reports the source as missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import (
    EASAOfficeXMLParser,
    ParsedDocument,
    ParsedEntry,
    ParsedPart,
    ParsedSection,
    ParsedSubpart,
)
from claw_easa.ingest.repository import upsert_source_document_from_values
from claw_easa.ingest.sources import get_alias
from claw_easa.retrieval.exact import search_references

FIXTURE = Path(__file__).parent / "fixtures" / "information_security_part_is.xml"
DOC_TITLE = "Easy Access Rules for Information Security"


@pytest.fixture
def db(tmp_path):
    reset_settings()
    database = Database(settings=Settings(data_dir=str(tmp_path), db_file="test.db"))
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()
    reset_settings()


def _parse_fixture() -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(FIXTURE, DOC_TITLE)


def _refs(doc: ParsedDocument) -> list[str]:
    return [
        entry.entry_ref
        for part in doc.parts
        for subpart in part.subparts
        for section in subpart.sections
        for entry in section.entries
    ]


def _seed_air_ops_only(db: Database) -> None:
    doc_id = upsert_source_document_from_values(
        db, slug="air-ops", source_family="ear",
        title="Easy Access Rules for Air Operations",
    )
    entry = ParsedEntry(
        entry_ref="ORO.GEN.200",
        entry_type="IR",
        title="ORO.GEN.200 Management system",
        body_lines=[
            "The operator shall establish, implement and maintain a management system.",
            "It shall include a compliance monitoring function.",
        ],
        sort_order=0,
        source_locator="paragraphs:1-3",
    )
    section = ParsedSection(title="General", sort_order=1, entries=[entry])
    subpart = ParsedSubpart(code="GEN", title="General requirements", sort_order=1, sections=[section])
    part = ParsedPart(code="ORO", title="ANNEX III (Part-ORO)", annex="III", sort_order=1, subparts=[subpart])
    CanonicalPersister(db).persist_document(
        doc_id, ParsedDocument(title="Air Ops", parts=[part]),
    )


def test_information_security_slug_alias_is_registered():
    alias = get_alias("information-security")

    assert alias is not None, (
        "no slug alias for the Information Security EAR, so Part-IS cannot "
        "be fetched by name"
    )
    assert alias.source_family == "ear"


def test_part_is_annexes_are_recognised():
    doc = _parse_fixture()

    part_codes = [part.code for part in doc.parts]

    assert part_codes, (
        "the Information Security EAR parsed to zero parts; its dotted annex "
        "codes (Part-IS.I.OR / Part-IS.AR) are not recognised"
    )
    assert any("IS.I.OR" in code for code in part_codes), (
        f"no Part-IS.I.OR part; parsed parts were {part_codes}"
    )


def test_is_i_or_requirements_are_parsed():
    refs = _refs(_parse_fixture())

    assert "IS.I.OR.200" in refs, (
        f"IS.I.OR.200 was not extracted; parsed refs were {refs}"
    )
    assert "IS.I.OR.205" in refs, (
        f"IS.I.OR.205 was not extracted; parsed refs were {refs}"
    )


def test_missing_source_is_reported_as_a_coverage_anomaly():
    anomalies = detect_anomalies({"missing_sources": ["information-security"]})

    assert anomalies, (
        "a corpus with no Information Security source reports no anomaly, so "
        "the Part-IS gap is invisible"
    )
    assert any(a.category == "coverage" for a in anomalies), (
        f"no coverage anomaly; got {[(a.category, a.message) for a in anomalies]}"
    )
    assert any("information-security" in a.message for a in anomalies)


def test_no_coverage_anomaly_when_every_source_is_present():
    assert detect_anomalies({"missing_sources": []}) == []


def test_absent_is_i_or_reference_does_not_match_unrelated_entries(db):
    _seed_air_ops_only(db)

    rows = search_references(db, "IS.I.OR.200")

    unrelated = [r["entry_ref"] for r in rows if not r["entry_ref"].startswith("IS.")]
    assert not unrelated, (
        f"searching for IS.I.OR.200 in a corpus without Part-IS returned "
        f"{unrelated}, which reads as a confident answer about information "
        f"security requirements"
    )
