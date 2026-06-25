"""Tests for ingesting manually-downloaded local files.

When the automatic fetcher is blocked (EASA bot-challenge), users can
download a document by hand and ingest it with ``parse --file``, which
goes through ``import_local_source``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import reset_settings
from claw_easa.ingest.repository import (
    get_document_by_slug,
    get_latest_source_file,
)
from claw_easa.ingest.service import _open_db, import_local_source


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAW_EASA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAW_EASA_DB_FILE", "test.db")
    reset_settings()
    yield tmp_path
    reset_settings()


def test_import_creates_document_and_copies_file(tmp_env):
    src = tmp_env / "downloaded.zip"
    src.write_bytes(b"PK\x03\x04 pretend archive")

    result = import_local_source("air-ops", src)

    # File copied into the managed downloads directory.
    dest = tmp_env / "downloads" / "air-ops" / "downloaded.zip"
    assert dest.exists()
    assert Path(result["local_path"]) == dest

    db = _open_db()
    try:
        doc = get_document_by_slug(db, "air-ops")
        assert doc is not None
        assert doc["source_family"] == "ear"  # from the known alias

        latest = get_latest_source_file(db, doc["id"])
        assert latest["local_path"] == str(dest)
        assert latest["checksum"]  # checksum recorded
        assert latest["download_url"].startswith("file://")
    finally:
        db.close()


def test_import_missing_file_raises(tmp_env):
    with pytest.raises(FileNotFoundError):
        import_local_source("air-ops", tmp_env / "does-not-exist.zip")


def test_import_reuses_existing_document_title(tmp_env):
    from claw_easa.ingest.repository import upsert_source_document_from_values

    db = _open_db()
    try:
        upsert_source_document_from_values(
            db, slug="air-ops", source_family="ear",
            title="Easy Access Rules for Air Operations",
        )
    finally:
        db.close()

    src = tmp_env / "manual.zip"
    src.write_bytes(b"PK\x03\x04")
    import_local_source("air-ops", src)

    db = _open_db()
    try:
        doc = get_document_by_slug(db, "air-ops")
        assert doc["title"] == "Easy Access Rules for Air Operations"
    finally:
        db.close()
