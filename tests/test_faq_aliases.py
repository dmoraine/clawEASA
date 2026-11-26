"""S1 — resolve_faq_ref: current FAQ ref first, then retired positional alias."""

import pytest

from claw_easa.db.faq_aliases import resolve_faq_ref
from claw_easa.db.faq_migration import up
from tests.faq_fixtures import add_domain, add_entry, legacy_corpus, open_db


@pytest.fixture
def migrated(tmp_path):
    db = open_db(tmp_path)
    ids = legacy_corpus(db)
    up(db)
    yield db, ids
    db.close()


def _ref(db, entry_id):
    return db.fetch_one("SELECT entry_ref FROM regulation_entries WHERE id = ?", (entry_id,))[
        "entry_ref"
    ]


def test_stable_id(migrated):
    db, ids = migrated
    assert resolve_faq_ref(db, _ref(db, ids["FAQ-parent-002"])) == [ids["FAQ-parent-002"]]


def test_legacy_positional_alias(migrated):
    db, ids = migrated
    assert resolve_faq_ref(db, "FAQ-grand-003") == [ids["FAQ-grand-003"]]


def test_unknown(migrated):
    db, _ = migrated
    assert resolve_faq_ref(db, "FAQ-grand-999") == []
    assert resolve_faq_ref(db, "ORO.FTL.110") == []  # not a FAQ entry


def test_ref_in_several_documents(migrated):
    db, _ = migrated
    first, second = add_domain(db, "faq-one"), add_domain(db, "faq-two")
    a = add_entry(db, first, "FAQ-shared-0000aaaa", "Shared ref question?", "Answer one.")
    b = add_entry(db, second, "FAQ-shared-0000aaaa", "Shared ref question?", "Answer two.")
    assert resolve_faq_ref(db, "FAQ-shared-0000aaaa") == [a, b]


def test_current_ref_wins_over_alias(migrated):
    db, ids = migrated
    dom = add_domain(db, "faq-reuse")
    current = add_entry(db, dom, "FAQ-grand-001", "Reused ref question?", "Reused answer.")
    assert resolve_faq_ref(db, "FAQ-grand-001") == [current]
