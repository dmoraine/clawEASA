"""Behaviour 3 — CAMO.* references belong to Part-CAMO, not Part-M.

``PART_PATTERN`` is ``ANNEX\\s+([IVX]+)\\s+\\(Part-([A-Z]+)\\)``: the annex
number must be followed by whitespace, so the lettered annexes of Regulation
(EU) No 1321/2014 — ``ANNEX Vb (Part-ML)``, ``ANNEX Vc (Part-CAMO)``,
``ANNEX Vd (Part-CAO)`` — are not recognised as part headings.  Part-CAMO is
never created and every CAMO.A.xxx requirement is filed under the preceding
``ANNEX I (Part-M)``, so citations name the wrong regulation part.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument
from claw_easa.ingest.repository import upsert_source_document_from_values
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


def _parse_fixture() -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(FIXTURE, DOC_TITLE)


def _part_of(doc: ParsedDocument, ref_prefix: str) -> str:
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref.startswith(ref_prefix):
                        return part.code
    raise AssertionError(f"no entry starting with {ref_prefix!r} was parsed")


def test_annex_vc_is_recognised_as_part_camo():
    doc = _parse_fixture()

    part_codes = [part.code for part in doc.parts]

    assert "CAMO" in part_codes, (
        f"ANNEX Vc (Part-CAMO) produced no part; parsed parts were {part_codes}"
    )


def test_lettered_annex_keeps_its_annex_label():
    doc = _parse_fixture()

    camo = next((part for part in doc.parts if part.code == "CAMO"), None)

    assert camo is not None and camo.annex == "Vc", (
        f"Part-CAMO annex label is {getattr(camo, 'annex', None)!r}, expected 'Vc'"
    )


def test_camo_requirements_are_not_attached_to_part_m():
    doc = _parse_fixture()

    assert _part_of(doc, "CAMO.A.200") == "CAMO", (
        "CAMO.A.200 is filed under Part-M, so citations name the wrong part"
    )


def test_camo_amc_is_not_attached_to_part_m():
    doc = _parse_fixture()

    assert _part_of(doc, "AMC1 CAMO.A.200") == "CAMO", (
        "AMC1 CAMO.A.200 is filed under Part-M"
    )


def test_part_m_requirements_stay_in_part_m():
    """Guard against over-correcting: M.A.xxx must not move to Part-CAMO."""
    doc = _parse_fixture()

    assert _part_of(doc, "M.A.201") == "M"


def test_lookup_reports_part_camo_for_a_camo_reference(db):
    doc_id = upsert_source_document_from_values(
        db, slug="continuing-airworthiness", source_family="ear", title=DOC_TITLE,
    )
    CanonicalPersister(db).persist_document(doc_id, _parse_fixture())

    rows = lookup_reference(db, "CAMO.A.200")

    assert rows, "CAMO.A.200 was not ingested at all"
    assert rows[0]["part_code"] == "CAMO", (
        f"lookup reports part_code {rows[0]['part_code']!r} for CAMO.A.200"
    )
