"""S4 review fixes: rollback reporting, down() validation, grouping gate, provenance."""
from __future__ import annotations


import pytest
from click.testing import CliRunner

from claw_easa.cli import main
from claw_easa.db.faq_migration import VERSION, MigrationError, down, up
from tests.faq_fixtures import add_domain, add_entry, legacy_corpus, open_db


# ── a. rollback --against on an unmigrated database ─────────────────────


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


def test_rollback_not_applied_against_is_not_a_verification(data_dir, tmp_path):
    backup = tmp_path / "b.db"
    assert _run("backup", "--data-dir", str(data_dir), "--backup-to", str(backup)).exit_code == 0
    result = _run("rollback", "--data-dir", str(data_dir), "--against", str(backup))
    assert result.exit_code != 0, result.output
    assert "rolled_back: no" in result.output.splitlines()
    assert "migration 005 was not applied; nothing was rolled back" in result.output
    assert "against_restorable_match: yes" not in result.output


def test_rollback_not_applied_without_against_stays_ok(data_dir):
    result = _run("rollback", "--data-dir", str(data_dir))
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["rolled_back: no"]


# ── b. down() validates the whole restore mapping first ─────────────────


@pytest.fixture
def mig(tmp_path):
    db = open_db(tmp_path)
    ids = legacy_corpus(db)
    up(db)
    yield db, ids
    db.close()


def _all(db, sql):
    with db.connection() as conn:
        return conn.execute(sql).fetchall()


def _state(db):
    q = lambda sql: [tuple(r.values()) for r in _all(db, sql)]  # noqa: E731
    return (
        q("SELECT id, entry_ref FROM regulation_entries ORDER BY id"),
        q("SELECT entry_id, chunk_text FROM entry_chunks ORDER BY id"),
        q("SELECT alias_ref, entry_id, alias_kind FROM faq_aliases ORDER BY 1, 2"),
        q("SELECT * FROM faq_groups ORDER BY group_id"),
        q("SELECT * FROM faq_entry_groups ORDER BY entry_id"),
        q("SELECT version FROM schema_migrations ORDER BY version"),
        q("SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"),
    )


def _tamper_duplicate_alias(db, ids):
    # Two FAQ rows would be restored to the same legacy ref.
    db.execute("UPDATE faq_aliases SET alias_ref = 'FAQ-grand-001' WHERE entry_id = ?",
               (ids["FAQ-grand-002"],))


def _tamper_two_aliases(db, ids):
    db.execute("INSERT INTO faq_aliases (alias_ref, entry_id) VALUES ('FAQ-grand-099', ?)",
               (ids["FAQ-grand-002"],))


def _tamper_alias_is_current_ref(db, ids):
    # A legacy alias equal to another row's current entry_ref.
    db.execute("UPDATE faq_aliases SET alias_ref = 'ORO.FTL.110' WHERE entry_id = ?",
               (ids["FAQ-beta-002"],))


def _tamper_alias_on_non_faq(db, ids):
    rule = db.fetch_one("SELECT id FROM regulation_entries WHERE entry_type = 'IR'")["id"]
    db.execute("INSERT INTO faq_aliases (alias_ref, entry_id) VALUES ('FAQ-rule-001', ?)",
               (rule,))


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(("tamper", "message"), [
    (_tamper_duplicate_alias, "duplicate_alias_refs=1"),
    (_tamper_two_aliases, "entries_with_several_aliases=1"),
    (_tamper_alias_is_current_ref, "aliases_equal_to_other_entry_ref=1"),
    (_tamper_alias_on_non_faq, "aliases_on_non_faq_entries=1"),
])
def test_down_refuses_invalid_mapping_and_changes_nothing(mig, tamper, message, force):
    db, ids = mig
    tamper(db, ids)
    before = _state(db)
    with pytest.raises(MigrationError, match=message):
        down(db, force=force)
    assert _state(db) == before


def test_down_valid_mapping_still_rolls_back(mig):
    db, _ = mig
    assert down(db) is True
    assert db.fetch_one("SELECT COUNT(*) AS n FROM faq_aliases")["n"] == 0


# ── c. grouping_active follows schema_migrations ────────────────────────

SHARED_T, SHARED_B = "Is the gated question answered?", "Yes, the gated answer applies."


@pytest.fixture
def gate_db(tmp_path, monkeypatch):
    db = open_db(tmp_path)
    monkeypatch.setattr("claw_easa.config._settings", db.settings)
    a, b = add_domain(db, "faq-a"), add_domain(db, "faq-bb")
    add_entry(db, a, "FAQ-a-001", SHARED_T, SHARED_B)
    add_entry(db, b, "FAQ-bb-001", SHARED_T, SHARED_B)
    up(db)
    yield db
    db.close()


def _unrecord(db):
    db.execute("DELETE FROM schema_migrations WHERE version = ?", (VERSION,))


def _vector_all(monkeypatch):
    def fake(db, query, top_k=10, *, slug=None):
        rows = _all(
            db,
            "SELECT e.id AS entry_id, e.entry_ref, e.entry_type, e.title, e.body_text, d.slug "
            "FROM regulation_entries e JOIN source_documents d ON d.id = e.document_id "
            "ORDER BY e.id")
        return [dict(r, chunk_text=f"{r['entry_ref']} chunk", vector_score=0.9 - 0.01 * n)
                for n, r in enumerate(rows) if slug is None or r["slug"] == slug][:top_k]

    monkeypatch.setattr("claw_easa.retrieval.vector.vector_search", fake)


def test_grouping_active_requires_recorded_005(gate_db, monkeypatch):
    from claw_easa.retrieval.exact import search_references
    from claw_easa.retrieval.faq_groups import grouping_active
    from claw_easa.retrieval.hybrid import hybrid_search

    _vector_all(monkeypatch)
    assert grouping_active(gate_db) is True
    assert len(search_references(gate_db, "gated question")) == 1
    assert len(hybrid_search(gate_db, "gated question")) == 1

    _unrecord(gate_db)
    assert gate_db.fetch_one("SELECT COUNT(*) AS n FROM faq_entry_groups")["n"] == 2
    assert grouping_active(gate_db) is False
    refs = search_references(gate_db, "gated question")
    assert len(refs) == 2 and not any("also_in" in r for r in refs)
    rows = hybrid_search(gate_db, "gated question")
    assert len(rows) == 2 and not any("also_in" in r for r in rows)


# ── d. a non-canonical-only candidate keeps the canonical's provenance ──
# One FAQ text in faq-small (canonical, 1 row) and faq-big (3 rows).  The
# query names "big", which only the big copy's stable ref contains.

PROV_T, PROV_B = "Can the commander extend the duty?", "Commander discretion applies."
SCORE_KEYS = {"fts_score", "hybrid_score", "vector_score", "match_source", "chunk_text",
              "chunk_kind", "chunk_index", "matched_subref", "resolved_from_alias"}
# displayed-row key -> entry_detail (lookup --json) entry key
DETAIL_KEY = {"entry_url": "source_url", "entry_locator": "source_locator",
              "document_page_url": "page_url", "document_revision": "revision",
              "part_code": "part", "subpart_code": "subpart", "slug": "slug",
              "entry_ref": "entry_ref", "title": "title", "body_text": "body_text"}


@pytest.fixture
def prov_db(tmp_path, monkeypatch):
    db = open_db(tmp_path)
    monkeypatch.setattr("claw_easa.config._settings", db.settings)
    ids = {}
    for slug, extra in (("faq-small", 0), ("faq-big", 2)):
        dom = add_domain(db, slug)
        db.execute("UPDATE source_documents SET source_url = ?, page_url = ?, revision = ? "
                   "WHERE id = ?", (f"https://doc.test/{slug}", f"https://page.test/{slug}",
                                    f"rev-{slug}", dom["doc"]))
        db.execute("UPDATE regulation_parts SET part_code = ? WHERE id = ?",
                   (f"P-{slug}", dom["part"]))
        db.execute("UPDATE regulation_subparts SET subpart_code = ? WHERE id = ?",
                   (f"S-{slug}", dom["subpart"]))
        ids[slug] = add_entry(db, dom, f"FAQ-{slug.split('-')[1]}-001", PROV_T, PROV_B)
        db.execute("UPDATE regulation_entries SET source_locator = ? WHERE id = ?",
                   (f"loc-{slug}", ids[slug]))
        for n in range(extra):
            add_entry(db, dom, f"FAQ-big-00{n + 2}", f"Other {n}?", f"Other {n}.")
    up(db)
    yield db, ids
    db.close()


def _ref(db, entry_id):
    return db.fetch_one("SELECT entry_ref FROM regulation_entries WHERE id = ?",
                        (entry_id,))["entry_ref"]


def _assert_canonical_provenance(db, shown, direct, canonical_id):
    from claw_easa.retrieval.entry_detail import entry_detail

    assert shown["id"] == canonical_id and shown["slug"] == "faq-small"
    assert set(direct) - SCORE_KEYS <= set(shown), set(direct) - set(shown)
    entry = entry_detail(db, _ref(db, canonical_id))["entry"]
    for key, detail_key in DETAIL_KEY.items():
        if key in shown:
            assert shown[key] == entry[detail_key], key
    if "document_url" in shown:
        assert shown["document_url"] == "https://doc.test/faq-small"


def _only_big_vector(monkeypatch, ids):
    def fake(db, query, top_k=10, *, slug=None):
        r = db.fetch_one(
            "SELECT e.id AS entry_id, e.entry_ref, e.entry_type, e.title, e.body_text, d.slug "
            "FROM regulation_entries e JOIN source_documents d ON d.id = e.document_id "
            "WHERE e.id = ?", (ids["faq-big"],))
        return [dict(r, chunk_text=f"{r['entry_ref']} (FAQ) — chunk", chunk_id=1,
                     vector_score=0.9)]

    monkeypatch.setattr("claw_easa.retrieval.vector.vector_search", fake)


def test_refs_non_canonical_only_keeps_canonical_provenance(prov_db):
    from claw_easa.retrieval.exact import search_references

    db, ids = prov_db
    (shown,) = [r for r in search_references(db, "big commander") if r["title"] == PROV_T]
    direct = search_references(db, _ref(db, ids["faq-small"]))[0]
    _assert_canonical_provenance(db, shown, direct, ids["faq-small"])


def test_snippets_non_canonical_only_keeps_canonical_provenance(prov_db):
    from claw_easa.retrieval.snippets import search_snippets

    db, ids = prov_db
    (shown,) = [r for r in search_snippets(db, "big commander") if r["title"] == PROV_T]
    direct = search_snippets(db, _ref(db, ids["faq-small"]))[0]
    _assert_canonical_provenance(db, shown, direct, ids["faq-small"])


@pytest.mark.parametrize("query", ["big commander", "unmatchedword elsewhere"])
def test_hybrid_non_canonical_only_keeps_canonical_provenance(prov_db, monkeypatch, query):
    from claw_easa.retrieval.hybrid import hybrid_search

    db, ids = prov_db
    _only_big_vector(monkeypatch, ids)
    (shown,) = [r for r in hybrid_search(db, query) if r["title"] == PROV_T]
    direct = hybrid_search(db, _ref(db, ids["faq-small"]))[0]
    _assert_canonical_provenance(db, shown, direct, ids["faq-small"])
