"""Behaviour 9 — the Information Security rulebook (Part-IS), end to end.

One Easy Access Rules document publishes two regulations: Implementing
Regulation (EU) 2023/203 — ANNEX I (Part-IS.I.OR) and ANNEX II (Part-IS.AR) —
and Delegated Regulation (EU) 2022/1645 — ANNEX I (Part-IS.D.OR).  They cover
different organisations and are amended on their own timelines, so an answer
sourced from Part-IS has to say which of the two states the rule it cites.

Three gaps are pinned here.

*Fetchable*: the ``information-security`` alias carries no page URL of its
own.  Every other alias that EASA does not list on the catalogue index has a
``fallback_page_url``; without one, a catalogue miss leaves Part-IS
unreachable by slug and the operator has to rediscover the official page by
hand.

*Separately traceable*: nothing in the pipeline knows a source can be built
from more than one regulation.  ``source_documents`` holds a single title,
``regulation_parts`` holds no attribution at all, and the manifest reports one
undifferentiated ``Easy Access Rules for Information Security`` line — so
``IS.D.OR.200`` and ``IS.I.OR.200`` become indistinguishable in provenance
even though a different regulation states each.  Both annexes are numbered
'ANNEX I', so the annex label cannot recover the distinction afterwards.

*Never silently green*: a part no declared regulation claims — a new annex in
a later revision — parses cleanly.  Attributing it by proximity would file
requirements under a regulation that does not state them, so it must stay
unattributed, say so in the diagnostics, and hold the build out of
``qualified``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.catalog import EasyAccessRulesCatalogScraper
from claw_easa.ingest.manifest import build_manifest, export_manifest
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument, ParsedEntry
from claw_easa.ingest.regulations import regulations_for
from claw_easa.ingest.repository import (
    get_document_by_slug,
    list_source_regulations,
    parts_by_regulation,
    record_source_regulations,
    upsert_source_document_from_values,
)
from claw_easa.ingest.service import _open_db, parse_source
from claw_easa.ingest.sources import get_alias
from claw_easa.retrieval.exact import lookup_reference

FIXTURE = Path(__file__).parent / "fixtures" / "information_security_part_is.xml"
UNMAPPED_FIXTURE = (
    Path(__file__).parent / "fixtures" / "information_security_unmapped_annex.xml"
)
UNKNOWN_FIXTURE = Path(__file__).parent / "fixtures" / "unknown_structure.xml"
DOC_TITLE = "Easy Access Rules for Information Security"
SLUG = "information-security"
REVISION = "December 2025"

#: The EASA document library page for the Information Security Easy Access
#: Rules, as recorded in the remediation brief of 18 August 2026.
PAGE_URL = (
    "https://www.easa.europa.eu/en/document-library/easy-access-rules/"
    "easy-access-rules-information-security-regulations-eu-2023203-and-20221645"
)

IMPLEMENTING = "(EU) 2023/203"
DELEGATED = "(EU) 2022/1645"

#: Which regulation states which annex of the consolidated document.
PART_PROVENANCE = (
    ("IS.I.OR", "I", IMPLEMENTING),
    ("IS.AR", "II", IMPLEMENTING),
    ("IS.D.OR", "I", DELEGATED),
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


def _parse(fixture: Path = FIXTURE) -> ParsedDocument:
    return EASAOfficeXMLParser().parse_file(fixture, DOC_TITLE)


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
        if entry.entry_ref.startswith(ref):
            return entry
    raise AssertionError(
        f"no entry with ref {ref!r} was parsed; parsed refs were "
        f"{sorted(e.entry_ref for e in _entries(doc))}"
    )


def _located(doc: ParsedDocument, ref: str) -> tuple[str, str]:
    """The ``(part_code, subpart_code)`` the entry *ref* is filed under."""
    for part in doc.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                for entry in section.entries:
                    if entry.entry_ref.startswith(ref):
                        return part.code, subpart.code
    raise AssertionError(f"no entry with ref {ref!r} was parsed")


def _ingest(db: Database, fixture: Path = FIXTURE) -> int:
    """Register the source with its regulations and persist a parse of it."""
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision=REVISION,
        page_url=PAGE_URL,
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


# ── The official source, reachable by slug ──────────────────────────────


def test_the_information_security_alias_carries_the_official_page():
    alias = get_alias(SLUG)

    assert alias is not None and alias.source_family == "ear"
    assert alias.fallback_page_url == PAGE_URL, (
        f"the Part-IS alias resolves to {alias.fallback_page_url!r}; without "
        f"the official page URL a catalogue miss leaves Part-IS unfetchable"
    )


def test_the_alias_resolves_to_the_official_page_without_the_catalogue(monkeypatch):
    """EASA's catalogue index is paginated and has missed EARs before."""
    monkeypatch.setattr(
        EasyAccessRulesCatalogScraper, "discover", lambda self, **kwargs: [],
    )

    entry = EasyAccessRulesCatalogScraper().resolve(SLUG)

    assert entry.slug == SLUG
    assert entry.page_url == PAGE_URL, (
        f"a catalogue miss resolved Part-IS to {entry.page_url!r}"
    )


def test_both_contributing_regulations_are_declared_for_the_source():
    regulations = regulations_for(SLUG)

    identifiers = [r.identifier for r in regulations]
    assert identifiers == [IMPLEMENTING, DELEGATED], (
        f"the Information Security source declares {identifiers}, expected "
        f"both contributing regulations"
    )
    kinds = {r.identifier: r.kind for r in regulations}
    assert kinds == {IMPLEMENTING: "implementing", DELEGATED: "delegated"}, (
        f"the two regulations are not distinguished by kind: {kinds}"
    )
    for regulation in regulations:
        assert regulation.identifier in regulation.title, (
            f"regulation title {regulation.title!r} does not name "
            f"{regulation.identifier!r}"
        )


def test_a_source_that_declares_no_regulations_is_left_alone():
    """Guard: the declaration is opt-in, not a requirement on every corpus."""
    assert regulations_for("air-ops") == ()


# ── Part hierarchy and IR / AMC / GM typing ─────────────────────────────


def test_the_document_layout_is_recognised():
    doc = _parse()

    assert doc.parser_mode == "part", (
        f"the Part-IS annex layout parsed as {doc.parser_mode!r}"
    )


def test_every_part_is_annex_is_parsed_with_its_annex_label():
    doc = _parse()

    parsed = [(part.code, part.annex) for part in doc.parts]

    assert parsed == [(code, annex) for code, annex, _reg in PART_PROVENANCE], (
        f"the Part-IS annexes parsed as {parsed}"
    )


def test_is_i_or_requirements_are_filed_under_their_own_part_and_subpart():
    doc = _parse()

    for ref in ("IS.I.OR.200", "IS.I.OR.205"):
        assert _located(doc, ref) == ("IS.I.OR", "A"), (
            f"{ref} is filed under {_located(doc, ref)}, expected Part-IS.I.OR "
            f"SUBPART A"
        )


def test_the_delegated_requirements_do_not_land_in_the_implementing_annex():
    doc = _parse()

    assert _located(doc, "IS.D.OR.200")[0] == "IS.D.OR", (
        "IS.D.OR.200 is stated by the delegated regulation but is filed under "
        f"part {_located(doc, 'IS.D.OR.200')[0]!r}"
    )
    assert _located(doc, "IS.AR.200")[0] == "IS.AR"


def test_entry_types_are_distinguished():
    doc = _parse()

    for ref, expected_type in (
        ("IS.I.OR.200", "IR"),
        ("IS.I.OR.205", "IR"),
        ("AMC1 IS.I.OR.205", "AMC"),
        ("GM1 IS.I.OR.200", "GM"),
        ("IS.D.OR.200", "IR"),
        ("AMC1 IS.D.OR.200", "AMC"),
    ):
        assert _entry(doc, ref).entry_type == expected_type, (
            f"{ref} was typed {_entry(doc, ref).entry_type!r}, expected "
            f"{expected_type!r}"
        )


def test_lookup_reports_the_part_hierarchy_and_type_through_sqlite(db):
    _ingest(db)

    for ref, part_code, entry_type in (
        ("IS.I.OR.200", "IS.I.OR", "IR"),
        ("IS.I.OR.205", "IS.I.OR", "IR"),
        ("AMC1 IS.I.OR.205", "IS.I.OR", "AMC"),
        ("GM1 IS.I.OR.200", "IS.I.OR", "GM"),
        ("IS.AR.200", "IS.AR", "IR"),
        ("IS.D.OR.200", "IS.D.OR", "IR"),
    ):
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} did not survive persistence"
        assert rows[0]["part_code"] == part_code, (
            f"lookup reports part {rows[0]['part_code']!r} for {ref}, expected "
            f"{part_code!r}"
        )
        assert rows[0]["subpart_code"] == "A"
        assert rows[0]["entry_type"] == entry_type, (
            f"{ref} is stored as {rows[0]['entry_type']!r}, expected {entry_type!r}"
        )


def test_parse_source_ingests_part_is_end_to_end(tmp_env):
    """The real path: import a local file, parse it, query SQLite."""
    result = parse_source(SLUG, file=FIXTURE)

    assert result["parts"] == len(PART_PROVENANCE), (
        f"parse_source found {result['parts']} parts, expected "
        f"{len(PART_PROVENANCE)}"
    )
    assert result["entries"] == len(_entries(_parse())), (
        f"parse_source persisted {result['entries']} entries of "
        f"{len(_entries(_parse()))} parsed"
    )
    assert result["unattributed_parts"] == [], (
        f"parts left without provenance: {result['unattributed_parts']}"
    )

    db = _open_db()
    try:
        assert get_document_by_slug(db, SLUG)["status"] == "parsed"

        rows = lookup_reference(db, "IS.I.OR.200")
        assert rows and rows[0]["part_code"] == "IS.I.OR", (
            "IS.I.OR.200 does not resolve to Part-IS.I.OR through the service path"
        )
        assert _part_attribution(db) == {
            code: regulation for code, _annex, regulation in PART_PROVENANCE
        }, (
            "the service path did not attribute the parts to their regulations: "
            f"{_part_attribution(db)}"
        )
    finally:
        db.close()


# ── Two regulations, kept apart ─────────────────────────────────────────


def test_both_regulations_are_recorded_against_the_source(db):
    _ingest(db)

    recorded = list_source_regulations(db, SLUG)

    assert [r["identifier"] for r in recorded] == [IMPLEMENTING, DELEGATED], (
        f"the source records {[r['identifier'] for r in recorded]}, expected "
        f"both contributing regulations"
    )
    assert len({r["title"] for r in recorded}) == 2, (
        "the two regulations collapsed into one title: "
        f"{[r['title'] for r in recorded]}"
    )
    assert {r["kind"] for r in recorded} == {"implementing", "delegated"}


def test_each_part_is_attributed_to_the_regulation_that_states_it(db):
    _ingest(db)

    attribution = _part_attribution(db)

    assert attribution == {
        code: regulation for code, _annex, regulation in PART_PROVENANCE
    }, f"parts were attributed as {attribution}"


def test_parts_are_queryable_by_regulation(db):
    _ingest(db)

    implementing = [row["part_code"] for row in parts_by_regulation(db, IMPLEMENTING)]
    delegated = [row["part_code"] for row in parts_by_regulation(db, DELEGATED)]

    assert implementing == ["IS.I.OR", "IS.AR"], (
        f"{IMPLEMENTING} answers with parts {implementing}"
    )
    assert delegated == ["IS.D.OR"], (
        f"{DELEGATED} answers with parts {delegated}"
    )
    assert all(row["slug"] == SLUG for row in parts_by_regulation(db, DELEGATED))
    assert parts_by_regulation(db, DELEGATED)[0]["entry_count"] == 2, (
        "the delegated regulation reports the wrong entry count: "
        f"{parts_by_regulation(db, DELEGATED)}"
    )


def test_the_manifest_keeps_the_two_regulations_apart(db):
    _ingest(db)

    manifest = build_manifest(db, catalog_revisions={SLUG: REVISION})
    source = next(s for s in manifest.sources if s.slug == SLUG)

    assert [r.identifier for r in source.regulations] == [IMPLEMENTING, DELEGATED], (
        f"the manifest reports regulations {[r.identifier for r in source.regulations]}"
    )
    counts = {r.identifier: r.entry_count for r in source.regulations}
    assert counts == {IMPLEMENTING: 5, DELEGATED: 2}, (
        f"the manifest cannot say what each regulation contributed: {counts}"
    )
    assert manifest.status == "qualified", (
        f"a complete Part-IS corpus did not qualify: {manifest.notes}"
    )


def test_the_exported_manifest_records_each_regulation_separately(db, tmp_path):
    _ingest(db)

    path = export_manifest(db, tmp_path / "m.json", current=build_manifest(db))
    payload = json.loads(path.read_text())
    source = next(
        s for s in payload["current"]["sources"] if s["slug"] == SLUG
    )

    assert [r["identifier"] for r in source["regulations"]] == [
        IMPLEMENTING, DELEGATED,
    ], f"the exported manifest carries {source.get('regulations')}"
    assert all(
        set(r) >= {"identifier", "title", "kind", "part_codes", "entry_count"}
        for r in source["regulations"]
    ), f"exported regulation provenance is incomplete: {source['regulations']}"


def test_a_build_missing_one_contributing_regulation_is_not_qualified(db):
    """The failure this guards: one annex silently dropped from the parse."""
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision=REVISION,
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))
    parsed = _parse()
    parsed.parts = [p for p in parsed.parts if p.code != "IS.D.OR"]
    CanonicalPersister(db).persist_document(doc_id, parsed)

    manifest = build_manifest(db)

    assert manifest.status == "incomplete", (
        f"a Part-IS build holding only {IMPLEMENTING} qualified as "
        f"{manifest.status!r}"
    )
    assert any(DELEGATED in note for note in manifest.notes), (
        f"nothing says {DELEGATED} contributed nothing: {manifest.notes}"
    )


# ── An unclaimed annex is never silently green ──────────────────────────


def test_an_annex_no_regulation_declares_is_left_unattributed(db):
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))

    summary = CanonicalPersister(db).persist_document(
        doc_id, _parse(UNMAPPED_FIXTURE),
    )

    assert summary.unattributed_parts == ("IS.Z.OR",), (
        f"the persister reports {summary.unattributed_parts} unattributed"
    )
    assert _part_attribution(db) == {"IS.I.OR": IMPLEMENTING, "IS.Z.OR": None}, (
        "an undeclared annex was attributed to a regulation that does not "
        f"state it: {_part_attribution(db)}"
    )


def test_an_unattributed_part_is_reported_as_a_provenance_anomaly():
    anomalies = detect_anomalies({
        "slug": SLUG,
        "unattributed_parts": ["IS.Z.OR"],
    })

    provenance = [a for a in anomalies if a.category == "provenance"]
    assert provenance, (
        f"a part no regulation claims raises no anomaly; got "
        f"{[(a.category, a.message) for a in anomalies]}"
    )
    assert any(a.severity == "error" for a in provenance)
    assert any("IS.Z.OR" in a.message for a in provenance)


def test_no_provenance_anomaly_when_every_part_is_attributed():
    assert detect_anomalies({"slug": SLUG, "unattributed_parts": []}) == []


def test_a_build_holding_an_unattributed_part_is_not_qualified(db):
    _ingest(db, UNMAPPED_FIXTURE)

    manifest = build_manifest(db)

    assert manifest.status == "incomplete", (
        f"a build with an unclaimed annex qualified as {manifest.status!r}"
    )
    assert any("IS.Z.OR" in note for note in manifest.notes), (
        f"nothing names the unclaimed annex: {manifest.notes}"
    )


def test_a_source_without_declared_regulations_still_qualifies(db):
    """Guard: attribution is required only where regulations are declared."""
    doc_id = upsert_source_document_from_values(
        db, slug="air-ops", source_family="ear",
        title="Easy Access Rules for Air Operations", revision="March 2026",
    )
    CanonicalPersister(db).persist_document(doc_id, _parse())

    manifest = build_manifest(db, catalog_revisions={"air-ops": "March 2026"})

    assert manifest.status == "qualified", (
        f"a source that declares no regulations was held back: {manifest.notes}"
    )
    assert next(s for s in manifest.sources if s.slug == "air-ops").regulations == ()


def test_an_unrecognised_layout_is_not_recorded_as_a_parsed_part_is(db):
    """A changed EASA XML layout must not pass as an ingested Part-IS."""
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE,
    )
    record_source_regulations(db, doc_id, regulations_for(SLUG))

    parsed = _parse(UNKNOWN_FIXTURE)
    summary = CanonicalPersister(db).persist_document(doc_id, parsed)

    assert parsed.parser_mode == "unrecognised"
    assert summary.entries == 0
    assert get_document_by_slug(db, SLUG)["status"] == "incomplete", (
        "an unread Part-IS document is recorded as successfully parsed"
    )
    assert build_manifest(db).status == "failed"
