"""``claw-easa refs`` answered as a citation, without changing what it prints.

The text output names a rule but not where to read it back, so a caller that
consumes ``refs`` holds a reference and no way to follow it.  Everything
needed is already stored: the URL the source published for the entry where
there is one, the URL of the document holding it otherwise, the locator
saying where in that document the entry was parsed from, the slug, and the
EASA revision held.

Both halves are pinned here.  The added JSON output projects that provenance
— entry URL before document URL, and no URL at all rather than one spelled
out of a reference — while the default text output, its results, its scores
and its limits stay exactly as they were.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

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
from claw_easa.ingest.repository import upsert_faq_entry, upsert_source_document_from_values
from claw_easa.retrieval.exact import search_references
from claw_easa.retrieval.provenance import refs_payload

AIR_OPS_PAGE = (
    "https://www.easa.europa.eu/en/document-library/easy-access-rules/"
    "easy-access-rules-air-operations"
)
AIR_OPS_DOWNLOAD = "https://www.easa.europa.eu/en/downloads/136682/en"
AIR_OPS_REVISION = "March 2026"
FAQ_PAGE = "https://www.easa.europa.eu/en/the-agency/faqs/air-operations"
FAQ_ENTRY_URL = f"{FAQ_PAGE}#duty-rosters"
OCCURRENCE_PAGE = (
    "https://www.easa.europa.eu/en/document-library/general-publications/"
    "occurrence-reporting-rule-book"
)

#: Body wording the JSON contract must not copy: ``refs`` answers with
#: references, and the text is served by ``lookup`` and ``snippets``.
RULE_BODY_LINE = "The operator shall publish duty rosters sufficiently in advance."


def _persist(db: Database, doc_id: int, part_code: str, entries: list[ParsedEntry]) -> None:
    section = ParsedSection(title="Section I — General", sort_order=0, entries=entries)
    subpart = ParsedSubpart(
        code=f"Subpart {part_code}", title="General requirements",
        sort_order=0, sections=[section],
    )
    part = ParsedPart(
        code=part_code, annex="Annex III", title=f"Part-{part_code}",
        sort_order=0, subparts=[subpart],
    )
    CanonicalPersister(db).persist_document(
        doc_id, ParsedDocument(title=f"Part-{part_code}", parts=[part]),
    )


def _seed(db: Database) -> None:
    """A corpus holding each state the URL fallback has to answer for."""
    # Fetched from the EASA catalogue: page, download URL and revision known,
    # entries with a locator but no URL of their own.
    air_ops = upsert_source_document_from_values(
        db, slug="air-ops", source_family="ear",
        title="Easy Access Rules for Air Operations",
        page_url=AIR_OPS_PAGE, source_url=AIR_OPS_DOWNLOAD,
        revision=AIR_OPS_REVISION, published_at="2026-03-01",
    )
    _persist(db, air_ops, "ORO", [
        ParsedEntry(
            entry_ref="ORO.FTL.110",
            entry_type="IR",
            title="ORO.FTL.110 Operator responsibilities",
            body_lines=[RULE_BODY_LINE],
            source_locator="paragraphs:118-124",
            sort_order=0,
        ),
        ParsedEntry(
            entry_ref="ORO.FTL.120",
            entry_type="IR",
            title="ORO.FTL.120 Flight time specification schemes",
            body_lines=["The operator shall establish flight time specification schemes."],
            source_locator="paragraphs:125-131",
            sort_order=1,
        ),
    ])

    # A FAQ: the source publishes a URL for the answer itself.
    faq = upsert_source_document_from_values(
        db, slug="faq-air-operations", source_family="faq",
        title="FAQ — Air Operations", page_url=FAQ_PAGE,
    )
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO regulation_parts (document_id, part_code, annex, title, sort_order) "
                "VALUES (?, 'FAQ', '', 'Air Operations', 0)",
                (faq,),
            )
            part_id = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_subparts (part_id, subpart_code, title, sort_order) "
                "VALUES (?, 'FAQ', 'Air Operations', 0)",
                (part_id,),
            )
            subpart_id = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_sections (subpart_id, section_code, title, sort_order) "
                "VALUES (?, NULL, 'Air Operations', 0)",
                (subpart_id,),
            )
            section_id = cur.lastrowid
        conn.commit()
    upsert_faq_entry(
        db, document_id=faq, part_id=part_id, subpart_id=subpart_id, section_id=section_id,
        entry_ref="FAQ-air-operations-001",
        title="How far in advance must duty rosters be published?",
        body_text="Rosters are published so that crew members can plan adequate rest.",
        source_url=FAQ_ENTRY_URL,
    )

    # Resolved from its EASA page, but no download URL was recorded for it.
    occurrence = upsert_source_document_from_values(
        db, slug="occurrence-reporting", source_family="rulebook",
        title="Occurrence Reporting Rule Book", page_url=OCCURRENCE_PAGE,
    )
    _persist(db, occurrence, "ORO2", [
        ParsedEntry(
            entry_ref="ORO.GEN.160",
            entry_type="IR",
            title="ORO.GEN.160 Occurrence reporting",
            body_lines=["The operator shall report every occurrence to the authority."],
            source_locator="paragraphs:12-18",
            sort_order=0,
        ),
    ])

    # Imported from a local file: nothing recorded a URL or a revision.
    local = upsert_source_document_from_values(
        db, slug="information-security", source_family="ear",
        title="Easy Access Rules for Information Security",
    )
    _persist(db, local, "IS", [
        ParsedEntry(
            entry_ref="IS.I.OR.200",
            entry_type="IR",
            title="IS.I.OR.200 Information security management system",
            body_lines=["The organisation shall establish an information security policy."],
            source_locator="paragraphs:40-58",
            sort_order=0,
        ),
    ])


@pytest.fixture
def settings(tmp_path):
    reset_settings()
    yield Settings(data_dir=str(tmp_path), db_file="test.db")
    reset_settings()


@pytest.fixture
def db(settings):
    database = Database(settings=settings)
    database.open()
    MigrationRunner(database).init_schema()
    _seed(database)
    yield database
    database.close()


@pytest.fixture
def cli(db, settings, monkeypatch):
    """A runner whose commands resolve to the seeded corpus."""
    monkeypatch.setattr("claw_easa.config._settings", settings)
    return CliRunner()


def _run(cli: CliRunner, *args: str) -> str:
    result = cli.invoke(cli_main, ["refs", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _source_of(db: Database, query: str, entry_ref: str) -> dict:
    rows = search_references(db, query)
    payload = refs_payload(rows, query, limit=10)
    matched = [r for r in payload["results"] if r["entry_ref"] == entry_ref]
    assert matched, f"{entry_ref} was not returned for {query!r}"
    return matched[0]["source"]


PROVENANCE_COLUMNS = (
    "entry_url", "entry_locator", "slug",
    "document_url", "document_page_url", "document_revision",
)


@pytest.mark.parametrize(
    "query, path",
    [
        # A bare reference is matched against entry_ref alone …
        ("ORO.FTL.110", "reference"),
        # … free text goes through FTS …
        ("operator responsibilities", "fts"),
        # … and a fragment no FTS token can match falls back to LIKE.
        ("osters sufficiently", "like"),
    ],
)
def test_every_match_path_projects_the_same_provenance(db, query, path):
    rows = search_references(db, query)

    assert rows, f"the {path} path returned nothing for {query!r}"
    for row in rows:
        missing = [column for column in PROVENANCE_COLUMNS if column not in row]
        assert not missing, f"the {path} path does not project {missing}"


def test_entry_url_is_preferred_over_the_document_url(db):
    source = _source_of(db, "duty rosters", "FAQ-air-operations-001")

    assert source["url"] == FAQ_ENTRY_URL, (
        "the FAQ publishes a URL for the answer itself, but the citation "
        f"points at {source['url']}"
    )
    assert source["url_kind"] == "entry"


def test_document_url_answers_an_entry_that_has_none(db):
    source = _source_of(db, "ORO.FTL.110", "ORO.FTL.110")

    assert source["url"] == AIR_OPS_DOWNLOAD
    assert source["url_kind"] == "document"
    assert source["locator"] == "paragraphs:118-124", (
        "the document URL cites a whole EAR, so the locator is what says "
        "where in it the entry was parsed from"
    )
    assert source["slug"] == "air-ops"
    assert source["revision"] == AIR_OPS_REVISION


def test_document_page_url_answers_a_document_without_a_download_url(db):
    source = _source_of(db, "ORO.GEN.160", "ORO.GEN.160")

    assert source["url"] == OCCURRENCE_PAGE, (
        "the EASA page is the only URL recorded for this source and is "
        "provenance just the same"
    )
    assert source["url_kind"] == "document"


def test_a_missing_url_is_reported_rather_than_derived(db):
    source = _source_of(db, "IS.I.OR.200", "IS.I.OR.200")

    assert source["url"] is None, (
        "nothing recorded a URL for this source, so any URL here was "
        "fabricated from an identifier"
    )
    assert source["url_kind"] is None
    assert "http" not in json.dumps(source)
    # What *is* known still answers: the slug and the locator locate the rule
    # in the corpus even where no address was published for it.
    assert source["slug"] == "information-security"
    assert source["locator"] == "paragraphs:40-58"
    assert source["revision"] is None


def test_refs_json_is_valid_and_carries_every_result_with_its_provenance(cli):
    payload = json.loads(_run(cli, "operator responsibilities", "--json"))

    assert payload["query"] == "operator responsibilities"
    assert payload["count"] == len(payload["results"]) >= 1
    for result in payload["results"]:
        assert set(result) == {"entry_ref", "entry_type", "title", "score", "source"}
        assert isinstance(result["score"], (int, float))
        assert set(result["source"]) == {"slug", "url", "url_kind", "locator", "revision"}


def test_refs_json_leaves_the_regulation_text_out(cli):
    output = _run(cli, "duty rosters", "--json")

    assert RULE_BODY_LINE not in output
    assert "body_text" not in output


def test_refs_json_preserves_the_scores_and_the_limit(db, cli):
    unlimited = json.loads(_run(cli, "operator", "--slug", "air-ops", "--json"))
    limited = json.loads(_run(cli, "operator", "--slug", "air-ops", "--limit", "1", "--json"))

    assert unlimited["count"] > 1, "the limit is not exercised by this corpus"
    assert limited["count"] == 1
    assert limited["limit"] == 1
    assert limited["slug"] == "air-ops"

    scored = {r["entry_ref"]: r["score"] for r in unlimited["results"]}
    for row in search_references(db, "operator", slug="air-ops"):
        assert scored[row["entry_ref"]] == row["fts_score"], (
            "the JSON output rescores results instead of reporting the "
            "score retrieval ranked them by"
        )


def test_refs_json_reports_no_result_as_an_empty_result_set(cli):
    payload = json.loads(_run(cli, "nonexistentzzz", "--json"))

    assert payload["results"] == []
    assert payload["count"] == 0
    assert payload["query"] == "nonexistentzzz"


def test_refs_text_output_is_unchanged(cli):
    output = _run(cli, "operator responsibilities")

    assert "  ORO.FTL.110 (IR) — Operator responsibilities [score=" in output
    assert "{" not in output and "url" not in output
    # The same results, whichever output was asked for.
    payload = json.loads(_run(cli, "operator responsibilities", "--json"))
    assert [line.split(" (")[0].strip() for line in output.strip().split("\n")] == [
        result["entry_ref"] for result in payload["results"]
    ]


def test_refs_text_still_reports_no_result_in_words(cli):
    output = _run(cli, "nonexistentzzz")

    assert output.strip() == "No results for: nonexistentzzz"
