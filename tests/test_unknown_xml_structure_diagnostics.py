"""Behaviour 6 — an unrecognised XML structure must not report success.

When no ANNEX/Part heading, Article heading or CS entry is found,
``parse_file`` falls through to ``_parse_parts``, which returns an empty
list.  The result is a ``ParsedDocument`` that claims ``parser_mode="part"``
with zero parts, ``persist_document`` marks the source ``status='parsed'``,
``detect_anomalies`` stays silent and ``format_report`` prints
``Verdict: PASS`` at 100% heading coverage.

Nothing anywhere says the document was not understood — the exact failure
mode that hides a changed EASA XML layout behind a green ingest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.diagnostics import coverage_report, format_report
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser, ParsedDocument
from claw_easa.ingest.repository import (
    get_document_by_slug,
    upsert_source_document_from_values,
)

FIXTURE = Path(__file__).parent / "fixtures" / "unknown_structure.xml"
DOC_TITLE = "Easy Access Rules for an Unrecognised Domain"

KNOWN_MODES = frozenset({"part", "hybrid", "article-structured", "cs-structured"})


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


def test_unrecognised_document_does_not_claim_a_known_parser_mode():
    doc = _parse_fixture()

    assert doc.parts == [], "fixture precondition: nothing should be extractable"
    assert doc.parser_mode not in KNOWN_MODES, (
        f"parser_mode is {doc.parser_mode!r} for a document it did not "
        f"understand; an unrecognised layout needs its own signal"
    )


def test_empty_parse_is_reported_as_an_anomaly():
    doc = _parse_fixture()

    anomalies = detect_anomalies({
        "entry_count": 0,
        "paragraph_count": doc.paragraph_count,
        "parser_mode": doc.parser_mode,
    })

    assert anomalies, (
        f"{doc.paragraph_count} paragraphs of regulation text yielded zero "
        f"entries and no anomaly was raised"
    )
    assert any(a.severity == "error" for a in anomalies), (
        f"extracting nothing is not an error-severity anomaly; got "
        f"{[(a.severity, a.category, a.message) for a in anomalies]}"
    )


def test_coverage_report_does_not_pass_a_document_with_no_entries():
    report = coverage_report(FIXTURE, DOC_TITLE)

    assert report.entries == 0, "fixture precondition"
    assert "Verdict: PASS" not in format_report(report), (
        "the coverage report passes a document from which nothing was "
        "extracted:\n" + format_report(report)
    )


def test_source_is_not_marked_parsed_when_nothing_was_extracted(db):
    doc_id = upsert_source_document_from_values(
        db, slug="mystery-ear", source_family="ear", title=DOC_TITLE,
    )

    summary = CanonicalPersister(db).persist_document(doc_id, _parse_fixture())

    assert summary.entries == 0, "fixture precondition"
    status = get_document_by_slug(db, "mystery-ear")["status"]
    assert status != "parsed", (
        "the source is recorded as successfully parsed although it produced "
        "no parts, subparts, sections or entries"
    )
