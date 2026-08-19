"""Corpus build manifest and source provenance.

Answers "what is this corpus made of, and can it be trusted?" — for every
source: which EASA revision was ingested, from which file, and how much it
contributed.  Each build is qualified as:

``qualified``
    every expected source is present, parsed, and demonstrably the edition
    EASA publishes;
``freshness-unknown``
    everything parsed, but no comparison against what EASA publishes was
    possible, so nothing establishes that the corpus is up to date;
``stale``
    everything parsed, but EASA has published a newer revision of a source;
``incomplete``
    an expected source is missing, or a registered one contributed nothing;
``failed``
    the corpus holds no entries at all.

``qualified`` is claimed only where a comparison was actually made.  A source
that carries no revision label, or that was never checked against the EASA
catalogue, grades ``freshness-unknown`` — which is the ordinary outcome, not
an edge case: nothing in the ingest path records a catalogue revision yet, so
a corpus built today says *I cannot show this is current* rather than
claiming that it is.

The grading itself lives in ``claw_easa.freshness`` and is not repeated here:
this module settles what is missing or unparsed, defers the edition
comparison to ``grade_freshness``, and rolls the per-source grades up on that
module's severity ladder.

Builds are append-only.  ``last_qualified_build`` therefore keeps answering
with the last good build no matter how many incomplete or failed builds are
recorded after it — a bad rebuild degrades what is *reported*, never what was
last known to be sound.

The build history in SQLite records the operational vocabulary defined in
``claw_easa.freshness``, where the grade for a complete, unsuperseded corpus
is ``current``.  ``qualified`` is this module's own older name for that same
state, so it is translated at the database boundary and nowhere else: a
manifest — in memory and in the exported JSON — keeps saying ``qualified``.
Every other status is spelled the same in both vocabularies and crosses that
boundary unchanged.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from claw_easa import __version__
from claw_easa.db.migrations import SCHEMA_VERSION
from claw_easa.db.sqlite import Database
from claw_easa.freshness import (
    CURRENT,
    FAILED,
    FRESHNESS_UNKNOWN,
    INCOMPLETE,
    STALE,
    Freshness,
    grade_freshness,
    rollup_status,
)

log = logging.getLogger(__name__)

MANIFEST_FILE_NAME = "corpus-manifest.json"

#: The manifest's own name for a corpus that is complete and demonstrably
#: current.  The other four statuses are ``claw_easa.freshness``'s own names,
#: re-exported above so that callers can keep importing every status this
#: module reports from this module.
QUALIFIED = "qualified"

#: The manifest's name for a freshness status, where the two differ.
_MANIFEST_STATUS = {CURRENT: QUALIFIED}

#: The inverse, applied when a graded build is written to the build history.
#:
#: Note the asymmetry with the migration in ``db.migrations``, which carries a
#: row *already stored* as 'qualified' over to 'freshness-unknown' instead.
#: Both are right: a build recorded here has just been graded by ``qualify``
#: against the catalogue revisions the caller supplied, while a row written by
#: an older version is a closed record that can no longer be compared against
#: anything — nothing in it establishes that it was up to date.
_RECORDED_STATUS = {status: name for name, status in _MANIFEST_STATUS.items()}

#: Source statuses that mean the document never reached a usable parse.
_UNPARSED_STATUSES = frozenset({"registered", "fetched", "incomplete", "error"})

#: Why a source was graded ``incomplete`` before any edition comparison.
_NO_ENTRIES = "no-entries"


@dataclass(frozen=True)
class RegulationProvenance:
    """What one regulation contributed to one source in this build.

    ``part_codes`` and ``entry_count`` describe what the build actually
    holds, not what the regulation declares: a regulation that states an
    annex the parse dropped reports no parts and no entries, which is what
    holds the build out of ``qualified``.
    """
    identifier: str
    title: str
    kind: str
    part_codes: tuple[str, ...] = ()
    entry_count: int = 0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["part_codes"] = list(self.part_codes)
        return payload


@dataclass(frozen=True)
class SourceProvenance:
    """Where one source in the corpus came from.

    ``status`` is the ingestion lifecycle — what the pipeline did with the
    document.  ``freshness`` is the operational grade of what it holds, which
    is a different question: a source can be 'parsed' and still be 'stale'.
    """
    slug: str
    source_family: str
    title: str | None = None
    status: str = "registered"
    #: Where the artefact came from: the EASA page it was resolved from, the
    #: URL given for it, and the URL it was actually downloaded from.
    page_url: str | None = None
    source_url: str | None = None
    #: The edition held — EASA's own label and that label as a sortable date.
    revision: str | None = None
    published_at: str | None = None
    #: What the EASA catalogue advertised, and when it was read.  All three
    #: absent is the ordinary state of a source nobody checked, and grades
    #: ``freshness-unknown`` rather than passing as current.
    catalog_revision: str | None = None
    catalog_published_at: str | None = None
    catalog_checked_at: str | None = None
    #: Which parser produced the entries held.
    parser_version: str | None = None
    #: The artefact itself: its SHA-256, where it is kept, and when it was
    #: retrieved.
    checksum: str | None = None
    local_path: str | None = None
    download_url: str | None = None
    retrieved_at: str | None = None
    entry_count: int = 0
    parsed_at: str | None = None
    #: This source on the ladder of ``claw_easa.freshness``, as ``qualify``
    #: graded it, with the basis and detail saying why.  The default is what
    #: an ungraded source is worth — a source nothing has compared against
    #: EASA is never assumed to be current, including one read back from a
    #: manifest recorded before the grade was carried.
    freshness: str = FRESHNESS_UNKNOWN
    freshness_basis: str | None = None
    freshness_detail: str | None = None
    #: What each declared regulation contributed, in the declared order.
    #: Empty for a source that declares no regulation provenance.
    regulations: tuple[RegulationProvenance, ...] = ()
    #: Parts held by this source that no declared regulation claims.  Always
    #: empty where no regulations are declared — attribution is required only
    #: where provenance is declared.
    unattributed_parts: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["regulations"] = [r.to_dict() for r in self.regulations]
        payload["unattributed_parts"] = list(self.unattributed_parts)
        return payload


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


def _collect_regulations(db: Database) -> dict[int, tuple[RegulationProvenance, ...]]:
    """What each declared regulation contributed, keyed by document id.

    Left-joined on the parts attributed to the regulation, so a regulation
    that contributed nothing to this build is still reported — with no parts
    and no entries — instead of dropping out of the manifest silently.
    """
    sql = (
        "SELECT sr.document_id, sr.identifier, sr.title, sr.kind, "
        "       sr.sort_order, rp.part_code, "
        "       (SELECT COUNT(*) FROM regulation_entries re "
        "        WHERE re.part_id = rp.id) AS entry_count "
        "FROM source_regulations sr "
        "LEFT JOIN regulation_parts rp "
        "       ON rp.document_id = sr.document_id "
        "      AND rp.regulation = sr.identifier "
        "ORDER BY sr.document_id, sr.sort_order, sr.id, rp.sort_order, rp.id"
    )
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    collected: dict[int, dict[str, dict]] = {}
    for row in rows:
        by_identifier = collected.setdefault(row["document_id"], {})
        regulation = by_identifier.setdefault(
            row["identifier"],
            {
                "title": row["title"],
                "kind": row["kind"],
                "part_codes": [],
                "entry_count": 0,
            },
        )
        if row["part_code"] is None:
            continue
        regulation["part_codes"].append(row["part_code"])
        regulation["entry_count"] += row["entry_count"] or 0

    return {
        document_id: tuple(
            RegulationProvenance(
                identifier=identifier,
                title=regulation["title"],
                kind=regulation["kind"],
                part_codes=tuple(regulation["part_codes"]),
                entry_count=regulation["entry_count"],
            )
            for identifier, regulation in by_identifier.items()
        )
        for document_id, by_identifier in collected.items()
    }


def _collect_unattributed_parts(db: Database) -> dict[int, tuple[str, ...]]:
    """Parts no declared regulation claims, keyed by document id.

    Restricted to sources that declare regulations: elsewhere an
    unattributed part is the normal state, not a gap.
    """
    sql = (
        "SELECT rp.document_id, rp.part_code FROM regulation_parts rp "
        "WHERE rp.regulation IS NULL "
        "  AND EXISTS (SELECT 1 FROM source_regulations sr "
        "              WHERE sr.document_id = rp.document_id) "
        "ORDER BY rp.document_id, rp.sort_order, rp.id"
    )
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    unattributed: dict[int, list[str]] = {}
    for row in rows:
        unattributed.setdefault(row["document_id"], []).append(row["part_code"])
    return {
        document_id: tuple(part_codes)
        for document_id, part_codes in unattributed.items()
    }


def _catalog_reading(row: dict, supplied: str | None) -> tuple[str | None, ...]:
    """What EASA advertises for one source: revision, its date, when read.

    A revision the caller supplies comes from a catalogue read now, so the
    dates stored against an earlier reading are not carried over with it:
    pairing a newly advertised label with the publication date of the one it
    supersedes would read as 'the catalogue is not newer' and grade a
    superseded copy current.  A caller that supplies nothing — or supplies
    what is already stored — leaves the stored reading as it is.
    """
    stored = row["catalog_revision"]
    if supplied is None or supplied == stored:
        return stored, row["catalog_published_at"], row["catalog_checked_at"]
    return supplied, None, None


def collect_provenance(
    db: Database, *, catalog_revisions: dict[str, str] | None = None,
) -> list[SourceProvenance]:
    """Read the provenance of every registered source out of the database.

    *catalog_revisions* is a catalogue read now, which overrides the reading
    stored against a source.  Without one the stored reading still stands —
    it is what EASA advertised when the artefact was retrieved, and dropping
    it would lose the only comparison a corpus qualified offline can make.
    """
    catalog_revisions = catalog_revisions or {}
    sql = (
        "SELECT sd.id, sd.slug, sd.source_family, sd.title, sd.status, "
        "       sd.page_url, sd.source_url, sd.revision, sd.published_at, "
        "       sd.catalog_revision, sd.catalog_published_at, "
        "       sd.catalog_checked_at, sd.parser_version, sd.parsed_at, "
        "       (SELECT COUNT(*) FROM regulation_entries re "
        "        WHERE re.document_id = sd.id) AS entry_count, "
        "       sf.checksum, sf.local_path, sf.download_url, sf.downloaded_at "
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

    regulations = _collect_regulations(db)
    unattributed = _collect_unattributed_parts(db)

    sources = []
    for row in rows:
        catalog_revision, catalog_published_at, catalog_checked_at = _catalog_reading(
            row, catalog_revisions.get(row["slug"]),
        )
        sources.append(SourceProvenance(
            slug=row["slug"],
            source_family=row["source_family"],
            title=row["title"],
            status=row["status"],
            page_url=row["page_url"],
            source_url=row["source_url"],
            revision=row["revision"],
            published_at=row["published_at"],
            catalog_revision=catalog_revision,
            catalog_published_at=catalog_published_at,
            catalog_checked_at=catalog_checked_at,
            parser_version=row["parser_version"],
            checksum=row["checksum"],
            local_path=row["local_path"],
            download_url=row["download_url"],
            retrieved_at=row["downloaded_at"],
            entry_count=row["entry_count"],
            parsed_at=row["parsed_at"],
            regulations=regulations.get(row["id"], ()),
            unattributed_parts=unattributed.get(row["id"], ()),
        ))
    return sources


def _provenance_gaps(source: SourceProvenance) -> list[str]:
    """Name every regulation and part the build cannot account for.

    A consolidated document carries more than one regulation: a build that
    lost one of them still holds entries, so only the per-regulation counts
    can tell that an annex went missing.  One note per gap, each naming the
    regulation or the part, so the diagnostics say *which* annex is missing
    rather than only that something is.
    """
    gaps = [
        f"source '{source.slug}' declares regulation "
        f"'{regulation.identifier}' but holds no entries from it"
        for regulation in source.regulations
        if not regulation.entry_count
    ]
    gaps += [
        f"source '{source.slug}' holds part '{part_code}', which no "
        f"declared regulation claims"
        for part_code in source.unattributed_parts
    ]
    return gaps


def grade_source(source: SourceProvenance) -> Freshness:
    """Grade one source on the ladder of ``claw_easa.freshness``.

    Completeness is settled first: a source that never reached a usable parse
    is ``incomplete`` whatever revision label it carries, because there is no
    held edition to compare — calling it current would answer for text the
    corpus does not hold.

    Everything else is the edition comparison itself, which is
    ``grade_freshness``'s to make and is not second-guessed here.  It returns
    ``current`` only where a comparison was actually possible, so a source
    with no revision on either side grades ``freshness-unknown`` rather than
    passing by default.
    """
    if source.entry_count == 0 or source.status in _UNPARSED_STATUSES:
        return Freshness(
            INCOMPLETE,
            _NO_ENTRIES,
            f"The source contributed no entries (status '{source.status}').",
        )
    return grade_freshness(
        revision=source.revision,
        published_at=source.published_at,
        catalog_revision=source.catalog_revision,
        catalog_published_at=source.catalog_published_at,
    )


def qualify(
    sources: list[SourceProvenance], *, expected_slugs: tuple[str, ...] = (),
) -> tuple[str, list[str]]:
    """Grade a set of sources, returning ``(status, notes)``.

    The build takes the worst grade among its sources, so ``qualified``
    requires every one of them to have been compared against what EASA
    publishes and found to match.  Two conditions are the build's own rather
    than any single source's: an expected source that is absent entirely, and
    a corpus that holds no entries at all.
    """
    notes: list[str] = []
    graded: list[str] = []

    present = {s.slug for s in sources}
    for slug in expected_slugs:
        if slug not in present:
            notes.append(f"expected source '{slug}' is not in the corpus")
            graded.append(INCOMPLETE)

    total_entries = sum(s.entry_count for s in sources)
    if not sources or total_entries == 0:
        notes.append("the corpus holds no entries")
        graded.append(FAILED)
        return _rolled_up(graded), notes

    for source in sources:
        freshness = grade_source(source)
        graded.append(freshness.status)

        if freshness.basis == _NO_ENTRIES:
            notes.append(
                f"source '{source.slug}' contributed no entries "
                f"(status '{source.status}')"
            )
        elif freshness.status == STALE:
            notes.append(
                f"source '{source.slug}' holds revision '{source.revision}', "
                f"superseded by the published revision "
                f"'{source.catalog_revision}'"
            )
        elif freshness.status == FRESHNESS_UNKNOWN:
            notes.append(
                f"source '{source.slug}' is not shown to be up to date — "
                f"{freshness.detail}"
            )

        # Reported whatever the source was graded: one held short of a clean
        # parse *because* an annex went missing or arrived unclaimed still
        # knows which one, and an otherwise current source that cannot
        # account for an annex is not a complete build either.
        gaps = _provenance_gaps(source)
        if gaps:
            notes.extend(gaps)
            graded.append(INCOMPLETE)

    return _rolled_up(graded), notes


def _rolled_up(graded: list[str]) -> str:
    """The worst of *graded*, named as the manifest names it."""
    status = rollup_status(graded)
    return _MANIFEST_STATUS.get(status, status)


def _with_freshness(source: SourceProvenance) -> SourceProvenance:
    """*source* carrying the grade ``qualify`` rolls up, so it survives export."""
    freshness = grade_source(source)
    return replace(
        source,
        freshness=freshness.status,
        freshness_basis=freshness.basis,
        freshness_detail=freshness.detail,
    )


def build_manifest(
    db: Database,
    *,
    catalog_revisions: dict[str, str] | None = None,
    expected_slugs: tuple[str, ...] = (),
) -> BuildManifest:
    """Qualify the corpus as it currently stands.

    *catalog_revisions* maps a slug to the revision EASA publishes today.
    Without it nothing can be compared, so the build is graded
    ``freshness-unknown``: neither staleness nor currency is claimed.
    """
    sources = [
        _with_freshness(source)
        for source in collect_provenance(db, catalog_revisions=catalog_revisions)
    ]
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

    The status is translated to the vocabulary the history records; the
    manifest itself is returned and stored as JSON unchanged, so what was
    graded stays legible next to what was recorded.
    """
    payload = manifest.to_dict()
    recorded_status = _RECORDED_STATUS.get(manifest.status, manifest.status)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus_builds "
                "(build_id, status, tool_version, schema_version, "
                " source_count, entry_count, notes, manifest_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    manifest.build_id,
                    recorded_status,
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
                    "(build_db_id, slug, source_family, title, status, "
                    " freshness, freshness_basis, freshness_detail, "
                    " page_url, source_url, revision, published_at, "
                    " catalog_revision, catalog_published_at, catalog_checked_at, "
                    " parser_version, checksum, local_path, download_url, "
                    " retrieved_at, entry_count, parsed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "        ?, ?, ?, ?, ?)",
                    (
                        build_db_id, source.slug, source.source_family,
                        source.title, source.status,
                        source.freshness, source.freshness_basis,
                        source.freshness_detail,
                        source.page_url, source.source_url,
                        source.revision, source.published_at,
                        source.catalog_revision, source.catalog_published_at,
                        source.catalog_checked_at, source.parser_version,
                        source.checksum, source.local_path, source.download_url,
                        source.retrieved_at, source.entry_count, source.parsed_at,
                    ),
                )
        conn.commit()

    log.info("Recorded corpus build %s (%s)", manifest.build_id, manifest.status)
    return manifest


def _source_from_payload(payload: dict) -> SourceProvenance:
    """Rebuild a source from recorded JSON.

    Builds recorded before regulation provenance existed carry neither key,
    and the empty defaults are the truthful reading of those: nothing was
    declared, so nothing went unattributed.
    """
    fields = dict(payload)
    fields["regulations"] = tuple(
        RegulationProvenance(
            identifier=r["identifier"],
            title=r["title"],
            kind=r["kind"],
            part_codes=tuple(r.get("part_codes", ())),
            entry_count=r.get("entry_count", 0),
        )
        for r in fields.pop("regulations", ()) or ()
    )
    fields["unattributed_parts"] = tuple(fields.pop("unattributed_parts", ()) or ())
    return SourceProvenance(**fields)


def _row_to_manifest(row: dict | None) -> BuildManifest | None:
    if row is None:
        return None
    payload = json.loads(row["manifest_json"])
    return BuildManifest(
        build_id=payload["build_id"],
        status=payload["status"],
        sources=[_source_from_payload(s) for s in payload.get("sources", [])],
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
    """The most recent qualified build, ignoring anything recorded after it.

    Matches on the status the history records rather than the one the
    manifest carries, and only on that one: a build migrated from an older
    database reads as 'freshness-unknown' and is deliberately not offered
    here, because nothing established that it was up to date.  Such a
    database still knows which corpus it holds — the migration points the
    'current' role slot at it.
    """
    return _row_to_manifest(
        db.fetch_one(
            "SELECT * FROM corpus_builds WHERE status = ? ORDER BY id DESC LIMIT 1",
            (_RECORDED_STATUS[QUALIFIED],),
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
