"""The annex layout the Information Security rulebook is actually published in.

``test_part_is_vertical`` pins Part-IS provenance against a fixture written in
the Air Ops annex shape — ``ANNEX I (Part-IS.I.OR)`` at ``Heading1``.  EASA
does not publish it in that shape.  The real document consolidates two
regulations, spends ``Heading1`` on naming each of them, heads its annexes one
level down at ``Heading2``, and closes each annex heading with a bracketed
marker after the annex title:

    Heading1  Implementing Regulation (EU) 2023/203
    Heading2  ANNEX I — INFORMATION SECURITY — AUTHORITY REQUIREMENTS [PART-IS.AR]
    Heading2  ANNEX II — INFORMATION SECURITY — ORGANISATION REQUIREMENTS [PART-IS.I.OR]
    Heading1  Delegated Regulation (EU) 2022/1645
    Heading2  ANNEX — INFORMATION SECURITY — ORGANISATION REQUIREMENTS [PART-IS.D.OR]

A parser that fixes the annex level at ``Heading1`` and requires a
parenthesised marker directly after a roman numeral reads no part out of that
at all.  The document then falls through to article parsing and every rule in
it — the authority requirements, both sets of organisation requirements — is
filed under a single ``ARTICLES`` part.  That is what the canary measured: the
source parsed, 320 entries landed, and neither (EU) 2023/203 nor (EU)
2022/1645 could be said to state any of them.

The generalisation under test reads the annex level off the document instead
of assuming it, accepts either marker spelling, and allows an unnumbered annex
— while still requiring the marker to *close* the heading, which is what keeps
the amendment annexes ('ANNEX III — ... ANNEXES VI (Part-ARA) and VII
(Part-ORA) to Regulation (EU) No 1178/2011') from inventing parts that the
Information Security regulations do not contain.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.manifest import build_manifest
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument
from claw_easa.ingest.regulations import regulations_for
from claw_easa.ingest.repository import (
    get_document_by_slug,
    parts_by_regulation,
    record_source_regulations,
    upsert_source_document_from_values,
)
from claw_easa.ingest.service import _open_db, parse_source
from claw_easa.retrieval.exact import lookup_reference

FIXTURES = Path(__file__).parent / "fixtures"
NESTED = FIXTURES / "information_security_nested_annexes.xml"
NESTED_UNMAPPED = FIXTURES / "information_security_nested_unmapped_annex.xml"

#: The Air Ops annex shape, for the cross-layout regression check: an annex
#: headed at ``Heading1`` with a parenthesised marker after the numeral.
AIR_OPS = FIXTURES / "air_operations_oro_cat_spa_march_2026.xml"
CONTINUING_AIRWORTHINESS = FIXTURES / "continuing_airworthiness_part_m_camo.xml"

DOC_TITLE = "Easy Access Rules for Information Security"
SLUG = "information-security"

IMPLEMENTING = "(EU) 2023/203"
DELEGATED = "(EU) 2022/1645"

#: The annexes the two regulations state, in document order, with the annex
#: label each is headed by.  The delegated regulation states one annex and
#: does not number it.
STATED_ANNEXES = (
    ("IS.AR", "I", IMPLEMENTING),
    ("IS.I.OR", "II", IMPLEMENTING),
    ("IS.D.OR", "", DELEGATED),
)

#: Parts named inside the amendment annex headings.  The Information Security
#: regulations amend these annexes of other regulations; they state none of
#: them, so none may be parsed as a part of this document.
AMENDED_ELSEWHERE = ("ARA", "ORA", "21")


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


def _parse(fixture: Path = NESTED) -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(fixture, DOC_TITLE)


def _codes(doc: ParsedDocument) -> list[str]:
    return [part.code for part in doc.parts]


def _located(doc: ParsedDocument, ref: str) -> tuple[str, str]:
    """The ``(part_code, entry_type)`` the entry *ref* is filed under."""
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref.startswith(ref):
                        return part.code, entry.entry_type
    raise AssertionError(
        f"no entry with ref {ref!r} was parsed; parsed refs were "
        f"{sorted(e for p in doc.parts for sp in p.subparts for s in sp.sections for e in [x.entry_ref for x in s.entries])}"
    )


def _ingest(db: Database, fixture: Path = NESTED) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision="December 2025",
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))
    CanonicalPersister(db).persist_document(doc_id, _parse(fixture))
    return doc_id


def _part_attribution(db: Database) -> dict[str, str | None]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT part_code, regulation FROM regulation_parts ORDER BY sort_order"
            )
            return {row["part_code"]: row["regulation"] for row in cur.fetchall()}


# ── The annexes the document actually states ────────────────────────────


def test_the_nested_annex_layout_is_read_as_a_part_document():
    doc = _parse()

    assert doc.parser_mode == "part", (
        f"the published Part-IS annex layout parsed as {doc.parser_mode!r}; "
        f"'article-structured' means no annex was recognised and every rule "
        f"fell through to article parsing"
    )


def test_the_document_does_not_collapse_into_one_articles_part():
    """The canary symptom: one part, named for nothing, stating everything."""
    doc = _parse()

    assert "ARTICLES" not in _codes(doc), (
        f"the rulebook parsed to {_codes(doc)} — an 'ARTICLES' part holds "
        f"rules from both regulations under a code neither of them states"
    )


def test_every_annex_the_regulations_state_is_parsed_with_its_label():
    doc = _parse()

    parsed = [(part.code, part.annex) for part in doc.parts]

    assert parsed == [(code, annex) for code, annex, _reg in STATED_ANNEXES], (
        f"the Part-IS annexes parsed as {parsed}"
    )


def test_an_unnumbered_annex_is_still_parsed():
    """The delegated regulation states one annex and gives it no numeral."""
    doc = _parse()

    delegated_annex = next(p for p in doc.parts if p.code == "IS.D.OR")

    assert delegated_annex.annex == "", (
        f"the unnumbered annex was labelled {delegated_annex.annex!r}"
    )


def test_an_amendment_annex_does_not_invent_a_part():
    """'ANNEX III — ... ANNEXES VI (Part-ARA) and VII (Part-ORA) to ...'.

    These annexes amend other regulations.  Reading a part marker from
    anywhere in the heading would file Part-ARA and Part-ORA under the
    Information Security regulations, which state neither.
    """
    doc = _parse()

    invented = [code for code in _codes(doc) if code in AMENDED_ELSEWHERE]
    assert invented == [], (
        f"the amendment annexes were parsed as parts {invented}; the "
        f"Information Security regulations amend them but state none of them"
    )
    # Not vacuous: the same parse does read the annexes the document states,
    # so 'no invented part' is not just 'no part at all'.
    assert _codes(doc) == [code for code, _annex, _reg in STATED_ANNEXES]


def test_rules_are_filed_under_the_annex_that_states_them():
    doc = _parse()

    for ref, part_code, entry_type in (
        ("IS.AR.200", "IS.AR", "IR"),
        ("GM1 IS.AR.200", "IS.AR", "GM"),
        ("IS.I.OR.200", "IS.I.OR", "IR"),
        ("AMC1 IS.I.OR.200", "IS.I.OR", "AMC"),
        ("IS.I.OR.205", "IS.I.OR", "IR"),
        ("IS.D.OR.200", "IS.D.OR", "IR"),
        ("AMC1 IS.D.OR.200", "IS.D.OR", "AMC"),
    ):
        assert _located(doc, ref) == (part_code, entry_type), (
            f"{ref} is filed as {_located(doc, ref)}, expected "
            f"({part_code!r}, {entry_type!r})"
        )


# ── Attribution to the regulation that states the rule ──────────────────


def test_each_annex_is_attributed_to_the_regulation_that_states_it(db):
    _ingest(db)

    attribution = _part_attribution(db)

    assert attribution == {
        code: regulation for code, _annex, regulation in STATED_ANNEXES
    }, f"the annexes were attributed as {attribution}"


def test_neither_regulation_is_left_stating_nothing(db):
    """The canary measured 0 entries against each of the two regulations."""
    _ingest(db)

    contributed = {
        identifier: sum(
            row["entry_count"] for row in parts_by_regulation(db, identifier)
        )
        for identifier in (IMPLEMENTING, DELEGATED)
    }

    assert contributed[IMPLEMENTING] > 0, (
        f"{IMPLEMENTING} is credited with no entry at all: {contributed}"
    )
    assert contributed[DELEGATED] > 0, (
        f"{DELEGATED} is credited with no entry at all: {contributed}"
    )


def test_the_two_regulations_do_not_share_their_parts(db):
    _ingest(db)

    implementing = [row["part_code"] for row in parts_by_regulation(db, IMPLEMENTING)]
    delegated = [row["part_code"] for row in parts_by_regulation(db, DELEGATED)]

    assert implementing == ["IS.AR", "IS.I.OR"], (
        f"{IMPLEMENTING} answers with parts {implementing}"
    )
    assert delegated == ["IS.D.OR"], f"{DELEGATED} answers with parts {delegated}"


def test_a_fully_attributed_nested_build_qualifies(db):
    _ingest(db)

    manifest = build_manifest(db)

    assert manifest.status == "qualified", (
        f"a Part-IS build whose every annex is attributed did not qualify: "
        f"{manifest.status!r} {manifest.notes}"
    )
    assert get_document_by_slug(db, SLUG)["status"] == "parsed"


def test_parse_source_ingests_the_nested_layout_end_to_end(tmp_env):
    """The real ingestion path: import the file, parse it, query SQLite."""
    result = parse_source(SLUG, file=NESTED)

    assert result["parts"] == len(STATED_ANNEXES), (
        f"parse_source found {result['parts']} parts, expected "
        f"{len(STATED_ANNEXES)}"
    )
    assert result["unattributed_parts"] == [], (
        f"annexes left without provenance: {result['unattributed_parts']}"
    )

    db = _open_db()
    try:
        assert get_document_by_slug(db, SLUG)["status"] == "parsed"
        assert _part_attribution(db) == {
            code: regulation for code, _annex, regulation in STATED_ANNEXES
        }, f"the service path attributed the annexes as {_part_attribution(db)}"

        rows = lookup_reference(db, "IS.D.OR.200")
        assert rows and rows[0]["part_code"] == "IS.D.OR", (
            "IS.D.OR.200 does not resolve to Part-IS.D.OR through the service "
            "path — the delegated annex did not survive ingestion"
        )
    finally:
        db.close()


# ── An annex no regulation claims ───────────────────────────────────────


def test_an_unclaimed_annex_is_parsed_rather_than_dropped():
    """Generalising the heading rule must not make an unknown annex vanish."""
    doc = _parse(NESTED_UNMAPPED)

    assert _codes(doc) == ["IS.I.OR", "IS.Z.OR"], (
        f"the unclaimed annex is not in the parse: {_codes(doc)} — an annex "
        f"that goes unparsed cannot be reported as unattributed either"
    )


def test_an_unclaimed_annex_is_left_unattributed(db):
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))

    summary = CanonicalPersister(db).persist_document(
        doc_id, _parse(NESTED_UNMAPPED),
    )

    assert summary.unattributed_parts == ("IS.Z.OR",), (
        f"the persister reports {summary.unattributed_parts} unattributed"
    )
    assert _part_attribution(db) == {"IS.I.OR": IMPLEMENTING, "IS.Z.OR": None}, (
        f"an unclaimed annex was filed under a regulation that does not state "
        f"it: {_part_attribution(db)}"
    )


def test_an_unclaimed_annex_is_reported_as_a_provenance_anomaly(db):
    """Diagnosed from what the parse actually produced, not from a literal."""
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))
    summary = CanonicalPersister(db).persist_document(
        doc_id, _parse(NESTED_UNMAPPED),
    )

    anomalies = detect_anomalies({
        "slug": SLUG,
        "unattributed_parts": list(summary.unattributed_parts),
    })

    provenance = [a for a in anomalies if a.category == "provenance"]
    assert provenance and any(a.severity == "error" for a in provenance), (
        f"an unclaimed annex raises no error: "
        f"{[(a.category, a.severity) for a in anomalies]}"
    )
    assert any("IS.Z.OR" in a.message for a in provenance)


# ── Incomplete while the provenance is incomplete ───────────────────────


def test_a_build_holding_an_unclaimed_annex_is_not_qualified(db):
    _ingest(db, NESTED_UNMAPPED)

    manifest = build_manifest(db)

    assert manifest.status == "incomplete", (
        f"a build with an annex no regulation claims qualified as "
        f"{manifest.status!r}"
    )
    assert any("IS.Z.OR" in note for note in manifest.notes), (
        f"nothing names the unclaimed annex: {manifest.notes}"
    )


def test_the_source_status_stays_incomplete_while_an_annex_is_unclaimed(db):
    _ingest(db, NESTED_UNMAPPED)

    assert get_document_by_slug(db, SLUG)["status"] == "incomplete", (
        "a parse whose provenance does not add up is recorded as 'parsed'"
    )


def test_a_regulation_that_contributed_nothing_holds_the_build_back(db):
    """The delegated annex dropped by a later revision, or renamed."""
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision="December 2025",
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))
    parsed = _parse()
    parsed.parts = [p for p in parsed.parts if p.code != "IS.D.OR"]
    CanonicalPersister(db).persist_document(doc_id, parsed)

    manifest = build_manifest(db)

    assert manifest.status == "incomplete", (
        f"a build holding only {IMPLEMENTING} qualified as {manifest.status!r}"
    )
    assert any(DELEGATED in note for note in manifest.notes), (
        f"nothing says {DELEGATED} contributed nothing: {manifest.notes}"
    )


# ── The layouts that already worked, unchanged ──────────────────────────


@pytest.mark.parametrize(
    ("fixture", "expected"),
    (
        (AIR_OPS, [("ORO", "III"), ("CAT", "IV"), ("SPA", "V")]),
        (CONTINUING_AIRWORTHINESS, [("M", "I"), ("145", "II"), ("CAMO", "Vc")]),
    ),
    ids=("air-operations", "continuing-airworthiness"),
)
def test_the_parenthesised_heading1_annex_layout_still_parses(fixture, expected):
    """Reading the annex level off the document must not lose the common one.

    Air Ops heads its annexes at ``Heading1`` with the marker parenthesised
    directly after the numeral, and the continuing-airworthiness rulebook adds
    lettered ('Vc') and numeric ('145') annexes to that shape.
    """
    doc = EASAOfficeXMLParser().parse_file(fixture, fixture.stem)

    assert [(p.code, p.annex) for p in doc.parts] == expected, (
        f"{fixture.name} parsed as {[(p.code, p.annex) for p in doc.parts]}"
    )
