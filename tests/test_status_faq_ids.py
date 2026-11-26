"""`claw-easa status` reports the 005_faq_stable_ids migration state, read-only."""

import hashlib
import sqlite3

import pytest
from click.testing import CliRunner

from claw_easa.cli import main
from claw_easa.config import reset_settings
from claw_easa.db.faq_migration import up
from tests.faq_fixtures import legacy_corpus, make_legacy_shaped, open_db

DOC = "docs/upgrading-the-database.md"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAW_EASA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLAW_EASA_DB_FILE", raising=False)
    reset_settings()
    yield tmp_path
    reset_settings()


def _build(path, *steps):
    db = open_db(path)
    for step in steps:
        step(db)
    db.close()
    reset_settings()


def _status():
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    return result.output


def _faq_line(output):
    lines = [line for line in output.splitlines() if line.startswith("FAQ ids:")]
    assert len(lines) == 1, output
    return lines[0]


def test_unmigrated_corpus_points_to_plan_and_upgrade_doc(data_dir):
    _build(data_dir, legacy_corpus)
    line = _faq_line(_status())
    assert line.startswith("FAQ ids:         NOT migrated")
    assert f"claw-easa db faq-ids plan --data-dir {data_dir}" in line
    assert DOC in line


def test_migrated_corpus(data_dir):
    _build(data_dir, legacy_corpus, up)
    assert _faq_line(_status()) == "FAQ ids:         migrated (005)"


def test_no_faq_rows_prints_no_faq_line(data_dir):
    _build(data_dir)
    output = _status()
    assert "Database:" in output
    assert "schema not initialised" not in output
    assert "FAQ ids:" not in output


def test_missing_database_prints_no_faq_line(data_dir):
    output = _status()
    assert "database not created" in output
    assert "FAQ ids:" not in output


def _tables(path):
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master"))
    finally:
        conn.close()


def test_status_is_read_only(data_dir):
    _build(data_dir, legacy_corpus, make_legacy_shaped)
    db_file = data_dir / "claw_easa.db"
    tables, digest = _tables(db_file), hashlib.sha256(db_file.read_bytes()).hexdigest()
    assert "faq_aliases" not in tables

    assert "NOT migrated" in _faq_line(_status())

    assert _tables(db_file) == tables
    assert hashlib.sha256(db_file.read_bytes()).hexdigest() == digest
