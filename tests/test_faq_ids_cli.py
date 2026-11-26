"""S1 — `claw-easa db faq-ids {plan|apply|rollback|verify} --data-dir DIR`."""

import hashlib
import re
import sqlite3

import pytest
from click.testing import CliRunner

import claw_easa.cli as cli
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


def test_plan_prints_faq_table_presence(data_dir):
    result = _run("plan", "--data-dir", str(data_dir))
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    start = lines.index("already_applied: no")
    assert lines[start + 1:start + 3] == ["faq_tables_present: 3", "faq_tables_rows: 0"]


def test_plan_and_apply_refuse_non_empty_faq_tables(data_dir, tmp_path):
    conn = sqlite3.connect(data_dir / "claw_easa.db")
    conn.execute("INSERT INTO faq_aliases (alias_ref, entry_id) VALUES ('FAQ-stale-001', "
                 "(SELECT MIN(id) FROM regulation_entries WHERE entry_type = 'FAQ'))")
    conn.commit()
    conn.close()
    result = _run("plan", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "faq_tables_rows: 1" in result.output.splitlines()
    assert "precheck_failed: faq_tables_not_empty" in result.output.splitlines()
    applied = _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    assert applied.exit_code == 1
    assert "faq_tables_not_empty" in applied.output
    assert not _applied(data_dir)


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


def _lines(result):
    return dict(line.split(": ", 1) for line in result.output.splitlines() if ": " in line)


def test_backup_command(data_dir, tmp_path):
    backup = tmp_path / "b.db"
    result = _run("backup", "--data-dir", str(data_dir), "--backup-to", str(backup))
    assert result.exit_code == 0, result.output
    assert all(COUNT_LINE.match(line) for line in result.output.splitlines()), result.output
    out = _lines(result)
    assert out["backup"] == str(backup)
    assert out["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert (out["integrity_check"], out["faq_rows"], out["applied"]) == ("ok", "14", "no")
    live = _lines(_run("verify", "--data-dir", str(data_dir)))
    for key in ("immutable_fingerprint", "restorable_fingerprint"):
        assert out[key] == live[key]
    again = _run("backup", "--data-dir", str(data_dir), "--backup-to", str(backup))
    assert again.exit_code == 1
    assert out["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()


def test_backup_command_fails_on_bad_integrity(data_dir, tmp_path, monkeypatch):
    from claw_easa.db import faq_migration

    real = faq_migration.snapshot
    monkeypatch.setattr(
        faq_migration, "snapshot", lambda db: {**real(db), "integrity_check": "corrupt"}
    )
    result = _run("backup", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    assert result.exit_code == 1
    assert "integrity_check: corrupt" in result.output


def test_apply_when_applied_takes_no_backup(data_dir, tmp_path):
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    again = _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b2.db"))
    assert again.exit_code == 0, again.output
    assert _lines(again) == {"backup": "none", "already_applied": "yes", "applied": "yes"}
    assert not (tmp_path / "b2.db").exists()


def test_verify_new_fields(data_dir, tmp_path):
    before = _lines(_run("verify", "--data-dir", str(data_dir), "--ref-prefix", "ORO.FTL"))
    assert (before["faq_groups"], before["faq_aliases"], before["faq_entries_ungrouped"]) == (
        "0", "0", "14",
    )
    assert before["aliases_unresolved"] == "0" and before["faiss_sha256"] == "absent"
    assert (before["ref_prefix_faq_rows"], before["ref_prefix_groups"]) == ("2", "2")
    (data_dir / "claw_easa.faiss").write_bytes(b"vec")
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    result = _run("verify", "--data-dir", str(data_dir), "--ref-prefix", "ORO.FTL")
    assert result.exit_code == 0, result.output
    assert all(COUNT_LINE.match(line) for line in result.output.splitlines()), result.output
    after = _lines(result)
    assert (after["faq_groups"], after["faq_aliases"], after["faq_entries_ungrouped"]) == (
        "10", "14", "0",
    )
    assert after["faiss_sha256"] == hashlib.sha256(b"vec").hexdigest()
    assert (after["ref_prefix_faq_rows"], after["ref_prefix_groups"]) == ("2", "1")


def test_verify_fails_on_ungrouped_or_unresolved_when_applied(data_dir, tmp_path):
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b.db"))
    conn = sqlite3.connect(data_dir / "claw_easa.db")
    conn.execute("DELETE FROM faq_entry_groups WHERE entry_id = (SELECT MIN(entry_id) "
                 "FROM faq_entry_groups)")
    conn.commit()
    conn.close()
    result = _run("verify", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "faq_entries_ungrouped: 1" in result.output


def test_verify_against_backup(data_dir, tmp_path):
    backup = tmp_path / "b.db"
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(backup))
    applied = _run("verify", "--data-dir", str(data_dir), "--against", str(backup))
    assert applied.exit_code == 0, applied.output
    assert _lines(applied)["against_immutable_match"] == "yes"
    assert _lines(applied)["against_restorable_match"] == "n/a"
    _run("rollback", "--data-dir", str(data_dir))
    restored = _run("verify", "--data-dir", str(data_dir), "--against", str(backup))
    assert restored.exit_code == 0, restored.output
    assert _lines(restored)["against_restorable_match"] == "yes"
    conn = sqlite3.connect(data_dir / "claw_easa.db")
    conn.execute("UPDATE regulation_entries SET body_text = 'altered' "
                 "WHERE entry_ref = 'FAQ-beta-002'")
    conn.commit()
    conn.close()
    altered = _run("verify", "--data-dir", str(data_dir), "--against", str(backup))
    assert altered.exit_code == 1
    assert _lines(altered)["against_immutable_match"] == "no"
    assert "altered" not in altered.output


def test_rollback_against_backup(data_dir, tmp_path):
    backup = tmp_path / "b.db"
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(backup))
    ok = _run("rollback", "--data-dir", str(data_dir), "--against", str(backup))
    assert ok.exit_code == 0, ok.output
    assert "against_restorable_match: yes" in ok.output
    other = tmp_path / "other.db"
    _run("backup", "--data-dir", str(data_dir), "--backup-to", str(other))
    _run("apply", "--data-dir", str(data_dir), "--backup-to", str(tmp_path / "b2.db"))
    conn = sqlite3.connect(other)
    conn.execute("UPDATE regulation_entries SET entry_ref = 'FAQ-beta-099' "
                 "WHERE entry_ref = 'FAQ-beta-002'")
    conn.commit()
    conn.close()
    bad = _run("rollback", "--data-dir", str(data_dir), "--against", str(other))
    assert bad.exit_code == 1
    assert "committed" in bad.output
    assert not _applied(data_dir)


@pytest.fixture
def default_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    path = tmp_path / "fakehome" / ".local" / "share" / "claw-easa"
    db = open_db(path)
    legacy_corpus(db)
    db.close()
    return path


def test_default_dir_write_refused_without_tty(default_dir, tmp_path):
    db_file = default_dir / "claw_easa.db"
    before = db_file.read_bytes()
    result = _run("apply", "--data-dir", str(default_dir), "--allow-default-data-dir",
                  "--backup-to", str(tmp_path / "b.db"))
    assert result.exit_code == 1
    assert "confirmation must be typed on a terminal" in result.output
    assert db_file.read_bytes() == before and not (tmp_path / "b.db").exists()
    assert not _applied(default_dir)


def test_default_dir_wrong_answer_refused(default_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    result = CliRunner().invoke(
        main, ["db", "faq-ids", "apply", "--data-dir", str(default_dir),
               "--allow-default-data-dir", "--backup-to", str(tmp_path / "b.db")],
        input="/somewhere/else.db\n",
    )
    assert result.exit_code == 1
    assert not (tmp_path / "b.db").exists() and not _applied(default_dir)


def test_default_dir_right_answer_proceeds(default_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    db_path = (default_dir / "claw_easa.db").resolve()
    result = CliRunner().invoke(
        main, ["db", "faq-ids", "apply", "--data-dir", str(default_dir),
               "--allow-default-data-dir", "--backup-to", str(tmp_path / "b.db")],
        input=f"{db_path}\n",
    )
    assert result.exit_code == 0, result.output
    assert "Type the full database path to confirm:" in result.output
    assert _applied(default_dir)
