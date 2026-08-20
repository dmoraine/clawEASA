from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
import zipfile

from claw_easa.config import get_settings
from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.freshness import parse_published_date
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser
from claw_easa.ingest.regulations import regulations_for
from claw_easa.ingest.repository import (
    get_document_by_slug,
    get_latest_source_file,
    record_download,
    record_source_regulations,
    upsert_source_document_from_values,
)
from claw_easa.ingest.sources import SourceSpec, get_alias

log = logging.getLogger(__name__)


def _open_db() -> Database:
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    return db


def _resolve_source(slug: str, *, url: str | None = None) -> SourceSpec:
    """Build a SourceSpec by resolving the slug against the EASA catalog.

    If *url* is provided it is used directly; otherwise the catalog
    scraper discovers the current page URL from the EASA website — and, with
    it, the revision EASA advertises and when that was read.  An explicit
    *url* bypasses the catalogue, so it carries no revision: nothing observed
    which edition it serves.
    """
    alias = get_alias(slug)
    source_family = alias.source_family if alias else "ear"
    language = alias.language if alias else "en"

    if url:
        return SourceSpec(
            slug=slug,
            source_family=source_family,
            title=slug,
            language=language,
            source_url=url,
        )

    from claw_easa.ingest.catalog import EasyAccessRulesCatalogScraper

    catalog = EasyAccessRulesCatalogScraper()
    entry = catalog.resolve(slug)

    effective_slug = alias.slug if alias else slug
    return SourceSpec(
        slug=effective_slug,
        source_family=source_family,
        title=entry.title,
        language=language,
        page_url=entry.page_url,
        revision=entry.revision,
        published_at=entry.published_at,
        checked_at=entry.checked_at,
    )


def fetch_source(slug: str, *, url: str | None = None, use_browser: bool = False) -> dict:
    """Fetch an EASA source document.

    The page URL is resolved dynamically from the EASA catalog unless
    *url* is given explicitly.  When *use_browser* is true, the optional
    headless-browser fallback is used in case EASA conditionally serves its
    JavaScript bot-challenge (requires ``playwright``).
    """
    source = _resolve_source(slug, url=url)
    settings = get_settings()
    data_dir = settings.data_path

    if use_browser:
        from claw_easa.ingest.scraper_browser import BrowserSourceFetcher
        fetcher = BrowserSourceFetcher()
    else:
        from claw_easa.ingest.scraper import EASASourceFetcher
        fetcher = EASASourceFetcher()

    db = _open_db()
    try:
        # The artefact behind the page the catalogue advertises *is* the
        # revision the catalogue advertises, so one reading is recorded twice:
        # as the edition held, and as the catalogue reading it was taken from.
        # They start out equal — the copy is current by construction — and a
        # later catalogue read that advertises something else is what makes
        # the held copy stale.  A published date the card did not corroborate
        # falls back to the month the revision label itself names.
        published_at = source.published_at or parse_published_date(source.revision)
        doc_id = upsert_source_document_from_values(
            db,
            slug=source.slug,
            source_family=source.source_family,
            title=source.title,
            language=source.language,
            page_url=source.page_url or None,
            source_url=source.source_url,
            revision=source.revision,
            published_at=published_at,
            catalog_revision=source.revision,
            catalog_published_at=published_at,
            catalog_checked_at=source.checked_at,
        )

        downloaded = fetcher.fetch(source, data_dir)

        file_id = record_download(
            db,
            document_id=doc_id,
            checksum=downloaded.checksum,
            local_path=str(downloaded.local_path),
            download_url=downloaded.download_url,
        )

        return {
            "document_id": doc_id,
            "file_id": file_id,
            "local_path": str(downloaded.local_path),
        }
    finally:
        db.close()


def _materialize_parse_path(path: Path) -> Path:
    """Extract the main XML document from a ZIP archive.

    EASA distributes Easy Access Rules as ZIP archives containing a flat
    Office Open XML file.  Some archives also include OPC metadata files
    like ``[Content_Types].xml`` — these are filtered out automatically.
    When multiple candidate XML files remain, the largest is selected
    (it is almost certainly the regulation document).
    """
    if path.suffix.lower() != '.zip':
        return path

    with zipfile.ZipFile(path) as zf:
        candidates: list[tuple[str, int]] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name_lower = info.filename.lower()
            if not name_lower.endswith('.xml'):
                continue
            basename = Path(info.filename).name
            if basename.startswith('[') or basename.startswith('_'):
                continue
            candidates.append((info.filename, info.file_size))

        if not candidates:
            raise ValueError(f"No XML document found inside archive {path}")

        xml_name = max(candidates, key=lambda c: c[1])[0]
        log.info("Extracting %s from %s", xml_name, path.name)

        out_path = path.with_suffix('.xml')
        zf.extract(xml_name, path.parent)
        extracted = path.parent / xml_name
        if extracted != out_path:
            extracted.replace(out_path)
        return out_path


def import_local_source(slug: str, file_path: str | Path) -> dict:
    """Register a manually-downloaded file as a source for *slug*.

    Use this when the automatic fetcher is unavailable or conditionally
    challenged: download the document by hand from the EASA document library,
    then point this at the local file.  The file is copied into the managed
    downloads directory and recorded as the latest source file, ready for
    ``parse_source``.
    """
    src = Path(file_path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    settings = get_settings()
    target_dir = settings.data_path / "downloads" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / src.name

    db = _open_db()
    try:
        existing = get_document_by_slug(db, slug)
        alias = get_alias(slug)
        source_family = (
            existing["source_family"] if existing
            else alias.source_family if alias else "ear"
        )
        title = existing["title"] if existing else slug
        language = existing["language"] if existing else (
            alias.language if alias else "en"
        )

        doc_id = upsert_source_document_from_values(
            db,
            slug=slug,
            source_family=source_family,
            title=title,
            language=language,
        )

        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

        hasher = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)

        file_id = record_download(
            db,
            document_id=doc_id,
            checksum=hasher.hexdigest(),
            local_path=str(dest),
            download_url=f"file://{src.resolve()}",
        )

        return {
            "document_id": doc_id,
            "file_id": file_id,
            "local_path": str(dest),
        }
    finally:
        db.close()


def parse_source(slug: str, *, file: str | Path | None = None) -> dict:
    if file is not None:
        import_local_source(slug, file)

    db = _open_db()
    try:
        doc = get_document_by_slug(db, slug)
        if not doc:
            raise ValueError(f"Document not found: {slug}")

        source_file = get_latest_source_file(db, doc["id"])
        if not source_file:
            raise ValueError(f"No source file for: {slug}")

        path = Path(source_file["local_path"])
        if not path.exists():
            raise FileNotFoundError(f"Source file missing: {path}")

        parse_path = _materialize_parse_path(path)

        parser = EASAOfficeXMLParser()
        parsed = parser.parse_file(parse_path, doc["title"])

        # Re-declare the source's regulations from the current declaration
        # before persisting, so each part is attributed to the regulation
        # that states it.  Declared against the slug the resolved document is
        # filed under, not the caller's argument, so the provenance follows
        # the document being parsed.  A slug that declares none records none,
        # which is how a source without regulation provenance is expressed.
        record_source_regulations(db, doc["id"], regulations_for(doc["slug"]))

        persister = CanonicalPersister(db)
        summary = persister.persist_document(doc["id"], parsed)

        return {
            "document_id": doc["id"],
            "parts": summary.parts,
            "subparts": summary.subparts,
            "sections": summary.sections,
            "entries": summary.entries,
            "duplicate_entries_skipped": summary.duplicate_entries_skipped,
            "empty_entries_skipped": summary.empty_entries_skipped,
            "unattributed_parts": list(summary.unattributed_parts),
        }
    finally:
        db.close()
