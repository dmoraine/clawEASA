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
from claw_easa.ingest.sources import get_source

log = logging.getLogger(__name__)


def _open_db() -> Database:
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    return db


def fetch_source(slug: str) -> dict:
    from claw_easa.ingest.scraper import EASASourceFetcher

    source = get_source(slug)
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
    if path.suffix.lower() != '.zip':
        return path

    with zipfile.ZipFile(path) as zf:
        names = [name for name in zf.namelist() if not name.endswith('/')]
        xml_names = [name for name in names if name.lower().endswith('.xml')]
        if len(xml_names) != 1:
            raise ValueError(f"Expected exactly one XML file in archive {path}, found {len(xml_names)}")
        xml_name = xml_names[0]
        out_path = path.with_suffix('')
        if out_path.suffix.lower() != '.xml':
            out_path = out_path.with_suffix('.xml')
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
