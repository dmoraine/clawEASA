"""Detailed JSON lookup of a provision that is already in the corpus.

``claw-easa lookup <REF>`` prints a five-line extract for a human reader.  An
agent quoting a rule needs the whole provision *and* the provenance it must
cite with it — which corpus, which EASA revision, where in the document — and
it needs to be told when the corpus cannot answer unambiguously rather than
handed one of several candidates.

These tests pin that contract:

- ``lookup <REF> --json`` emits one JSON document holding the complete entry;
- the corpus can be named explicitly with ``--slug``;
- absence and ambiguity are reported as such, never resolved by guessing;
- provenance the corpus does not hold is ``null``, never invented;
- entry text is carried as JSON data, whatever it contains;
- the existing text output of ``lookup`` and ``refs`` is untouched.
"""
from __future__ import annotations

import json

import pytest

from claw_easa.cli import main as cli_main
from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import (
    ParsedDocument,
    ParsedEntry,
    ParsedPart,
    ParsedSection,
    ParsedSubpart,
)
from claw_easa.ingest.regulations import IMPLEMENTING, Regulation
from claw_easa.ingest.repository import (
    record_source_regulations,
    upsert_source_document_from_values,
)
from claw_easa.retrieval.entry_detail import entry_detail
from claw_easa.retrieval.exact import lookup_reference

AIR_OPS_TITLE = "Easy Access Rules for Air Operations"
AIR_OPS_PAGE = "https://www.easa.europa.eu/en/document-library/easy-access-rules/air-ops"
AIR_OPS_REVISION = "March 2026"

AIR_OPS_REGULATION = Regulation(
    identifier="(EU) No 965/2012",
    title="Commission Implementing Regulation (EU) No 965/2012",
    kind=IMPLEMENTING,
    part_codes=("ORO",),
)

IR_BODY = [
    "(a) The operator shall establish a fatigue risk management system.",
    "(b) The operator shall publish duty rosters in advance.",
    "(c) The operator shall keep records for 24 months.",
    "(d) The operator shall provide fatigue management training.",
    "(e) The operator shall review the system annually.",
    "(f) The operator shall report findings to the competent authority.",
]


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Runtime settings pinned to a throwaway data directory."""
    reset_settings()
    configured = Settings(data_dir=str(tmp_path), db_file="detail.db")
    monkeypatch.setattr("claw_easa.config._settings", configured)
    yield configured
    reset_settings()


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings=settings)
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()


def _ir(ref: str = "ORO.FTL.110") -> ParsedEntry:
    return ParsedEntry(
        entry_ref=ref,
        entry_type="IR",
        title="Fatigue management",
        body_lines=list(IR_BODY),
        sort_order=1,
        source_locator="paragraphs:120-131",
    )


def _amc(ref: str = "AMC1 ORO.FTL.110") -> ParsedEntry:
    return ParsedEntry(
        entry_ref=ref,
        entry_type="AMC",
        title="Fatigue management - acceptable means",
        body_lines=["The fatigue risk management system should be documented."],
        sort_order=2,
        source_locator="paragraphs:132-140",
    )


def _gm(ref: str = "GM1 ORO.FTL.110") -> ParsedEntry:
    return ParsedEntry(
        entry_ref=ref,
        entry_type="GM",
        title="Fatigue management - guidance",
        body_lines=["Fatigue is a physiological state of reduced capability."],
        sort_order=3,
        source_locator="paragraphs:141-148",
    )


def _persist(
    db: Database,
    *,
    slug: str = "air-ops",
    title: str = AIR_OPS_TITLE,
    page_url: str | None = AIR_OPS_PAGE,
    revision: str | None = AIR_OPS_REVISION,
    regulations: tuple[Regulation, ...] = (AIR_OPS_REGULATION,),
    part_code: str = "ORO",
    subpart_code: str = "FTL",
    entries: list[ParsedEntry] | None = None,
) -> int:
    doc_id = upsert_source_document_from_values(
        db,
        slug=slug,
        source_family="ear",
        title=title,
        page_url=page_url,
        revision=revision,
    )
    record_source_regulations(db, doc_id, regulations)
    section = ParsedSection(title="General", sort_order=1, entries=entries or [_ir()])
    subpart = ParsedSubpart(
        code=subpart_code,
        title="Flight and Duty Time Limitations",
        sort_order=1,
        sections=[section],
    )
    part = ParsedPart(
        code=part_code,
        title=f"ANNEX III (Part-{part_code})",
        annex="III",
        sort_order=1,
        subparts=[subpart],
    )
    CanonicalPersister(db).persist_document(doc_id, ParsedDocument(title=title, parts=[part]))
    return doc_id


def _full_corpus(db: Database) -> None:
    _persist(db, entries=[_ir(), _amc(), _gm()])


# ── The entry itself ────────────────────────────────────────────────────────


def test_ir_detail_reports_the_reference_type_and_title(db):
    _full_corpus(db)

    detail = entry_detail(db, "ORO.FTL.110")

    assert detail["status"] == "found"
    assert detail["entry"]["entry_ref"] == "ORO.FTL.110"
    assert detail["entry"]["entry_type"] == "IR"
    assert detail["entry"]["title"] == "Fatigue management"


def test_ir_detail_returns_the_whole_provision_not_a_truncated_extract(db):
    _full_corpus(db)

    text = entry_detail(db, "ORO.FTL.110")["entry"]["text"]

    missing = [line for line in IR_BODY if line not in text]
    assert not missing, (
        f"the detail lookup dropped {len(missing)} lines of the provision, so a "
        f"quote taken from it would be incomplete: {missing}"
    )


def test_ir_detail_carries_the_provenance_needed_to_cite_it(db):
    _full_corpus(db)

    entry = entry_detail(db, "ORO.FTL.110")["entry"]

    assert entry["slug"] == "air-ops"
    assert entry["revision"] == AIR_OPS_REVISION
    assert entry["page_url"] == AIR_OPS_PAGE
    assert entry["source_locator"] == "paragraphs:120-131"
    assert entry["part"] == "ORO"
    assert entry["subpart"] == "FTL"
    assert entry["regulation"] == "(EU) No 965/2012"


def test_amc_detail_is_resolved_by_its_own_reference(db):
    _full_corpus(db)

    detail = entry_detail(db, "AMC1 ORO.FTL.110")

    assert detail["status"] == "found"
    assert detail["entry"]["entry_type"] == "AMC"
    assert detail["entry"]["entry_ref"] == "AMC1 ORO.FTL.110"


def test_gm_detail_is_resolved_by_its_own_reference(db):
    _full_corpus(db)

    detail = entry_detail(db, "GM1 ORO.FTL.110")

    assert detail["status"] == "found"
    assert detail["entry"]["entry_type"] == "GM"
    assert detail["entry"]["entry_ref"] == "GM1 ORO.FTL.110"


def test_looking_up_the_implementing_rule_does_not_return_its_amc_or_gm(db):
    _full_corpus(db)

    detail = entry_detail(db, "ORO.FTL.110")

    assert detail["match_count"] == 1, (
        "the implementing rule, its AMC and its GM are distinct provisions; "
        f"lookup returned {detail['match_count']} of them as one answer"
    )


def test_detail_prefers_body_markdown_over_body_text(db):
    _full_corpus(db)
    db.execute(
        "UPDATE regulation_entries SET body_markdown = ? WHERE entry_ref = ?",
        ("**(a)** The operator shall establish a fatigue risk management system.",
         "ORO.FTL.110"),
    )

    entry = entry_detail(db, "ORO.FTL.110")["entry"]

    assert entry["text"] == entry["body_markdown"]
    assert entry["text_format"] == "markdown"


def test_detail_resolves_a_reference_stored_with_its_title_suffix(db):
    """Same resolution as ``lookup``, including corpora parsed before it."""
    _persist(
        db,
        slug="continuing-airworthiness",
        title="Easy Access Rules for Continuing Airworthiness",
        regulations=(),
        part_code="M",
        subpart_code="B",
        entries=[
            ParsedEntry(
                entry_ref="M.A.201 Responsibilities",
                entry_type="INFO",
                title="M.A.201 Responsibilities",
                body_lines=["The owner shall be responsible for continuing airworthiness."],
                sort_order=1,
            )
        ],
    )

    detail = entry_detail(db, "M.A.201")

    assert detail["status"] == "found", (
        "exact lookup resolves 'M.A.201' against the stored "
        "'M.A.201 Responsibilities' but the detail lookup does not"
    )
    assert [row["entry_ref"] for row in lookup_reference(db, "M.A.201")] == [
        detail["entry"]["entry_ref"]
    ]


# ── Naming the corpus ───────────────────────────────────────────────────────


def test_lookup_scoped_to_a_corpus_returns_that_corpus_entry(db):
    _full_corpus(db)
    _persist(
        db,
        slug="aircrew",
        title="Easy Access Rules for Aircrew",
        page_url=None,
        revision="December 2025",
        regulations=(),
        entries=[_ir()],
    )

    detail = entry_detail(db, "ORO.FTL.110", slug="aircrew")

    assert detail["status"] == "found"
    assert detail["entry"]["slug"] == "aircrew"
    assert detail["entry"]["revision"] == "December 2025"


def test_the_same_reference_in_two_corpora_is_reported_as_ambiguous(db):
    _full_corpus(db)
    _persist(db, slug="aircrew", title="Easy Access Rules for Aircrew",
             regulations=(), entries=[_ir()])

    detail = entry_detail(db, "ORO.FTL.110")

    assert detail["status"] == "ambiguous", (
        "the reference exists in two corpora; answering with one of them "
        "silently attributes the rule to a source that may not state it"
    )
    assert detail["entry"] is None
    assert [match["slug"] for match in detail["matches"]] == ["air-ops", "aircrew"]


def test_lookup_scoped_to_a_corpus_without_the_reference_is_not_found(db):
    _full_corpus(db)
    _persist(db, slug="aircrew", title="Easy Access Rules for Aircrew",
             regulations=(), entries=[_ir("ORA.GEN.200")])

    detail = entry_detail(db, "ORO.FTL.110", slug="aircrew")

    assert detail["status"] == "not_found"
    assert detail["entry"] is None


def test_the_query_echoes_the_reference_and_corpus_that_were_searched(db):
    _full_corpus(db)

    detail = entry_detail(db, "  ORO.FTL.110  ", slug="air-ops")

    assert detail["query"] == {"reference": "ORO.FTL.110", "slug": "air-ops"}


# ── Absence ─────────────────────────────────────────────────────────────────


def test_an_absent_reference_is_reported_as_not_found(db):
    _full_corpus(db)

    detail = entry_detail(db, "ORO.FTL.999")

    assert detail["status"] == "not_found"
    assert detail["entry"] is None
    assert detail["matches"] == []
    assert detail["match_count"] == 0


def test_a_prefix_of_a_reference_does_not_answer_for_the_full_rule(db):
    _full_corpus(db)

    assert entry_detail(db, "ORO.FTL.11")["status"] == "not_found", (
        "ORO.FTL.11 is not ORO.FTL.110; answering the prefix with the full "
        "rule cites a provision the user did not ask for"
    )


def test_a_wildcard_reference_is_matched_literally(db):
    _full_corpus(db)

    assert entry_detail(db, "ORO.FTL.11%")["status"] == "not_found"
    assert entry_detail(db, "%")["status"] == "not_found"


def test_a_corpus_name_is_matched_as_a_value_not_as_sql(db):
    _full_corpus(db)

    detail = entry_detail(db, "ORO.FTL.110", slug="air-ops' OR '1'='1")

    assert detail["status"] == "not_found"
    assert entry_detail(db, "ORO.FTL.110")["status"] == "found", (
        "the corpus did not survive a lookup scoped to an SQL-shaped slug"
    )


# ── Corpora stored before the current metadata existed ──────────────────────


LEGACY_ENTRIES_DDL = """
CREATE TABLE regulation_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES regulation_parts(id) ON DELETE CASCADE,
    subpart_id INTEGER NOT NULL REFERENCES regulation_subparts(id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES regulation_sections(id) ON DELETE CASCADE,
    entry_ref TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body_text TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

LEGACY_BODY = "(a) The operator shall establish a fatigue risk management system."


@pytest.fixture
def legacy_db(settings) -> Database:
    """A corpus whose entries predate body_markdown/source_locator/source_url.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table alone, so a
    database built by an earlier version keeps the shape it was created with.
    """
    database = Database(settings=settings)
    database.open()
    database.execute_script(LEGACY_ENTRIES_DDL)
    MigrationRunner(database).init_schema()

    doc_id = upsert_source_document_from_values(
        database, slug="air-ops", source_family="ear", title=AIR_OPS_TITLE,
    )
    with database.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO regulation_parts "
                "(document_id, part_code, annex, title, sort_order) "
                "VALUES (?, 'ORO', 'III', 'ANNEX III (Part-ORO)', 1)",
                (doc_id,),
            )
            part_id = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_subparts (part_id, subpart_code, title, sort_order) "
                "VALUES (?, 'FTL', 'Flight and Duty Time Limitations', 1)",
                (part_id,),
            )
            subpart_id = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_sections (subpart_id, title, sort_order) "
                "VALUES (?, 'General', 1)",
                (subpart_id,),
            )
            section_id = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_entries "
                "(document_id, part_id, subpart_id, section_id, entry_ref, entry_type, "
                " title, body_text, sort_order) "
                "VALUES (?, ?, ?, ?, 'ORO.FTL.110', 'IR', 'Fatigue management', ?, 1)",
                (doc_id, part_id, subpart_id, section_id, LEGACY_BODY),
            )
        conn.commit()

    yield database
    database.close()


def test_a_legacy_entry_without_the_current_columns_is_still_found(legacy_db):
    detail = entry_detail(legacy_db, "ORO.FTL.110")

    assert detail["status"] == "found", (
        "a corpus ingested before body_markdown/source_locator existed still "
        "holds the rule; the detail lookup must read it rather than fail"
    )


def test_a_legacy_entry_falls_back_to_body_text(legacy_db):
    entry = entry_detail(legacy_db, "ORO.FTL.110")["entry"]

    assert entry["body_markdown"] is None
    assert entry["body_text"] == LEGACY_BODY
    assert entry["text"] == LEGACY_BODY
    assert entry["text_format"] == "text"


def test_legacy_provenance_the_corpus_never_recorded_is_null(legacy_db):
    entry = entry_detail(legacy_db, "ORO.FTL.110")["entry"]

    for field in ("source_locator", "source_url", "page_url", "revision", "regulation"):
        assert field in entry, f"{field} disappeared from the contract"
        assert entry[field] is None, (
            f"{field} is not recorded for this corpus but the lookup answered "
            f"{entry[field]!r} — provenance must never be invented"
        )


def test_an_unrecorded_page_url_is_not_reconstructed_from_the_slug(legacy_db):
    rendered = json.dumps(entry_detail(legacy_db, "ORO.FTL.110"))

    assert "easa.europa.eu" not in rendered, (
        "no URL was recorded for this corpus, so emitting one fabricates a "
        "citation the user would follow"
    )


# ── Entry text is data, not structure ───────────────────────────────────────


DANGEROUS_BODY = (
    'He said "quoted" and kept a \\backslash\\\n'
    '</script><script>alert(1)</script>\n'
    '{"entry_ref": "SPOOFED", "status": "found"}\n'
    'line separator and paragraph separator\n'
    'escape \x1b[31mred\x1b[0m and bell \x07'
)


@pytest.fixture
def dangerous_db(db) -> Database:
    _full_corpus(db)
    db.execute(
        "UPDATE regulation_entries SET body_markdown = ?, body_text = ? "
        "WHERE entry_ref = ?",
        (DANGEROUS_BODY, DANGEROUS_BODY, "ORO.FTL.110"),
    )
    return db


def test_dangerous_entry_text_round_trips_as_a_json_string(dangerous_db):
    rendered = json.dumps(entry_detail(dangerous_db, "ORO.FTL.110"))

    assert json.loads(rendered)["entry"]["text"] == DANGEROUS_BODY


def test_dangerous_entry_text_cannot_forge_the_envelope(dangerous_db):
    detail = json.loads(json.dumps(entry_detail(dangerous_db, "ORO.FTL.110")))

    assert detail["entry"]["entry_ref"] == "ORO.FTL.110"
    assert detail["status"] == "found"


def test_the_rendered_document_carries_no_raw_control_characters(dangerous_db):
    from claw_easa.retrieval.entry_detail import render_json

    rendered = render_json(entry_detail(dangerous_db, "ORO.FTL.110"))

    assert rendered.isascii(), "non-ASCII is emitted raw, so U+2028 can break consumers"
    assert " " not in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered


# ── CLI contract ────────────────────────────────────────────────────────────


def _run(*args):
    from click.testing import CliRunner

    return CliRunner().invoke(cli_main, list(args))


def test_lookup_json_emits_a_single_json_document(db):
    _full_corpus(db)
    db.close()

    result = _run("lookup", "ORO.FTL.110", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1.0"
    assert payload["entry"]["entry_ref"] == "ORO.FTL.110"


def test_lookup_json_can_be_scoped_to_a_corpus(db):
    _full_corpus(db)
    _persist(db, slug="aircrew", title="Easy Access Rules for Aircrew",
             regulations=(), entries=[_ir()])
    db.close()

    ambiguous = json.loads(_run("lookup", "ORO.FTL.110", "--json").output)
    scoped = json.loads(_run("lookup", "ORO.FTL.110", "--json", "--slug", "aircrew").output)

    assert ambiguous["status"] == "ambiguous"
    assert scoped["status"] == "found"
    assert scoped["entry"]["slug"] == "aircrew"


def test_lookup_json_reports_an_absent_reference_without_failing(db):
    _full_corpus(db)
    db.close()

    result = _run("lookup", "ORO.FTL.999", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "not_found"


EXPECTED_LOOKUP_TEXT = (
    "\nORO.FTL.110 (IR) — Fatigue management [air-ops]\n"
    "  (a) The operator shall establish a fatigue risk management system.\n"
    "  (b) The operator shall publish duty rosters in advance.\n"
    "  (c) The operator shall keep records for 24 months.\n"
    "  (d) The operator shall provide fatigue management training.\n"
    "  (e) The operator shall review the system annually.\n"
    "  ...\n"
)

EXPECTED_REFS_TEXT = (
    "  AMC1 ORO.FTL.110 (AMC) — Fatigue management - acceptable means [score=1.00]\n"
    "  GM1 ORO.FTL.110 (GM) — Fatigue management - guidance [score=1.00]\n"
    "  ORO.FTL.110 (IR) — Fatigue management [score=1.00]\n"
)


def test_lookup_text_output_is_unchanged(db):
    _full_corpus(db)
    db.close()

    assert _run("lookup", "ORO.FTL.110").output == EXPECTED_LOOKUP_TEXT


def test_lookup_text_output_for_an_absent_reference_is_unchanged(db):
    _full_corpus(db)
    db.close()

    assert _run("lookup", "ORO.FTL.999").output == "No results for: ORO.FTL.999\n"


def test_refs_text_output_is_unchanged(db):
    _full_corpus(db)
    db.close()

    assert _run("refs", "ORO.FTL.110").output == EXPECTED_REFS_TEXT


def test_lookup_text_can_be_scoped_to_a_corpus(db):
    _full_corpus(db)
    _persist(db, slug="aircrew", title="Easy Access Rules for Aircrew",
             regulations=(), entries=[_ir()])
    db.close()

    scoped = _run("lookup", "ORO.FTL.110", "--slug", "aircrew").output

    assert "[aircrew]" in scoped
    assert "[air-ops]" not in scoped
