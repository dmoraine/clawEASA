"""Corpus build manifest — provenance, qualification status, JSON export.

A build of the corpus has to say what it was built from and whether it is fit
to answer from: which sources, which EASA revision of each, how many entries
they contributed.  The qualification statuses are ``qualified``,
``freshness-unknown``, ``incomplete``, ``stale`` and ``failed``.

``qualified`` is claimed only where a comparison against what EASA publishes
was actually made, so every test that expects it hands ``build_manifest`` the
catalogue revision to compare against.  Without one nothing establishes that
the corpus is current and the build grades ``freshness-unknown`` — the
ordinary outcome for a build nobody checked, pinned in
``TestFreshnessUnknown``.

The load-bearing rule is the one ``TestPersistence`` tests: a build that did
not qualify must never displace the last one that did.

The build history in SQLite records the operational vocabulary — a qualified
build is stored as ``current`` — so ``TestRecordedStatus`` pins what crosses
that boundary in each direction.
"""
from __future__ import annotations

import json

import pytest

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.manifest import (
    build_manifest,
    export_manifest,
    last_qualified_build,
    latest_build,
    record_build,
)
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import (
    ParsedDocument,
    ParsedEntry,
    ParsedPart,
    ParsedSection,
    ParsedSubpart,
)
from claw_easa.ingest.repository import upsert_source_document_from_values


@pytest.fixture
def db(tmp_path):
    reset_settings()
    database = Database(settings=Settings(data_dir=str(tmp_path), db_file="test.db"))
    database.open()
    MigrationRunner(database).init_schema()
    yield database
    database.close()
    reset_settings()


def _parsed(ref: str = "ORO.GEN.200") -> ParsedDocument:
    entry = ParsedEntry(
        entry_ref=ref,
        entry_type="IR",
        title=f"{ref} Management system",
        body_lines=["The operator shall establish a management system."],
        sort_order=0,
        source_locator="paragraphs:1-2",
    )
    section = ParsedSection(title="General", sort_order=1, entries=[entry])
    subpart = ParsedSubpart(code="GEN", title="General", sort_order=1, sections=[section])
    part = ParsedPart(
        code="ORO", title="ANNEX III (Part-ORO)", annex="III", sort_order=1,
        subparts=[subpart],
    )
    return ParsedDocument(title="Air Ops", parts=[part])


def _ingest(db: Database, slug: str, *, revision: str | None = None,
            entries: bool = True) -> int:
    doc_id = upsert_source_document_from_values(
        db, slug=slug, source_family="ear", title=f"EAR {slug}", revision=revision,
    )
    parsed = _parsed(f"{slug.upper().replace('-', '')}.GEN.200") if entries else ParsedDocument(
        title=slug, parts=[],
    )
    CanonicalPersister(db).persist_document(doc_id, parsed)
    return doc_id


#: What EASA publishes for the source ``_ingest`` registers by default — the
#: same revision, so a build handed this one grades ``qualified``.  A build
#: that expects ``qualified`` has to be handed something to compare against.
CATALOG = {"air-ops": "March 2026"}


class TestQualification:
    def test_a_complete_current_corpus_qualifies(self, db):
        _ingest(db, "air-ops", revision="March 2026")

        manifest = build_manifest(db, catalog_revisions={"air-ops": "March 2026"})

        assert manifest.status == "qualified"
        assert manifest.entry_count == 1
        assert [s.slug for s in manifest.sources] == ["air-ops"]
        assert manifest.sources[0].revision == "March 2026"

    def test_superseded_revision_makes_the_build_stale(self, db):
        _ingest(db, "air-ops", revision="December 2025")

        manifest = build_manifest(db, catalog_revisions={"air-ops": "March 2026"})

        assert manifest.status == "stale"
        assert any("December 2025" in note and "March 2026" in note
                   for note in manifest.notes)

    def test_expected_source_absent_makes_the_build_incomplete(self, db):
        """The absent source is the only fault: the present one is current."""
        _ingest(db, "air-ops", revision="March 2026")

        manifest = build_manifest(
            db,
            catalog_revisions={"air-ops": "March 2026"},
            expected_slugs=("air-ops", "information-security"),
        )

        assert manifest.status == "incomplete"
        assert any("information-security" in note for note in manifest.notes)

    def test_source_that_contributed_nothing_makes_the_build_incomplete(self, db):
        _ingest(db, "air-ops", revision="March 2026")
        _ingest(db, "mystery-ear", entries=False)

        manifest = build_manifest(db, catalog_revisions={"air-ops": "March 2026"})

        assert manifest.status == "incomplete"
        assert any("mystery-ear" in note for note in manifest.notes)

    def test_a_corpus_with_no_entries_at_all_fails(self, db):
        _ingest(db, "mystery-ear", entries=False)

        manifest = build_manifest(db)

        assert manifest.status == "failed"


#: Five sources carrying no revision label, which is what the ingest path
#: registers today: nothing in it reads the EASA catalogue.
UNLABELLED_SLUGS = (
    "air-ops",
    "aircrew",
    "basic-regulation",
    "information-security",
    "initial-airworthiness",
)


class TestFreshnessUnknown:
    """The ordinary outcome: a whole corpus nobody compared against EASA.

    Every source parsed and contributed entries, so nothing is missing — but
    no source holds a revision label and no catalogue was supplied, so no
    edition comparison was possible anywhere.  The build has to keep saying
    so at every layer it crosses — the manifest, the build history, the
    exported JSON — rather than passing as qualified by default, and it must
    not become the build ``last_qualified_build`` offers.
    """

    def test_a_corpus_nothing_compared_grades_freshness_unknown(self, db, tmp_path):
        for slug in UNLABELLED_SLUGS:
            _ingest(db, slug)

        manifest = build_manifest(db)

        assert manifest.status == "freshness-unknown"
        assert len(manifest.sources) == 5
        assert manifest.entry_count == 5, "a source contributed nothing"
        assert [s.freshness for s in manifest.sources] == ["freshness-unknown"] * 5

        recorded = record_build(db, manifest)

        row = db.fetch_one(
            "SELECT status FROM corpus_builds WHERE build_id = ?", (recorded.build_id,)
        )
        assert row["status"] == "freshness-unknown", (
            f"an uncompared build is recorded as {row['status']!r}"
        )
        assert last_qualified_build(db) is None, (
            "a build nothing compared against EASA is offered as the last "
            "qualified one"
        )

        payload = json.loads(export_manifest(db, tmp_path / "m.json").read_text())

        assert payload["current"]["status"] == "freshness-unknown"
        assert payload["last_qualified"] is None


class TestPersistence:
    def test_recorded_build_is_the_latest(self, db):
        _ingest(db, "air-ops", revision="March 2026")
        recorded = record_build(
            db, build_manifest(db, catalog_revisions=CATALOG),
        )

        assert latest_build(db).build_id == recorded.build_id
        assert last_qualified_build(db).build_id == recorded.build_id

    def test_incomplete_build_never_replaces_the_last_qualified_one(self, db):
        _ingest(db, "air-ops", revision="March 2026")
        qualified = record_build(db, build_manifest(db, catalog_revisions=CATALOG))
        assert qualified.status == "qualified"

        _ingest(db, "mystery-ear", entries=False)
        incomplete = record_build(db, build_manifest(db, catalog_revisions=CATALOG))
        assert incomplete.status == "incomplete"

        assert latest_build(db).build_id == incomplete.build_id
        preserved = last_qualified_build(db)
        assert preserved.build_id == qualified.build_id
        assert preserved.status == "qualified"
        assert preserved.entry_count == qualified.entry_count

    def test_failed_build_never_replaces_the_last_qualified_one(self, db):
        _ingest(db, "air-ops", revision="March 2026")
        qualified = record_build(db, build_manifest(db, catalog_revisions=CATALOG))

        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM regulation_entries")
            conn.commit()

        failed = record_build(db, build_manifest(db, catalog_revisions=CATALOG))
        assert failed.status == "failed"
        assert last_qualified_build(db).build_id == qualified.build_id

    def test_build_ids_are_unique_across_builds(self, db):
        _ingest(db, "air-ops", revision="March 2026")
        first = record_build(db, build_manifest(db))
        second = record_build(db, build_manifest(db))

        assert first.build_id != second.build_id


#: ``corpus_builds`` exactly as schema 003 left it, for the upgrade path.
LEGACY_CORPUS_BUILDS = """
CREATE TABLE corpus_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL
        CHECK(status IN ('qualified', 'incomplete', 'stale', 'failed')),
    tool_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0,
    entry_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TestRecordedStatus:
    """What the manifest grades, and what the build history stores.

    The two vocabularies meet in ``record_build``.  A build graded now is
    recorded as ``current``; a build some older version already stored as
    ``qualified`` is not, because nothing compared it against EASA.
    """

    def test_a_qualified_build_is_recorded_as_current(self, db):
        _ingest(db, "air-ops", revision="March 2026")

        recorded = record_build(
            db, build_manifest(db, catalog_revisions={"air-ops": "March 2026"}),
        )

        assert recorded.status == "qualified", "the manifest changed vocabulary"
        row = db.fetch_one(
            "SELECT status FROM corpus_builds WHERE build_id = ?", (recorded.build_id,)
        )
        assert row["status"] == "current", (
            f"a qualified build is recorded as {row['status']!r}"
        )
        assert last_qualified_build(db).build_id == recorded.build_id

    @pytest.mark.parametrize("status", ["incomplete", "failed"])
    def test_every_other_status_is_recorded_unchanged(self, db, status):
        if status == "incomplete":
            _ingest(db, "air-ops", revision="March 2026")
        _ingest(db, "mystery-ear", entries=False)

        recorded = record_build(db, build_manifest(db))

        assert recorded.status == status
        row = db.fetch_one(
            "SELECT status FROM corpus_builds WHERE build_id = ?", (recorded.build_id,)
        )
        assert row["status"] == status

    def test_a_build_stored_as_qualified_migrates_to_freshness_unknown(self, tmp_path):
        legacy = Database(
            settings=Settings(data_dir=str(tmp_path), db_file="legacy.db")
        )
        legacy.open()
        legacy.execute_script(LEGACY_CORPUS_BUILDS)
        legacy.execute(
            "INSERT INTO corpus_builds (build_id, status, tool_version, "
            "schema_version, entry_count, manifest_json) "
            "VALUES ('build-legacy', 'qualified', '0.1.0', "
            "'003_regulation_provenance', 1, ?)",
            (json.dumps({"build_id": "build-legacy", "status": "qualified"}),),
        )

        MigrationRunner(legacy).init_schema()

        row = legacy.fetch_one(
            "SELECT status, role FROM corpus_builds WHERE build_id = 'build-legacy'"
        )
        assert row["status"] == "freshness-unknown", (
            f"a build nothing ever compared against EASA reads as {row['status']!r}"
        )
        assert last_qualified_build(legacy) is None, (
            "a migrated build is offered as the last qualified one"
        )
        assert row["role"] == "current", (
            "the database holds a corpus but no build is in the current slot"
        )
        legacy.close()


class TestJSONExport:
    def test_export_writes_current_and_last_qualified(self, db, tmp_path):
        _ingest(db, "air-ops", revision="March 2026")
        qualified = record_build(db, build_manifest(db, catalog_revisions=CATALOG))

        _ingest(db, "mystery-ear", entries=False)
        incomplete = record_build(db, build_manifest(db, catalog_revisions=CATALOG))

        path = export_manifest(db, tmp_path / "corpus-manifest.json")
        payload = json.loads(path.read_text())

        assert payload["current"]["build_id"] == incomplete.build_id
        assert payload["current"]["status"] == "incomplete"
        assert payload["last_qualified"]["build_id"] == qualified.build_id
        assert payload["last_qualified"]["status"] == "qualified"

    def test_exported_source_carries_its_provenance(self, db, tmp_path):
        _ingest(db, "air-ops", revision="March 2026")
        record_build(db, build_manifest(db, catalog_revisions={"air-ops": "March 2026"}))

        payload = json.loads(export_manifest(db, tmp_path / "m.json").read_text())
        source = payload["current"]["sources"][0]

        assert source["slug"] == "air-ops"
        assert source["revision"] == "March 2026"
        assert source["catalog_revision"] == "March 2026"
        assert source["entry_count"] == 1
        assert source["status"] == "parsed"

    def test_export_without_any_qualified_build_says_so(self, db, tmp_path):
        _ingest(db, "mystery-ear", entries=False)
        record_build(db, build_manifest(db))

        payload = json.loads(export_manifest(db, tmp_path / "m.json").read_text())

        assert payload["current"]["status"] == "failed"
        assert payload["last_qualified"] is None
