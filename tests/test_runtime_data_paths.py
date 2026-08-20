"""Behaviour 1 — the runtime data/database path must not depend on cwd.

``Settings.data_dir`` defaults to the relative string ``"data"``, so
``db_path``/``faiss_index_path`` resolve against whatever directory the
process happens to be started from.  Running ``claw-easa`` from ``~`` and
from the repository root therefore reads and writes two different SQLite
databases, which silently looks like an empty corpus.

These tests pin the contract: the resolved runtime paths are the same no
matter which directory the process runs in.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_easa.config import get_settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest.repository import (
    get_document_by_slug,
    upsert_source_document_from_values,
)

ENV_OVERRIDES = (
    "CLAW_EASA_DATA_DIR",
    "CLAW_EASA_DB_FILE",
    "CLAW_EASA_FAISS_INDEX_FILE",
)


@pytest.fixture
def workdirs(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Two working directories, default configuration only.

    No ``CLAW_EASA_*`` overrides and no user ``config.yaml``, so the tests
    exercise the shipped defaults.
    """
    for var in ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    first = tmp_path / "workdir-one"
    second = tmp_path / "workdir-two"
    first.mkdir()
    second.mkdir()

    reset_settings()
    yield first, second
    reset_settings()


def _resolved_paths_in(directory: Path, monkeypatch) -> tuple[str, str]:
    """Resolve the runtime paths from *directory*, as a fresh process would."""
    monkeypatch.chdir(directory)
    reset_settings()
    settings = get_settings()
    return (
        str(settings.db_path.resolve()),
        str(settings.faiss_index_path.resolve()),
    )


def test_db_path_is_absolute(workdirs, monkeypatch):
    first, _ = workdirs
    monkeypatch.chdir(first)

    settings = get_settings()

    assert settings.db_path.is_absolute(), (
        f"db_path {settings.db_path} is relative, so it is interpreted "
        f"against the current working directory"
    )


def test_faiss_index_path_is_absolute(workdirs, monkeypatch):
    first, _ = workdirs
    monkeypatch.chdir(first)

    settings = get_settings()

    assert settings.faiss_index_path.is_absolute(), (
        f"faiss_index_path {settings.faiss_index_path} is relative, so it is "
        f"interpreted against the current working directory"
    )


def test_db_path_is_identical_from_two_working_directories(workdirs, monkeypatch):
    first, second = workdirs

    db_from_first, _ = _resolved_paths_in(first, monkeypatch)
    db_from_second, _ = _resolved_paths_in(second, monkeypatch)

    assert db_from_first == db_from_second, (
        "the database location changes with the working directory"
    )


def test_faiss_index_path_is_identical_from_two_working_directories(workdirs, monkeypatch):
    first, second = workdirs

    _, faiss_from_first = _resolved_paths_in(first, monkeypatch)
    _, faiss_from_second = _resolved_paths_in(second, monkeypatch)

    assert faiss_from_first == faiss_from_second, (
        "the FAISS index location changes with the working directory"
    )


def _open_db() -> Database:
    """Same sequence as ``claw_easa.ingest.service._open_db``."""
    db = Database()
    db.open()
    MigrationRunner(db).init_schema()
    return db


def test_ingested_document_is_visible_from_another_working_directory(workdirs, monkeypatch):
    """The end-to-end symptom: a corpus ingested from one cwd disappears."""
    first, second = workdirs

    monkeypatch.chdir(first)
    reset_settings()
    db = _open_db()
    try:
        upsert_source_document_from_values(
            db,
            slug="air-ops",
            source_family="ear",
            title="Easy Access Rules for Air Operations",
        )
    finally:
        db.close()

    monkeypatch.chdir(second)
    reset_settings()
    db = _open_db()
    try:
        doc = get_document_by_slug(db, "air-ops")
    finally:
        db.close()

    assert doc is not None, (
        "the air-ops document ingested from one directory is not visible "
        "when the CLI runs from another directory"
    )
