"""S1 — FAQ ingestion keyed by content-based stable ids (HTTP stubbed)."""

from types import SimpleNamespace

import pytest

from claw_easa.config import reset_settings
from claw_easa.db.faq_migration import MigrationError, is_applied
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database
from claw_easa.ingest import http
from claw_easa.ingest.faq_identity import StableIdCollision, content_hash
from claw_easa.ingest.faq_service import ingest_faq_domain
from claw_easa.ingest.faq_sources import make_faq_domain
from tests.faq_fixtures import legacy_corpus

A = ("What does question alpha ask?", "Answer alpha explains the rule.")
B = ("What does question bravo ask?", "Answer bravo explains the rule.")
C = ("What does question charlie ask?", "Answer charlie explains the rule.")
N = ("What does the new question ask?", "The new answer explains the rule.")
B_EDITED = (B[0], "Answer bravo explains the amended rule.")

DEMO = make_faq_domain("demo", "Demo", "https://example.test/faqs/demo")
OTHER = make_faq_domain("other", "Other", "https://example.test/faqs/other")


def _page(*items):
    body = "".join(
        f'<div class="faq-child"><h4>{q}</h4><div class="body"><p>{a}</p></div></div>'
        for q, a in items
    )
    return f"<html><body>{body}</body></html>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAW_EASA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLAW_EASA_DB_FILE", raising=False)
    reset_settings()
    pages: dict[str, str] = {}
    monkeypatch.setattr(http, "get", lambda url, **kw: SimpleNamespace(text=pages[url]))
    db = Database()
    db.open()
    MigrationRunner(db).init_schema()
    yield db, pages
    db.close()
    reset_settings()


def _ingest(pages, domain, *items):
    pages[domain.url] = _page(*items)
    return ingest_faq_domain(domain)


def _rows(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, entry_ref, title FROM regulation_entries "
                "WHERE entry_type = 'FAQ' ORDER BY id"
            )
            return cur.fetchall()


def _ids_by_title(db):
    return {r["title"]: (r["id"], r["entry_ref"]) for r in _rows(db)}


def _sid(item, prefix="FAQ-demo"):
    return f"{prefix}-{content_hash(*item)[:8]}"


def test_new_faq_gets_stable_id(env):
    db, pages = env
    _ingest(pages, DEMO, A, B)
    assert [r["entry_ref"] for r in _rows(db)] == [_sid(A), _sid(B)]


def test_first_ingestion_on_empty_db_records_the_migration(env):
    db, pages = env
    assert not is_applied(db)
    _ingest(pages, DEMO, A)
    assert is_applied(db)
    assert db.fetch_one("SELECT COUNT(*) AS n FROM faq_aliases")["n"] == 0


def test_reingest_same_page_adds_nothing(env):
    db, pages = env
    _ingest(pages, DEMO, A, B, C)
    before = _rows(db)
    _ingest(pages, DEMO, A, B, C)
    assert _rows(db) == before


def test_insert_and_remove_keep_other_ids(env):
    db, pages = env
    _ingest(pages, DEMO, A, B, C)
    before = _ids_by_title(db)
    _ingest(pages, DEMO, N, A, C)
    after = _ids_by_title(db)
    assert after[A[0]] == before[A[0]]
    assert after[C[0]] == before[C[0]]
    assert after[B[0]] == before[B[0]]  # removed from the page, row kept
    assert after[N[0]][1] == _sid(N)
    assert len(after) == 4


def test_edited_faq_gets_new_row_old_kept(env):
    db, pages = env
    _ingest(pages, DEMO, A, B)
    _ingest(pages, DEMO, A, B_EDITED)
    refs = [r["entry_ref"] for r in _rows(db)]
    assert refs == [_sid(A), _sid(B), _sid(B_EDITED)]


def test_repeated_text_on_page_gets_ordinal(env):
    db, pages = env
    _ingest(pages, DEMO, A, B, A)
    assert [r["entry_ref"] for r in _rows(db)] == [_sid(A), _sid(B), f"{_sid(A)}-2"]


def test_groups_rebuilt_after_ingestion(env):
    db, pages = env
    _ingest(pages, DEMO, A, B)
    _ingest(pages, OTHER, A)
    group = db.fetch_one(
        "SELECT member_count, domain_count FROM faq_groups WHERE content_hash = ?",
        (content_hash(*A),),
    )
    assert (group["member_count"], group["domain_count"]) == (2, 2)
    assert db.fetch_one("SELECT COUNT(*) AS n FROM faq_entry_groups")["n"] == 3


def test_guard_refuses_unmigrated_faq_rows(env):
    db, pages = env
    legacy_corpus(db)
    before = _rows(db)
    with pytest.raises(MigrationError) as exc:
        _ingest(pages, DEMO, A)
    msg = str(exc.value)
    assert "claw-easa db faq-ids plan --data-dir" in msg
    assert "faq-ids apply" in msg
    assert "ingestion is blocked" in msg
    assert _rows(db) == before
    assert not is_applied(db)


def test_existing_row_with_other_text_under_same_id_raises(env):
    db, pages = env
    _ingest(pages, DEMO, A)
    db.execute("UPDATE regulation_entries SET body_text = 'Something else entirely.'")
    with pytest.raises(StableIdCollision):
        _ingest(pages, DEMO, A)
