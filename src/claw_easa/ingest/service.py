from __future__ import annotations

import logging
from pathlib import Path
import zipfile

from claw_easa.config import get_settings
from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.normalize import CanonicalPersister
from claw_easa.ingest.parser import EASAOfficeXMLParser
from claw_easa.ingest.repository import (
    get_document_by_slug,
    get_latest_source_file,
    record_download,
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
    scraper discovers the current page URL from the EASA website.
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
    )


def fetch_source(slug: str, *, url: str | None = None) -> dict:
    """Fetch an EASA source document.

    The page URL is resolved dynamically from the EASA catalog unless
    *url* is given explicitly.
    """
    from claw_easa.ingest.scraper import EASASourceFetcher

    source = _resolve_source(slug, url=url)
    settings = get_settings()
    data_dir = Path(settings.data_dir)

    db = _open_db()
    try:
        doc_id = upsert_source_document_from_values(
            db,
            slug=source.slug,
            source_family=source.source_family,
            title=source.title,
            language=source.language,
            page_url=source.page_url,
            source_url=source.source_url,
        )

        fetcher = EASASourceFetcher()
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


def parse_source(slug: str) -> dict:
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
        }
    finally:
        db.close()
