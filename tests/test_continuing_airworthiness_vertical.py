"""Behaviour 5 — the Continuing Airworthiness rulebook, end to end.

Regulation (EU) No 1321/2014 splits into eight annexes, and two of the shapes
it uses are not read correctly.

*Numeric part codes*: ``PART_PATTERN`` requires the part code to start with a
letter, so ``ANNEX II (Part-145)``, ``ANNEX III (Part-66)`` and ``ANNEX IV
(Part-147)`` produce no part.  Every ``145.A.xxx`` requirement is filed under
the preceding ``ANNEX I (Part-M)`` — the same misattribution that lettered
annexes suffered, and it names the wrong regulation part in a citation.

*Un-numbered soft law*: Part-M states its acceptable means of compliance as
``AMC M.A.201(e)``, not ``AMC1 M.A.201(e)``.  ``ARTICLE_AMC_PATTERN`` requires
a digit after ``AMC``, so the heading falls through to the text-based
implementing-rule branch and is typed ``IR`` — non-binding guidance presented
as binding law.  ``GM M.A.302(b)`` fails the same way.

The last test walks the real service path (import → parse → SQLite → lookup)
rather than driving the parser and persister directly, so a regression
anywhere along that chain is caught here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument, ParsedEntry
from claw_easa.ingest.repository import (
    get_document_by_slug,
    reference_exists,
    upsert_source_document_from_values,
)
from claw_easa.ingest.service import _open_db, parse_source
from claw_easa.retrieval.exact import lookup_reference

FIXTURE = Path(__file__).parent / "fixtures" / "continuing_airworthiness_part_m_camo.xml"
DOC_TITLE = "Easy Access Rules for Continuing Airworthiness"
SLUG = "continuing-airworthiness"


@pytest.fixture
def db(tmp_path):
    reset_settings()
    database = Database(settings=Settings(data_dir=str(tmp_path), db_file="test.db"))
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()
    reset_settings()


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Point the service-level data directory at a scratch path."""
    monkeypatch.setenv("CLAW_EASA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAW_EASA_DB_FILE", "test.db")
    reset_settings()
    yield tmp_path
    reset_settings()


def _parse_fixture() -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(FIXTURE, DOC_TITLE)


def _entries(doc: ParsedDocument) -> list[ParsedEntry]:
    return [
        entry
        for part in doc.parts
        for subpart in part.subparts
        for section in subpart.sections
        for entry in section.entries
    ]


def _entry(doc: ParsedDocument, ref: str) -> ParsedEntry:
    for entry in _entries(doc):
        if entry.entry_ref == ref:
            return entry
    raise AssertionError(
        f"no entry with ref {ref!r} was parsed; parsed refs were "
        f"{[e.entry_ref for e in _entries(doc)]}"
    )


def _part_of(doc: ParsedDocument, ref: str) -> str:
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref == ref:
                        return part.code
    raise AssertionError(f"no entry with ref {ref!r} was parsed")


# ── Numeric part codes ──────────────────────────────────────────────────


def test_numeric_annex_is_recognised_as_its_own_part():
    doc = _parse_fixture()

    part_codes = [part.code for part in doc.parts]

    assert "145" in part_codes, (
        f"ANNEX II (Part-145) produced no part; parsed parts were {part_codes}"
    )


def test_numeric_part_keeps_its_annex_label():
    doc = _parse_fixture()

    part_145 = next((p for p in doc.parts if p.code == "145"), None)

    assert part_145 is not None and part_145.annex == "II", (
        f"Part-145 annex label is {getattr(part_145, 'annex', None)!r}, expected 'II'"
    )


def test_145_requirements_are_not_attached_to_part_m():
    doc = _parse_fixture()

    assert _part_of(doc, "145.A.30") == "145", (
        "145.A.30 is filed under Part-M, so the citation names the wrong "
        "regulation part"
    )


def test_part_m_requirements_stay_in_part_m():
    """Guard against over-correcting: M.A.xxx must not move to Part-145."""
    doc = _parse_fixture()

    assert _part_of(doc, "M.A.201") == "M"
    assert _part_of(doc, "M.A.302") == "M"


def test_camo_requirements_stay_in_part_camo():
    """Part-145 sits between Part-M and Part-CAMO and must not absorb it."""
    doc = _parse_fixture()

    assert _part_of(doc, "CAMO.A.200") == "CAMO"


# ── IR / AMC / GM typing ────────────────────────────────────────────────


def test_unnumbered_amc_is_typed_as_amc():
    doc = _parse_fixture()

    entry = _entry(doc, "AMC M.A.201(e)")

    assert entry.entry_type == "AMC", (
        f"'AMC M.A.201(e)' is an acceptable means of compliance but was typed "
        f"{entry.entry_type!r}, presenting guidance as binding law"
    )


def test_unnumbered_gm_is_typed_as_gm():
    doc = _parse_fixture()

    entry = _entry(doc, "GM M.A.302(b)")

    assert entry.entry_type == "GM", (
        f"'GM M.A.302(b)' is guidance material but was typed {entry.entry_type!r}"
    )


def test_unnumbered_amc_on_a_numeric_part_is_typed_as_amc():
    doc = _parse_fixture()

    entry = _entry(doc, "AMC 145.A.30(e)")

    assert entry.entry_type == "AMC", (
        f"'AMC 145.A.30(e)' was typed {entry.entry_type!r}"
    )


def test_numbered_amc_is_still_typed_as_amc():
    """Guard: the Part-CAMO numbered form must keep working."""
    doc = _parse_fixture()

    amc = [e for e in _entries(doc) if e.entry_ref.startswith("AMC1 CAMO.A.200")]

    assert amc and amc[0].entry_type == "AMC", (
        f"numbered AMC regressed; got {[(e.entry_ref, e.entry_type) for e in amc]}"
    )


def test_implementing_rules_are_still_typed_ir():
    """Guard: widening AMC/GM must not steal the implementing rules."""
    doc = _parse_fixture()

    for ref in ("M.A.201", "M.A.302", "145.A.30", "CAMO.A.200"):
        assert _entry(doc, ref).entry_type == "IR", (
            f"{ref} is an implementing rule but was typed "
            f"{_entry(doc, ref).entry_type!r}"
        )


def test_every_entry_heading_is_still_captured():
    """No heading may be dropped while tightening the reference patterns."""
    doc = _parse_fixture()

    refs = {e.entry_ref for e in _entries(doc)}
    expected = {
        "M.A.201", "AMC M.A.201(e)", "M.A.202", "M.A.302", "GM M.A.302(b)",
        "145.A.30", "AMC 145.A.30(e)", "CAMO.A.105", "CAMO.A.200",
    }

    assert expected <= refs, f"headings lost: {sorted(expected - refs)}"


# ── source → parse → SQLite → lookup ────────────────────────────────────


def test_lookup_reports_the_right_part_for_every_reference(db):
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    CanonicalPersister(db).persist_document(doc_id, _parse_fixture())

    for ref, expected_part in (
        ("M.A.201", "M"),
        ("M.A.302", "M"),
        ("145.A.30", "145"),
        ("CAMO.A.105", "CAMO"),
        ("CAMO.A.200", "CAMO"),
    ):
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} was not ingested at all"
        assert rows[0]["part_code"] == expected_part, (
            f"lookup reports part_code {rows[0]['part_code']!r} for {ref}, "
            f"expected {expected_part!r}"
        )


def test_lookup_preserves_entry_type_through_sqlite(db):
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    CanonicalPersister(db).persist_document(doc_id, _parse_fixture())

    for ref, expected_type in (
        ("M.A.201", "IR"),
        ("AMC M.A.201(e)", "AMC"),
        ("GM M.A.302(b)", "GM"),
        ("AMC 145.A.30(e)", "AMC"),
        ("CAMO.A.200", "IR"),
    ):
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} did not survive persistence"
        assert rows[0]["entry_type"] == expected_type, (
            f"{ref} is stored as {rows[0]['entry_type']!r}, expected "
            f"{expected_type!r}"
        )


def test_parse_source_ingests_the_fixture_and_lookup_resolves_it(tmp_env):
    """The real path: import a local file, parse it, query SQLite."""
    result = parse_source(SLUG, file=FIXTURE)

    assert result["entries"] > 0, (
        f"parse_source extracted no entries: {result}"
    )
    assert result["parts"] >= 3, (
        f"parse_source found {result['parts']} parts, expected Part-M, "
        f"Part-145 and Part-CAMO"
    )

    db = _open_db()
    try:
        doc = get_document_by_slug(db, SLUG)
        assert doc["status"] == "parsed", (
            f"document status is {doc['status']!r} after a successful parse"
        )

        m_rows = lookup_reference(db, "M.A.201")
        assert m_rows and m_rows[0]["part_code"] == "M"
        assert m_rows[0]["entry_type"] == "IR"

        camo_rows = lookup_reference(db, "CAMO.A.200")
        assert camo_rows and camo_rows[0]["part_code"] == "CAMO"

        maint_rows = lookup_reference(db, "145.A.30")
        assert maint_rows and maint_rows[0]["part_code"] == "145", (
            "145.A.30 does not resolve to Part-145 through the service path"
        )

        amc_rows = lookup_reference(db, "AMC M.A.201(e)")
        assert amc_rows and amc_rows[0]["entry_type"] == "AMC"

        assert reference_exists(db, "145.A.30")
        assert reference_exists(db, "CAMO.A.200")
    finally:
        db.close()
