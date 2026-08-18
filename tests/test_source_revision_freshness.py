"""Behaviour 5 — a stale local Air Operations revision must be identifiable.

EASA republishes each Easy Access Rules document as dated revisions.  Nothing
in the pipeline records which revision was ingested: ``CatalogEntry`` carries
only slug/title/URLs, ``source_documents`` has no revision column, and
``upsert_source_document_from_values`` has no revision parameter.  A corpus
built from the December 2025 Air Operations revision therefore looks exactly
like one built from the March 2026 revision that EASA currently publishes,
and answers are served from superseded rule text without warning.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import detect_anomalies
from claw_easa.ingest.catalog import EasyAccessRulesCatalogScraper
from claw_easa.ingest.repository import (
    get_document_by_slug,
    upsert_source_document_from_values,
)

CATALOG_HTML = Path(__file__).parent / "fixtures" / "easa_catalog_air_ops_march_2026.html"

LOCAL_REVISION = "December 2025"
CATALOG_REVISION = "March 2026"


@pytest.fixture
def db(tmp_path):
    reset_settings()
    database = Database(settings=Settings(data_dir=str(tmp_path), db_file="test.db"))
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()
    reset_settings()


def _air_ops_catalog_entry():
    # Same access pattern diagnostics.py uses for parser internals.
    soup = BeautifulSoup(CATALOG_HTML.read_text(), "html.parser")
    entries = EasyAccessRulesCatalogScraper._extract_entries(soup)
    return next(e for e in entries if "air-operations" in e.slug)


def test_catalog_entry_exposes_the_published_revision():
    entry = _air_ops_catalog_entry()

    revision = getattr(entry, "revision", None)

    assert revision is not None, (
        "CatalogEntry drops the revision EASA publishes, so a local copy "
        "can never be compared against the current one"
    )
    assert CATALOG_REVISION in revision, (
        f"expected the March 2026 revision, got {revision!r}"
    )


def test_source_document_records_the_ingested_revision(db):
    upsert_source_document_from_values(
        db,
        slug="air-ops",
        source_family="ear",
        title="Easy Access Rules for Air Operations",
        revision=LOCAL_REVISION,
    )

    doc = get_document_by_slug(db, "air-ops")

    assert doc["revision"] == LOCAL_REVISION, (
        "the ingested revision is not stored, so staleness cannot be assessed"
    )


def test_stale_local_revision_is_flagged():
    anomalies = detect_anomalies({
        "slug": "air-ops",
        "local_revision": LOCAL_REVISION,
        "catalog_revision": CATALOG_REVISION,
    })

    assert anomalies, (
        f"a local {LOCAL_REVISION} copy against the current "
        f"{CATALOG_REVISION} revision raises no anomaly"
    )
    freshness = [a for a in anomalies if a.category == "freshness"]
    assert freshness, (
        f"no freshness anomaly; got {[(a.category, a.message) for a in anomalies]}"
    )
    assert any(
        LOCAL_REVISION in a.message and CATALOG_REVISION in a.message
        for a in freshness
    ), (
        f"the freshness anomaly does not name both revisions: "
        f"{[a.message for a in freshness]}"
    )


def test_current_local_revision_is_not_flagged():
    anomalies = detect_anomalies({
        "slug": "air-ops",
        "local_revision": CATALOG_REVISION,
        "catalog_revision": CATALOG_REVISION,
    })

    assert [a for a in anomalies if a.category == "freshness"] == []
