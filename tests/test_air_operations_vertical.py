"""Behaviour 7 — the Air Operations rulebook, end to end.

Regulation (EU) No 965/2012 is the vertical IOSA Workbench needs first, and
its March 2026 revision states rules in two positions the ANNEX -> SUBPART ->
SECTION -> entry hierarchy does not describe:

*Above the first subpart*: ``ORO.GEN.005 Scope`` is stated directly under
``ANNEX III (Part-ORO)``, before ``SUBPART GEN``.  ``_parse_subparts`` starts
reading at the first SUBPART heading, so the scope of the whole annex is
never parsed — and its ``Heading2IR`` style is not an entry style either.

*Above the first section*: ``CAT.GEN.100 Competent authority`` is stated
under ``SUBPART A``, before ``SECTION 1``.  ``_parse_sections`` starts
reading at the first SECTION heading, so the rule naming the authority
responsible for commercial air transport is dropped.

Both shapes are taken from the EASA download, so both losses are silent: the
parse reports success, and a lookup of a rule that exists in the rulebook
comes back empty.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.diagnostics import coverage_report, format_report
from claw_easa.ingest.manifest import (
    QUALIFIED,
    STALE,
    build_manifest,
    last_qualified_build,
    record_build,
)
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument, ParsedEntry
from claw_easa.ingest.repository import (
    get_document_by_slug,
    upsert_source_document_from_values,
)
from claw_easa.ingest.service import _open_db, parse_source
from claw_easa.retrieval.exact import lookup_reference

FIXTURE = (
    Path(__file__).parent / "fixtures" / "air_operations_oro_cat_spa_march_2026.xml"
)
UNRECOGNISED_FIXTURE = Path(__file__).parent / "fixtures" / "unknown_structure.xml"
DOC_TITLE = "Easy Access Rules for Air Operations"
SLUG = "air-ops"
REVISION = "March 2026"
SUPERSEDED_REVISION = "December 2025"

#: One rule per annex in scope, with the part that must own it.
SENTINELS = (
    ("ORO.GEN.005", "ORO"),
    ("ORO.GEN.110", "ORO"),
    ("ORO.FTL.110", "ORO"),
    ("CAT.GEN.100", "CAT"),
    ("CAT.GEN.MPA.100", "CAT"),
    ("SPA.GEN.100", "SPA"),
    ("SPA.PBN.100", "SPA"),
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


def _ingest(db: Database, *, revision: str | None = REVISION,
            parsed: ParsedDocument | None = None) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug=SLUG, source_family="ear", title=DOC_TITLE, revision=revision,
    )
    CanonicalPersister(db).persist_document(
        doc_id, _parse_fixture() if parsed is None else parsed,
    )
    return doc_id


# ── Coverage of the three annexes in scope ──────────────────────────────


def test_every_annex_sentinel_reference_is_parsed():
    doc = _parse_fixture()

    refs = {e.entry_ref for e in _entries(doc)}
    expected = {
        "ORO.GEN.005", "ORO.GEN.110", "ORO.FTL.110",
        "CAT.GEN.100", "CAT.GEN.MPA.100",
        "SPA.GEN.100", "SPA.PBN.100",
    }

    assert expected <= refs, (
        f"sentinel rules missing from the parse: {sorted(expected - refs)}; "
        f"parsed refs were {sorted(refs)}"
    )


def test_the_document_layout_is_recognised():
    doc = _parse_fixture()

    assert doc.parser_mode == "part", (
        f"the Air Ops annex layout parsed as {doc.parser_mode!r}"
    )


def test_the_three_annexes_in_scope_are_distinct_parts():
    doc = _parse_fixture()

    part_codes = [part.code for part in doc.parts]

    assert part_codes == ["ORO", "CAT", "SPA"], (
        f"Part-ORO, Part-CAT and Part-SPA are not three distinct parts; "
        f"parsed parts were {part_codes}"
    )


def test_each_part_keeps_its_own_annex_label():
    doc = _parse_fixture()

    labels = {part.code: part.annex for part in doc.parts}

    assert labels == {"ORO": "III", "CAT": "IV", "SPA": "V"}, (
        f"annex labels are {labels}, expected ORO/III, CAT/IV and SPA/V"
    )


def test_every_sentinel_is_filed_under_its_own_part():
    """A rule attributed to the wrong annex cites the wrong regulation."""
    doc = _parse_fixture()

    for ref, expected_part in SENTINELS:
        assert _part_of(doc, ref) == expected_part, (
            f"{ref} is filed under Part-{_part_of(doc, ref)}, expected "
            f"Part-{expected_part}"
        )


def test_a_subpart_lead_in_rule_is_not_swallowed_by_the_next_subpart():
    """Guard: ORO.GEN.005 scopes the annex, it is not part of SUBPART GEN."""
    doc = _parse_fixture()

    oro = next(part for part in doc.parts if part.code == "ORO")
    holder = next(
        subpart for subpart in oro.subparts
        for section in subpart.sections
        for entry in section.entries
        if entry.entry_ref == "ORO.GEN.005"
    )

    assert holder.code == "GENERAL", (
        f"ORO.GEN.005 is stated above SUBPART GEN but was filed under "
        f"subpart {holder.code!r}"
    )


# ── IR / AMC / GM typing ────────────────────────────────────────────────


def test_implementing_rules_are_typed_ir():
    doc = _parse_fixture()

    for ref, _part in SENTINELS:
        assert _entry(doc, ref).entry_type == "IR", (
            f"{ref} is an implementing rule but was typed "
            f"{_entry(doc, ref).entry_type!r}"
        )


def test_numbered_amc_is_typed_as_amc():
    doc = _parse_fixture()

    amc = [e for e in _entries(doc) if e.entry_ref.startswith("AMC1")]

    assert amc, "no AMC entry was parsed at all"
    assert all(e.entry_type == "AMC" for e in amc), (
        f"acceptable means of compliance presented as binding law: "
        f"{[(e.entry_ref, e.entry_type) for e in amc if e.entry_type != 'AMC']}"
    )


def test_numbered_gm_is_typed_as_gm():
    doc = _parse_fixture()

    gm = [e for e in _entries(doc) if e.entry_ref.startswith("GM1")]

    assert gm, "no GM entry was parsed at all"
    assert all(e.entry_type == "GM" for e in gm), (
        f"guidance material mistyped: "
        f"{[(e.entry_ref, e.entry_type) for e in gm if e.entry_type != 'GM']}"
    )


def test_subpart_headings_do_not_become_entries():
    """Guard: reading level-2 headings must not file SUBPART titles as rules."""
    doc = _parse_fixture()

    junk = [
        e.entry_ref for e in _entries(doc)
        if e.entry_ref.upper().startswith(("SUBPART", "SECTION"))
        or e.entry_type == "INFO"
    ]

    assert junk == [], f"structural headings were stored as entries: {junk}"


# ── source → parse → SQLite → lookup ────────────────────────────────────


def test_lookup_reports_the_right_part_for_every_sentinel(db):
    _ingest(db)

    for ref, expected_part in SENTINELS:
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} did not survive persistence"
        assert rows[0]["part_code"] == expected_part, (
            f"lookup reports part_code {rows[0]['part_code']!r} for {ref}, "
            f"expected {expected_part!r}"
        )


def test_lookup_preserves_entry_type_through_sqlite(db):
    _ingest(db)

    for ref, expected_type in (
        ("ORO.GEN.005", "IR"),
        ("CAT.GEN.100", "IR"),
        ("AMC1 ORO.GEN.110(a)", "AMC"),
        ("AMC1 CAT.GEN.MPA.100(b)", "AMC"),
        ("GM1 SPA.GEN.100(a)", "GM"),
    ):
        rows = lookup_reference(db, ref)
        assert rows, f"{ref} does not resolve by exact lookup"
        assert rows[0]["entry_type"] == expected_type, (
            f"{ref} is stored as {rows[0]['entry_type']!r}, expected "
            f"{expected_type!r}"
        )


def test_lookup_resolves_a_reference_written_with_a_non_breaking_space(db):
    """Air Ops separates 'ORO.FTL.110' from its title with U+00A0."""
    _ingest(db)

    rows = lookup_reference(db, "ORO.FTL.110")

    assert rows and rows[0]["part_code"] == "ORO", (
        "a reference stored behind a non-breaking space does not resolve"
    )


def test_parse_source_ingests_the_fixture_and_lookup_resolves_it(tmp_env):
    """The real path: import a local file, parse it, query SQLite."""
    result = parse_source(SLUG, file=FIXTURE)

    assert result["parts"] == 3, (
        f"parse_source found {result['parts']} parts, expected Part-ORO, "
        f"Part-CAT and Part-SPA"
    )

    db = _open_db()
    try:
        assert get_document_by_slug(db, SLUG)["status"] == "parsed"

        for ref, expected_part in SENTINELS:
            rows = lookup_reference(db, ref)
            assert rows and rows[0]["part_code"] == expected_part, (
                f"{ref} does not resolve to Part-{expected_part} through the "
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
        "the Air Ops corpus does not pass its own coverage report:\n"
        + format_report(report)
    )


# ── provenance, revision and qualification ──────────────────────────────


def test_the_current_revision_qualifies_with_its_provenance(db):
    _ingest(db, revision=REVISION)

    manifest = build_manifest(db, catalog_revisions={SLUG: REVISION})

    assert manifest.status == QUALIFIED, f"notes: {manifest.notes}"
    source = next(s for s in manifest.sources if s.slug == SLUG)
    assert source.revision == REVISION
    assert source.entry_count == len(_entries(_parse_fixture()))


def test_the_superseded_revision_does_not_qualify(db):
    _ingest(db, revision=SUPERSEDED_REVISION)

    manifest = build_manifest(db, catalog_revisions={SLUG: REVISION})

    assert manifest.status == STALE, (
        f"a {SUPERSEDED_REVISION} corpus is graded {manifest.status!r} "
        f"against the published {REVISION} revision"
    )
    assert any(
        SUPERSEDED_REVISION in note and REVISION in note
        for note in manifest.notes
    ), f"the note does not name both revisions: {manifest.notes}"


def test_the_superseded_revision_is_flagged_as_an_anomaly(db):
    _ingest(db, revision=SUPERSEDED_REVISION)

    doc = get_document_by_slug(db, SLUG)
    anomalies = detect_anomalies({
        "slug": SLUG,
        "local_revision": doc["revision"],
        "catalog_revision": REVISION,
    })

    assert [a for a in anomalies if a.category == "freshness"], (
        f"no freshness anomaly for a {SUPERSEDED_REVISION} corpus; got "
        f"{[(a.category, a.message) for a in anomalies]}"
    )


def test_an_unrecognised_rebuild_never_replaces_the_qualified_build(db):
    """A failed re-ingest must degrade the report, not the last good build."""
    _ingest(db, revision=REVISION)
    qualified = record_build(db, build_manifest(db, catalog_revisions={SLUG: REVISION}))
    assert qualified.status == QUALIFIED

    unrecognised = EASAOfficeXMLParser().parse_file(UNRECOGNISED_FIXTURE, DOC_TITLE)
    _ingest(db, revision=REVISION, parsed=unrecognised)

    assert get_document_by_slug(db, SLUG)["status"] == "incomplete", (
        "a parse that extracted nothing is recorded as a successful parse"
    )

    rebuilt = record_build(db, build_manifest(db, catalog_revisions={SLUG: REVISION}))
    assert rebuilt.status != QUALIFIED, (
        f"a corpus that parsed nothing is graded {rebuilt.status!r}"
    )

    preserved = last_qualified_build(db)
    assert preserved.build_id == qualified.build_id
    assert preserved.entry_count == qualified.entry_count
