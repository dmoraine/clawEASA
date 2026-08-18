"""Corpus build manifest and source provenance.

Answers "what is this corpus made of, and can it be trusted?" — for every
source: which EASA revision was ingested, from which file, and how much it
contributed.  Each build is qualified as:

``qualified``
    every expected source is present, parsed and current;
``stale``
    everything parsed, but EASA has published a newer revision of a source;
``incomplete``
    an expected source is missing, or a registered one contributed nothing;
``failed``
    the corpus holds no entries at all.

Builds are append-only.  ``last_qualified_build`` therefore keeps answering
with the last good build no matter how many incomplete or failed builds are
recorded after it — a bad rebuild degrades what is *reported*, never what was
last known to be sound.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from claw_easa import __version__
from claw_easa.db.migrations import SCHEMA_VERSION
from claw_easa.db.sqlite import Database
from claw_easa.ingest.anomalies import revision_key

log = logging.getLogger(__name__)

MANIFEST_FILE_NAME = "corpus-manifest.json"

QUALIFIED = "qualified"
STALE = "stale"
INCOMPLETE = "incomplete"
FAILED = "failed"

#: Worst status wins when a build trips more than one rule.
_SEVERITY = {QUALIFIED: 0, STALE: 1, INCOMPLETE: 2, FAILED: 3}

#: Source statuses that mean the document never reached a usable parse.
_UNPARSED_STATUSES = frozenset({"registered", "fetched", "incomplete", "error"})


@dataclass(frozen=True)
class SourceProvenance:
    """Where one source in the corpus came from."""
    slug: str
    source_family: str
    title: str | None = None
    status: str = "registered"
    revision: str | None = None
    catalog_revision: str | None = None
    checksum: str | None = None
    local_path: str | None = None
    download_url: str | None = None
    entry_count: int = 0
    parsed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BuildManifest:
    """A qualified snapshot of the corpus and what it was built from."""
    build_id: str
    status: str
    sources: list[SourceProvenance] = field(default_factory=list)
    entry_count: int = 0
    notes: list[str] = field(default_factory=list)
    tool_version: str = __version__
    schema_version: str = SCHEMA_VERSION
    created_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "build_id": self.build_id,
            "status": self.status,
            "tool_version": self.tool_version,
            "schema_version": self.schema_version,
            "entry_count": self.entry_count,
            "source_count": len(self.sources),
            "created_at": self.created_at,
            "notes": list(self.notes),
            "sources": [s.to_dict() for s in self.sources],
        }


# ── Building ────────────────────────────────────────────────────────────


def collect_provenance(
    db: Database, *, catalog_revisions: dict[str, str] | None = None,
) -> list[SourceProvenance]:
    """Read the provenance of every registered source out of the database."""
    catalog_revisions = catalog_revisions or {}
    sql = (
        "SELECT sd.slug, sd.source_family, sd.title, sd.status, sd.revision, "
        "       sd.parsed_at, "
        "       (SELECT COUNT(*) FROM regulation_entries re "
        "        WHERE re.document_id = sd.id) AS entry_count, "
        "       sf.checksum, sf.local_path, sf.download_url "
        "FROM source_documents sd "
        "LEFT JOIN source_files sf ON sf.id = ("
        "    SELECT id FROM source_files "
        "    WHERE document_id = sd.id ORDER BY downloaded_at DESC, id DESC LIMIT 1"
        ") "
        "ORDER BY sd.slug"
    )
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    return [
        SourceProvenance(
            slug=row["slug"],
            source_family=row["source_family"],
            title=row["title"],
            status=row["status"],
            revision=row["revision"],
            catalog_revision=catalog_revisions.get(row["slug"]),
            checksum=row["checksum"],
            local_path=row["local_path"],
            download_url=row["download_url"],
            entry_count=row["entry_count"],
            parsed_at=row["parsed_at"],
        )
        for row in rows
    ]


def qualify(
    sources: list[SourceProvenance], *, expected_slugs: tuple[str, ...] = (),
) -> tuple[str, list[str]]:
    """Grade a set of sources, returning ``(status, notes)``."""
    notes: list[str] = []
    status = QUALIFIED

    def worsen(candidate: str) -> None:
        nonlocal status
        if _SEVERITY[candidate] > _SEVERITY[status]:
            status = candidate

    present = {s.slug for s in sources}
    for slug in expected_slugs:
        if slug not in present:
            notes.append(f"expected source '{slug}' is not in the corpus")
            worsen(INCOMPLETE)

    total_entries = sum(s.entry_count for s in sources)
    if not sources or total_entries == 0:
        notes.append("the corpus holds no entries")
        worsen(FAILED)
        return status, notes

    for source in sources:
        if source.entry_count == 0 or source.status in _UNPARSED_STATUSES:
            notes.append(
                f"source '{source.slug}' contributed no entries "
                f"(status '{source.status}')"
            )
            worsen(INCOMPLETE)
            continue

        local, catalog = source.revision, source.catalog_revision
        if not catalog or not local or local == catalog:
            continue
        local_key, catalog_key = revision_key(local), revision_key(catalog)
        if local_key and catalog_key and local_key > catalog_key:
            continue
        notes.append(
            f"source '{source.slug}' holds revision '{local}', superseded by "
            f"the published revision '{catalog}'"
        )
        worsen(STALE)

    return status, notes


def build_manifest(
    db: Database,
    *,
    catalog_revisions: dict[str, str] | None = None,
    expected_slugs: tuple[str, ...] = (),
) -> BuildManifest:
    """Qualify the corpus as it currently stands.

    *catalog_revisions* maps a slug to the revision EASA publishes today;
    without it staleness cannot be assessed and is not claimed.
    """
    sources = collect_provenance(db, catalog_revisions=catalog_revisions)
    status, notes = qualify(sources, expected_slugs=expected_slugs)
    now = datetime.now(timezone.utc)
    return BuildManifest(
        build_id=f"build-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}",
        status=status,
        sources=sources,
        entry_count=sum(s.entry_count for s in sources),
        notes=notes,
        created_at=now.isoformat(timespec="seconds"),
    )


# ── Persistence ─────────────────────────────────────────────────────────


def record_build(db: Database, manifest: BuildManifest) -> BuildManifest:
    """Append *manifest* to the build history.

    Never updates or deletes an earlier build, so the last qualified one
    survives every later incomplete or failed build.
    """
    payload = manifest.to_dict()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus_builds "
                "(build_id, status, tool_version, schema_version, "
                " source_count, entry_count, notes, manifest_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    manifest.build_id,
                    manifest.status,
                    manifest.tool_version,
                    manifest.schema_version,
                    len(manifest.sources),
                    manifest.entry_count,
                    "\n".join(manifest.notes) or None,
                    json.dumps(payload),
                ),
            )
            build_db_id = cur.lastrowid
            for source in manifest.sources:
                cur.execute(
                    "INSERT INTO corpus_build_sources "
                    "(build_db_id, slug, source_family, title, status, revision, "
                    " catalog_revision, checksum, local_path, download_url, "
                    " entry_count, parsed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        build_db_id, source.slug, source.source_family,
                        source.title, source.status, source.revision,
                        source.catalog_revision, source.checksum,
                        source.local_path, source.download_url,
                        source.entry_count, source.parsed_at,
                    ),
                )
        conn.commit()

    log.info("Recorded corpus build %s (%s)", manifest.build_id, manifest.status)
    return manifest


def _row_to_manifest(row: dict | None) -> BuildManifest | None:
    if row is None:
        return None
    payload = json.loads(row["manifest_json"])
    return BuildManifest(
        build_id=payload["build_id"],
        status=payload["status"],
        sources=[SourceProvenance(**s) for s in payload.get("sources", [])],
        entry_count=payload.get("entry_count", 0),
        notes=payload.get("notes", []),
        tool_version=payload.get("tool_version", ""),
        schema_version=payload.get("schema_version", ""),
        created_at=payload.get("created_at"),
    )


def latest_build(db: Database) -> BuildManifest | None:
    """The most recent build, whatever its status."""
    return _row_to_manifest(
        db.fetch_one("SELECT * FROM corpus_builds ORDER BY id DESC LIMIT 1")
    )


def last_qualified_build(db: Database) -> BuildManifest | None:
    """The most recent qualified build, ignoring anything recorded after it."""
    return _row_to_manifest(
        db.fetch_one(
            "SELECT * FROM corpus_builds WHERE status = ? ORDER BY id DESC LIMIT 1",
            (QUALIFIED,),
        )
    )


def export_manifest(
    db: Database, path: str | Path, *, current: BuildManifest | None = None,
) -> Path:
    """Write the current and last qualified build as JSON.

    *current* defaults to the head of the build history.  The last qualified
    build always comes from the history, so exporting an unqualified build
    cannot overwrite the record of the last good one.
    """
    current = current or latest_build(db)
    qualified = last_qualified_build(db)
    payload = {
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "current": current.to_dict() if current else None,
        "last_qualified": qualified.to_dict() if qualified else None,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    return out
