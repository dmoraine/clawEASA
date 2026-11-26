"""S1 — `claw-easa db faq-ids {plan|apply|rollback|verify} --data-dir DIR`."""

import re
import sqlite3

import pytest
from click.testing import CliRunner

from claw_easa.cli import main
from tests.faq_fixtures import SAME_TITLE, SHARED, add_domain, add_entry, legacy_corpus, open_db

COUNT_LINE = re.compile(r"^[a-z0-9_]+: \S+$")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = tmp_path / "data"
    db = open_db(path)
    legacy_corpus(db)
    db.close()
    return path


def _run(*args):
    return CliRunner().invoke(main, ["db", "faq-ids", *args])


def _applied(path):
    conn = sqlite3.connect(path / "claw_easa.db")
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = '005_faq_stable_ids'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_plan_prints_counts_only_and_writes_nothing(data_dir):
    db_file = data_dir / "claw_easa.db"
    before = (db_file.read_bytes(), db_file.stat().st_mtime_ns)
    result = _run("plan", "--data-dir", str(data_dir))
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines and all(COUNT_LINE.match(line) for line in lines), result.output
    assert "faq_rows: 14" in lines and "would_apply: yes" in lines
    assert SHARED[0] not in result.output and SAME_TITLE not in result.output
    assert (db_file.read_bytes(), db_file.stat().st_mtime_ns) == before


def test_plan_fails_and_names_the_precheck(data_dir):
    db = open_db(data_dir)
    add_entry(db, add_domain(db, "faq-odd"), "FAQ-odd-1", "Odd question here?", "Odd answer.")
    db.close()
    result = _run("plan", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "legacy_pattern_mismatch" in result.output


def test_data_dir_is_mandatory():
    assert _run("plan").exit_code == 2


def test_missing_database_is_refused(tmp_path):
    result = _run("plan", "--data-dir", str(tmp_path / "nowhere"))
    assert result.exit_code == 1
    assert not (tmp_path / "nowhere").exists()


def test_default_data_dir_refused_unless_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    default = tmp_path / ".local" / "share" / "claw-easa"
    open_db(default).close()
    refused = _run("plan", "--data-dir", str(default))
    assert refused.exit_code == 1
    assert "--allow-default-data-dir" in refused.output
    assert _run("plan", "--data-dir", str(default), "--allow-default-data-dir").exit_code == 0


def test_apply_requires_backup(data_dir):
    assert _run("apply", "--data-dir", str(data_dir)).exit_code == 2
    assert not _applied(data_dir)


def test_apply_refuses_existing_backup(data_dir, tmp_path):
    backup = tmp_path / "backup.db"
    backup.write_bytes(b"keep")
    result = _run("apply", "--data-dir", str(data_dir), "--backup-to", str(backup))
    assert result.exit_code == 1
    assert backup.read_bytes() == b"keep"
    assert not _applied(data_dir)


def test_apply_backs_up_then_migrates_idempotently(data_dir, tmp_path):
    backup = tmp_path / "backup.db"
    result = _run("apply", "--data-dir", str(data_dir), "--backup-to", str(backup))
    assert result.exit_code == 0, result.output
    assert _applied(data_dir)
    copy = sqlite3.connect(backup)
    try:
        assert copy.execute(
            "SELECT entry_ref FROM regulation_entries WHERE entry_ref = 'FAQ-grand-001'"
        ).fetchone()
    finally:
        copy.close()
    again = _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b2.db"))
    assert again.exit_code == 0
    assert "already_applied: yes" in again.output


def test_verify_and_rollback(data_dir, tmp_path):
    before = _run("verify", "--data-dir", str(data_dir))
    assert before.exit_code == 0, before.output
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    rolled = _run("rollback", "--data-dir", str(data_dir))
    assert rolled.exit_code == 0, rolled.output
    assert not _applied(data_dir)
    assert _run("verify", "--data-dir", str(data_dir)).output == before.output


def test_rollback_refuses_post_migration_rows_unless_forced(data_dir, tmp_path):
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    db = open_db(data_dir)
    add_entry(db, add_domain(db, "faq-late"), "FAQ-late-0123abcd", "Late question?", "Late.")
    db.close()
    refused = _run("rollback", "--data-dir", str(data_dir))
    assert refused.exit_code == 1
    assert "1 FAQ row" in refused.output
    assert _applied(data_dir)
    assert _run("rollback", "--data-dir", str(data_dir), "--force").exit_code == 0
    assert not _applied(data_dir)
