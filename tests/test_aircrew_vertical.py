"""Behaviour 8 — the Aircrew rulebook (Part-ORA), end to end.

Regulation (EU) No 1178/2011 ANNEX VII (Part-ORA) states the organisation
requirements for aircrew, and its December 2025 revision uses three shapes the
parser does not read.

*Roman-numbered sections*: Part-ORA writes ``SECTION I – General`` and
``SECTION II – Management`` where Air Ops writes ``SECTION 1``.
``SECTION_PATTERN`` matches Arabic digits only, so no section heading is
recognised and every rule of a subpart collapses into one ``General``
section — ``ORA.GEN.200``, a management requirement, is reported as if it
were stated under the general section.

*Mixed-case references*: the aero-medical centre rules are spelled
``ORA.AeMC.105``, ``ORA.AeMC.115`` and ``ORA.AeMC.200``.  ``REFERENCE_CORE``
accepts uppercase components only, so each one is truncated at the lowercase
letter and stored as ``ORA.A``.  Three distinct rules collide under one
reference, and a lookup of ``ORA.AeMC.115`` answers with whichever of them
was persisted first.

*Chapter-level soft law*: SUBPART ATO subdivides its third section into
chapters, so the AMC of a chapter rule is one heading level deeper than
elsewhere — ``AMC1 ORA.ATO.300 General`` is a ``Heading6AMC``.
``_identify_entry`` reads no style below level 5, so the AMC is not an entry
at all: its text is appended to the body of the implementing rule above it,
presenting guidance material as the rule's own binding text.

``Heading6OrgManual`` and ``Heading7OrgManual`` are the sub-headings EASA
sets *inside* the body of a soft-law entry ('DISTANCE LEARNING'), at the same
and at a deeper level than the entry heading.  They are body text; the guard
tests below keep reading deeper headings from filing them as rules.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.diagnostics import coverage_report, format_report
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument, ParsedEntry
from claw_easa.ingest.repository import (
    get_document_by_slug,
    upsert_source_document_from_values,
)
from claw_easa.ingest.service import _open_db, parse_source
from claw_easa.retrieval.exact import lookup_reference

FIXTURE = Path(__file__).parent / "fixtures" / "aircrew_part_ora_december_2025.xml"
DOC_TITLE = "Easy Access Rules for Aircrew"
SLUG = "aircrew"

#: Every implementing rule of the fixture, with the subpart that must own it.
IMPLEMENTING_RULES = (
    ("ORA.GEN.105", "GEN"),
    ("ORA.GEN.115", "GEN"),
    ("ORA.GEN.200", "GEN"),
    ("ORA.ATO.100", "ATO"),
    ("ORA.ATO.300", "ATO"),
    ("ORA.AeMC.105", "AeMC"),
    ("ORA.AeMC.115", "AeMC"),
    ("ORA.AeMC.200", "AeMC"),
)

#: Sub-headings EASA sets inside a soft-law body.  None is a rule.
BODY_SUBHEADINGS = (
    "GENERAL",
    "NON-COMPLEX ORGANISATIONS - GENERAL",
    "DISTANCE LEARNING",
)


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
        f"{sorted(e.entry_ref for e in _entries(doc))}"
    )


def _subpart_of(doc: ParsedDocument, ref: str) -> str:
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref == ref:
                        return subpart.code
    raise AssertionError(f"no entry with ref {ref!r} was parsed")


def _section_of(doc: ParsedDocument, ref: str) -> str:
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref == ref:
                        return section.title
    raise AssertionError(f"no entry with ref {ref!r} was parsed")


def _ingest(db: Database) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision="December 2025",
    )
    CanonicalPersister(db).persist_document(doc_id, _parse_fixture())
    return doc_id


# ── Coverage of Part-ORA ────────────────────────────────────────────────


def test_the_document_layout_is_recognised():
    doc = _parse_fixture()

    assert doc.parser_mode == "part", (
        f"the Part-ORA annex layout parsed as {doc.parser_mode!r}"
    )


def test_part_ora_is_a_part_carrying_its_annex_label():
    doc = _parse_fixture()

    labels = {part.code: part.annex for part in doc.parts}

    assert labels == {"ORA": "VII"}, (
        f"annex labels are {labels}, expected ORA under ANNEX VII"
    )


def test_the_three_ora_subparts_are_parsed():
    doc = _parse_fixture()

    codes = [sp.code for part in doc.parts for sp in part.subparts]

    assert codes == ["GEN", "ATO", "AeMC"], (
        f"SUBPART GEN, ATO and AeMC are not three distinct subparts; parsed "
        f"subparts were {codes}"
    )


def test_every_implementing_rule_is_parsed_under_its_own_subpart():
    doc = _parse_fixture()

    refs = {e.entry_ref for e in _entries(doc)}
    expected = {ref for ref, _subpart in IMPLEMENTING_RULES}

    assert expected <= refs, (
        f"rules missing from the parse: {sorted(expected - refs)}; parsed "
        f"refs were {sorted(refs)}"
    )
    for ref, subpart in IMPLEMENTING_RULES:
        assert _subpart_of(doc, ref) == subpart, (
            f"{ref} is filed under SUBPART {_subpart_of(doc, ref)!r}, expected "
            f"SUBPART {subpart!r}"
        )


# ── Mixed-case references ───────────────────────────────────────────────


def test_a_mixed_case_reference_is_not_truncated():
    """``ORA.AeMC.115`` must survive as itself, not as ``ORA.A``."""
    doc = _parse_fixture()

    refs = [e.entry_ref for e in _entries(doc)]

    assert "ORA.A" not in refs, (
        "a mixed-case reference was truncated at its first lowercase letter, "
        f"so distinct AeMC rules collide under one reference: {refs}"
    )


def test_each_aero_medical_rule_keeps_its_own_reference():
    doc = _parse_fixture()

    aemc = [e.entry_ref for e in _entries(doc) if e.entry_ref.startswith("ORA.AeMC")]

    assert aemc == ["ORA.AeMC.105", "ORA.AeMC.115", "ORA.AeMC.200"], (
        f"the aero-medical centre rules parsed as {aemc}"
    )


def test_a_mixed_case_rule_keeps_its_own_body():
    """A collision would answer ORA.AeMC.115 with a different rule's text."""
    doc = _parse_fixture()

    body = " ".join(_entry(doc, "ORA.AeMC.115").body_lines)

    assert "legal capacity" in body, (
        f"ORA.AeMC.115 (Application) carries the wrong body text: {body!r}"
    )


# ── Roman-numbered sections ─────────────────────────────────────────────


def test_roman_numbered_sections_are_recognised():
    doc = _parse_fixture()

    titles = [
        section.title
        for part in doc.parts
        for subpart in part.subparts
        for section in subpart.sections
    ]

    assert "SECTION II – Management" in titles, (
        f"a Roman-numbered SECTION heading produced no section; parsed "
        f"sections were {titles}"
    )


def test_a_management_rule_is_not_reported_under_the_general_section():
    doc = _parse_fixture()

    assert _section_of(doc, "ORA.GEN.200") == "SECTION II – Management", (
        f"ORA.GEN.200 is stated under SECTION II but is filed under section "
        f"{_section_of(doc, 'ORA.GEN.200')!r}"
    )
    assert _section_of(doc, "ORA.GEN.105") == "SECTION I – General", (
        f"ORA.GEN.105 is stated under SECTION I but is filed under section "
        f"{_section_of(doc, 'ORA.GEN.105')!r}"
    )


def test_section_headings_do_not_become_entries():
    """Guard: reading Roman sections must not file their titles as rules."""
    doc = _parse_fixture()

    junk = [
        e.entry_ref for e in _entries(doc)
        if e.entry_ref.upper().startswith(("SECTION", "SUBPART", "CHAPTER"))
    ]

    assert junk == [], f"structural headings were stored as entries: {junk}"


# ── Chapter-level soft law ──────────────────────────────────────────────


def test_a_chapter_rules_amc_is_an_entry_of_its_own():
    doc = _parse_fixture()

    amc = [e for e in _entries(doc) if e.entry_ref.startswith("AMC1 ORA.ATO.300")]

    assert amc, (
        "'AMC1 ORA.ATO.300' is stated one heading level deeper than elsewhere "
        "and was not parsed as an entry at all; parsed refs were "
        f"{sorted(e.entry_ref for e in _entries(doc))}"
    )
    assert amc[0].entry_type == "AMC", (
        f"'AMC1 ORA.ATO.300' was typed {amc[0].entry_type!r}"
    )


def test_the_amc_text_is_not_appended_to_the_rule_it_qualifies():
    """Guidance swallowed into an IR body reads as binding law."""
    doc = _parse_fixture()

    rule_body = " ".join(_entry(doc, "ORA.ATO.300").body_lines)

    assert "supported by a tutor" not in rule_body, (
        f"the AMC text was stored inside the body of ORA.ATO.300: {rule_body!r}"
    )
    assert "approval of the competent authority" in rule_body, (
        f"ORA.ATO.300 lost its own body text: {rule_body!r}"
    )


def test_body_subheadings_are_not_stored_as_rules():
    """Guard: 'DISTANCE LEARNING' is body text of an AMC, not an entry."""
    doc = _parse_fixture()

    entries = _entries(doc)
    junk = [e.entry_ref for e in entries if e.entry_ref in BODY_SUBHEADINGS]

    assert junk == [], f"body sub-headings were stored as entries: {junk}"
    assert [e.entry_ref for e in entries if e.entry_type == "INFO"] == [], (
        "an unreferenced heading was stored as an INFO entry"
    )


def test_a_body_subheading_stays_with_the_entry_it_belongs_to():
    doc = _parse_fixture()

    amc = next(e for e in _entries(doc) if e.entry_ref.startswith("AMC1 ORA.ATO.300"))

    assert "DISTANCE LEARNING" in amc.body_lines, (
        f"the AMC lost its sub-heading; body was {amc.body_lines}"
    )


# ── IR / AMC / GM typing ────────────────────────────────────────────────


def test_implementing_rules_are_typed_ir():
    doc = _parse_fixture()

    for ref, _subpart in IMPLEMENTING_RULES:
        assert _entry(doc, ref).entry_type == "IR", (
            f"{ref} is an implementing rule but was typed "
            f"{_entry(doc, ref).entry_type!r}"
        )


def test_soft_law_is_never_typed_as_binding_law():
    doc = _parse_fixture()

    soft = [
        e for e in _entries(doc)
        if e.entry_ref.startswith(("AMC1", "GM1"))
    ]

    assert len(soft) == 6, (
        f"the fixture states six AMC/GM entries, {len(soft)} were parsed: "
        f"{[e.entry_ref for e in soft]}"
    )
    mistyped = [
        (e.entry_ref, e.entry_type) for e in soft
        if e.entry_type != e.entry_ref[:3].rstrip("1")
    ]
    assert mistyped == [], f"soft law mistyped: {mistyped}"


# ── source → parse → SQLite → lookup ────────────────────────────────────


def test_lookup_resolves_every_implementing_rule(db):
    _ingest(db)

    for ref, subpart in IMPLEMENTING_RULES:
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} did not survive persistence"
        assert rows[0]["entry_ref"] == ref, (
            f"a lookup of {ref} answers with {rows[0]['entry_ref']!r}"
        )
        assert rows[0]["subpart_code"] == subpart, (
            f"lookup reports subpart {rows[0]['subpart_code']!r} for {ref}, "
            f"expected {subpart!r}"
        )


def test_no_stored_reference_is_disambiguated_by_a_collision_suffix(db):
    """``ORA.A#2`` means two distinct rules were stored under one reference."""
    _ingest(db)

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT entry_ref FROM regulation_entries ORDER BY id")
            refs = [row["entry_ref"] for row in cur.fetchall()]

    assert [ref for ref in refs if "#" in ref] == [], (
        f"distinct rules collided under one reference: {refs}"
    )


def test_lookup_answers_a_mixed_case_reference_with_that_rule(db):
    _ingest(db)

    rows = lookup_reference(db, "ORA.AeMC.115")

    assert len(rows) == 1, (
        f"ORA.AeMC.115 resolves to {len(rows)} entries: "
        f"{[(r['entry_ref'], r['title']) for r in rows]}"
    )
    assert "legal capacity" in rows[0]["body_text"], (
        f"ORA.AeMC.115 answers with the body of another rule: {rows[0]['title']!r}"
    )


def test_lookup_preserves_entry_type_through_sqlite(db):
    _ingest(db)

    for ref, expected_type in (
        ("ORA.GEN.200", "IR"),
        ("ORA.AeMC.200", "IR"),
        ("AMC1 ORA.GEN.200(a)(1);(2);(3);(5)", "AMC"),
        ("GM1 ORA.GEN.130(c)", "GM"),
        ("AMC1 ORA.ATO.300", "AMC"),
        ("AMC1 ORA.AeMC.200", "AMC"),
    ):
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} does not resolve by exact lookup"
        assert rows[0]["entry_type"] == expected_type, (
            f"{ref} is stored as {rows[0]['entry_type']!r}, expected "
            f"{expected_type!r}"
        )


def test_parse_source_ingests_the_fixture_and_lookup_resolves_it(tmp_env):
    """The real path: import a local file, parse it, query SQLite."""
    result = parse_source(SLUG, file=FIXTURE)

    assert result["parts"] == 1, (
        f"parse_source found {result['parts']} parts, expected Part-ORA alone"
    )
    assert result["entries"] == len(_entries(_parse_fixture())), (
        f"parse_source persisted {result['entries']} entries of "
        f"{len(_entries(_parse_fixture()))} parsed"
    )

    db = _open_db()
    try:
        assert get_document_by_slug(db, SLUG)["status"] == "parsed"

        for ref, subpart in IMPLEMENTING_RULES:
            rows = lookup_reference(db, ref)
            assert rows and rows[0]["subpart_code"] == subpart, (
                f"{ref} does not resolve to SUBPART {subpart} through the "
                f"service path"
            )
    finally:
        db.close()


# ── diagnostics ─────────────────────────────────────────────────────────


def test_coverage_report_captures_every_entry_heading():
    report = coverage_report(FIXTURE, DOC_TITLE)

    assert report.uncaptured_headings == [], (
        "entry headings were left uncaptured:\n" + format_report(report)
    )
    assert "Verdict: PASS" in format_report(report), (
        "the Part-ORA corpus does not pass its own coverage report:\n"
        + format_report(report)
    )
