"""S2 — read-side FAQ grouping, alias resolution and additive outputs (synthetic only)."""
from __future__ import annotations

import pytest

from claw_easa.config import get_settings, reset_settings


@pytest.mark.parametrize(("raw", "expected"), [
    (None, 10), ("3", 3), ("1", 1), ("0", 10), ("-2", 10), ("ten", 10), ("", 10),
])
def test_overfetch_factor_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CLAW_EASA_FAQ_OVERFETCH_FACTOR", raising=False)
    else:
        monkeypatch.setenv("CLAW_EASA_FAQ_OVERFETCH_FACTOR", raw)
    reset_settings()
    try:
        assert get_settings().faq_overfetch_factor == expected
    finally:
        reset_settings()


# ── Synthetic regression corpus ─────────────────────────────────────────
# One FAQ text in three domains (sizes small 2 < mid 3 < big 5), so the
# canonical member is the faq-small copy, plus distinct IR/AMC/GM rules.

from claw_easa.db.faq_migration import up  # noqa: E402
from claw_easa.ingest.faq_identity import normalise  # noqa: E402
from tests.faq_fixtures import add_domain, add_entry, open_db  # noqa: E402

FDP_TITLE = "Can the commander extend the flight duty period?"
FDP_BODY = "Commander discretion allows an FDP extension when crew fatigue is managed."
LEGACY = ("FAQ-small-028", "FAQ-mid-057", "FAQ-big-036")
RULES = [
    ("IR", "ORO.FTL.205", "Flight duty period", "The FDP extension by commander discretion."),
    ("AMC", "AMC1 ORO.FTL.205", "Commander discretion", "Commander discretion fatigue guidance."),
    ("GM", "GM1 ORO.FTL.205", "FDP extension", "FDP extension fatigue explanation."),
    ("AMC", "AMC1 ORO.FTL.210", "Rest after extension", "Rest after commander discretion."),
    ("GM", "GM1 ORO.FTL.210", "Fatigue reporting", "Fatigue reports after an FDP extension."),
]

# Longer rule bodies so FTS (bm25) ranks the FAQ copies above the rules.
RULE_PAD = " The operator shall keep records and apply the procedure in every case." * 4


def build_corpus(db) -> dict[str, int]:
    small, mid, big = (add_domain(db, s) for s in ("faq-small", "faq-mid", "faq-big"))
    ids = {
        "FAQ-small-028": add_entry(db, small, "FAQ-small-028", FDP_TITLE, FDP_BODY),
        "FAQ-mid-057": add_entry(db, mid, "FAQ-mid-057", FDP_TITLE, FDP_BODY),
        "FAQ-big-036": add_entry(db, big, "FAQ-big-036", FDP_TITLE, FDP_BODY),
    }
    add_entry(db, small, "FAQ-small-029", "Is a small-only question answered?", "Only small.")
    for n in (58, 59):
        add_entry(db, mid, f"FAQ-mid-0{n}", f"Mid question {n}?", f"Mid answer {n}.")
    for n in (37, 38, 39, 40):
        add_entry(db, big, f"FAQ-big-0{n}", f"Big question {n}?", f"Big answer {n}.")
    rules = add_domain(db, "rules-ftl", family="ear")
    for entry_type, ref, title, body in RULES:
        ids[ref] = add_entry(db, rules, ref, title, body + RULE_PAD, entry_type=entry_type)
    return ids


def _ref_of(db, entry_id):
    return db.fetch_one("SELECT entry_ref FROM regulation_entries WHERE id = ?", (entry_id,))[
        "entry_ref"
    ]


@pytest.fixture
def raw_db(tmp_path, monkeypatch):
    """Unmigrated: positional refs, empty group tables."""
    db = open_db(tmp_path)
    monkeypatch.setattr("claw_easa.config._settings", db.settings)
    ids = build_corpus(db)
    yield db, ids
    db.close()


@pytest.fixture
def mig_db(raw_db):
    db, ids = raw_db
    up(db)
    stable = {legacy: _ref_of(db, ids[legacy]) for legacy in LEGACY}
    return db, ids, stable


def _rows_for(db, ids):
    from claw_easa.retrieval.exact import lookup_reference

    return [lookup_reference(db, _ref_of(db, i))[0] for i in ids]


def test_format_also_in():
    from claw_easa.retrieval.faq_groups import format_also_in

    assert format_also_in({}) == ""
    assert format_also_in({"also_in": []}) == ""
    row = {"also_in": [{"entry_ref": "FAQ-a-1", "slug": "faq-a"},
                       {"entry_ref": "FAQ-b-1", "slug": "faq-b"}]}
    assert format_also_in(row) == " [also in: faq-a, faq-b]"


def test_collapse_three_copies_to_canonical(mig_db):
    from claw_easa.retrieval.faq_groups import collapse_faq_duplicates

    db, ids, stable = mig_db
    big, mid = (dict(r, fts_score=s) for r, s in zip(
        _rows_for(db, [ids["FAQ-big-036"], ids["FAQ-mid-057"]]), (9.0, 5.0)))
    rule = dict(_rows_for(db, [ids["GM1 ORO.FTL.205"]])[0], fts_score=7.0)
    out = collapse_faq_duplicates(db, [big, rule, mid], score_key="fts_score")
    assert [r["entry_ref"] for r in out] == [stable["FAQ-small-028"], "GM1 ORO.FTL.205"]
    assert out[1] is rule and "also_in" not in rule
    faq = out[0]
    assert faq["fts_score"] == 9.0  # best member, never a sum
    assert faq["slug"] == "faq-small" and faq["id"] == ids["FAQ-small-028"]
    assert faq["also_in"] == [{"entry_ref": stable["FAQ-mid-057"], "slug": "faq-mid"},
                              {"entry_ref": stable["FAQ-big-036"], "slug": "faq-big"}]
    assert faq["group_size"] == 3 and len(faq["content_hash"]) == 64
    assert faq["canonical_ref"] == stable["FAQ-small-028"]


def test_collapse_carries_chunk_of_best_member(mig_db):
    from claw_easa.retrieval.faq_groups import collapse_faq_duplicates

    db, ids, stable = mig_db
    big = dict(_rows_for(db, [ids["FAQ-big-036"]])[0], hybrid_score=0.9,
               chunk_text=f"{stable['FAQ-big-036']} (FAQ) — matched paragraph",
               chunk_kind="paragraph", chunk_index=2, match_source="vector")
    (faq,) = collapse_faq_duplicates(db, [big], score_key="hybrid_score")
    assert faq["entry_ref"] == stable["FAQ-small-028"]
    assert faq["chunk_text"] == f"{stable['FAQ-small-028']} (FAQ) — matched paragraph"
    assert (faq["chunk_kind"], faq["chunk_index"], faq["match_source"]) == (
        "paragraph", 2, "vector")
    assert faq["hybrid_score"] == 0.9


def test_collapse_slug_keeps_in_slug_member(mig_db):
    from claw_easa.retrieval.faq_groups import collapse_faq_duplicates

    db, ids, stable = mig_db
    big = dict(_rows_for(db, [ids["FAQ-big-036"]])[0], fts_score=3.0)
    out = collapse_faq_duplicates(db, [big], slug="faq-big", score_key="fts_score")
    assert out[0]["entry_ref"] == stable["FAQ-big-036"] and out[0]["slug"] == "faq-big"
    assert [m["slug"] for m in out[0]["also_in"]] == ["faq-small", "faq-mid"]


def test_collapse_unmigrated_is_identity(raw_db):
    from claw_easa.retrieval.faq_groups import collapse_faq_duplicates

    db, ids = raw_db
    rows = _rows_for(db, [ids[r] for r in LEGACY])
    before = [dict(r) for r in rows]
    out = collapse_faq_duplicates(db, rows)
    assert out == before and all(a is b for a, b in zip(out, rows))


# ── 1. resolve_reference / lookup ───────────────────────────────────────


@pytest.mark.parametrize("legacy", LEGACY)
def test_lookup_resolves_legacy_and_stable_ids(mig_db, legacy):
    from claw_easa.retrieval.exact import lookup_reference

    db, ids, stable = mig_db
    by_alias = lookup_reference(db, legacy)
    assert [r["id"] for r in by_alias] == [ids[legacy]]  # the copy itself
    assert by_alias[0]["entry_ref"] == stable[legacy]
    assert by_alias[0]["resolved_from_alias"] == legacy
    assert len(by_alias[0]["also_in"]) == 2
    by_id = lookup_reference(db, stable[legacy])
    assert [r["id"] for r in by_id] == [ids[legacy]]
    assert by_id[0].get("resolved_from_alias") is None


def test_lookup_non_faq_rows_unchanged(mig_db):
    from claw_easa.retrieval.exact import lookup_reference

    db, _, _ = mig_db
    row = lookup_reference(db, "GM1 ORO.FTL.205")[0]
    assert set(row) == {"id", "entry_ref", "entry_type", "title", "body_text",
                        "body_markdown", "slug", "part_code", "subpart_code"}


# ── 2. refs / search_references ─────────────────────────────────────────


def _faq_texts(rows):
    return [normalise(f"{r['title']} {r['body_text']}") for r in rows if r["entry_type"] == "FAQ"]


@pytest.mark.parametrize("legacy", LEGACY)
def test_refs_resolves_legacy_and_stable_ids(mig_db, legacy):
    from claw_easa.retrieval.exact import search_references

    db, ids, stable = mig_db
    for query in (legacy, stable[legacy]):
        rows = search_references(db, query)
        assert [r["id"] for r in rows] == [ids[legacy]]
        assert rows[0]["fts_score"] == 1.0 and len(rows[0]["also_in"]) == 2
        assert rows[0]["entry_ref"] == stable[legacy]


def test_refs_collapses_copies_and_keeps_rules(mig_db):
    from claw_easa.retrieval import service

    db, ids, stable = mig_db
    rows = service.refs("commander discretion", limit=3)
    assert len(rows) == 3
    texts = _faq_texts(rows)
    assert len(texts) == 1 == len(set(texts))
    faq = next(r for r in rows if r["entry_type"] == "FAQ")
    assert faq["entry_ref"] == stable["FAQ-small-028"]
    assert [m["slug"] for m in faq["also_in"]] == ["faq-mid", "faq-big"]
    assert sum(r["entry_type"] != "FAQ" for r in rows) == 2


def test_refs_slug_returns_in_slug_member(mig_db):
    from claw_easa.retrieval.exact import search_references

    db, ids, stable = mig_db
    rows = search_references(db, "commander discretion", slug="faq-big")
    assert [r["entry_ref"] for r in rows] == [stable["FAQ-big-036"]]


def test_refs_unmigrated_unchanged(raw_db):
    from claw_easa.retrieval.exact import search_references

    db, _ = raw_db
    rows = search_references(db, "commander discretion", limit=3)
    assert len(rows) == 3 and not any("also_in" in r for r in rows)
    assert len(_faq_texts(rows)) >= 2  # copies are not grouped
    assert all("also_in" not in r for r in search_references(db, "FAQ-small-028"))


# ── 3. refs --json / refs text ──────────────────────────────────────────

from click.testing import CliRunner  # noqa: E402

from claw_easa.cli import main as cli_main  # noqa: E402


def _cli(*args):
    result = CliRunner().invoke(cli_main, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_refs_payload_additive_keys(mig_db):
    from claw_easa.retrieval.exact import search_references
    from claw_easa.retrieval.provenance import refs_payload

    db, ids, stable = mig_db
    results = refs_payload(search_references(db, "commander discretion", limit=3),
                           "q", limit=3)["results"]
    faq, rule = results[0], results[1]
    assert faq["also_in"] == [{"entry_ref": stable["FAQ-mid-057"], "slug": "faq-mid"},
                              {"entry_ref": stable["FAQ-big-036"], "slug": "faq-big"}]
    assert faq["legacy_refs"] == ["FAQ-small-028"] and faq["group_size"] == 3
    assert len(faq["content_hash"]) == 64
    assert rule == {
        "entry_ref": "AMC1 ORO.FTL.205", "entry_type": "AMC", "title": "Commander discretion",
        "score": rule["score"],
        "source": {"slug": "rules-ftl", "url": "https://example.test/AMC1 ORO.FTL.205",
                   "url_kind": "entry", "locator": None, "revision": None},
        "also_in": [], "content_hash": None, "legacy_refs": [], "group_size": None,
    }
    assert list(rule)[:5] == ["entry_ref", "entry_type", "title", "score", "source"]


def test_refs_text_suffix(mig_db):
    db, ids, stable = mig_db
    out = _cli("refs", "commander discretion", "--limit", "3")
    lines = out.splitlines()
    assert lines[0].endswith("[score=0.98] [also in: faq-mid, faq-big]")
    assert lines[0].startswith(f"  {stable['FAQ-small-028']} (FAQ) — {FDP_TITLE}")
    assert lines[1] == "  AMC1 ORO.FTL.205 (AMC) — Commander discretion [score=0.81]"


# ── 4. snippets ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("legacy", LEGACY)
def test_snippets_resolves_legacy_and_stable_ids(mig_db, legacy):
    from claw_easa.retrieval.snippets import search_snippets

    db, ids, stable = mig_db
    for query in (legacy, stable[legacy]):
        rows = search_snippets(db, query)
        assert [r["id"] for r in rows] == [ids[legacy]] and len(rows[0]["also_in"]) == 2


def test_snippets_collapse_and_chunk_text(mig_db):
    from claw_easa.retrieval import service

    db, ids, stable = mig_db
    rows = service.snippets("commander discretion", limit=3)
    assert len(rows) == 3 and len(_faq_texts(rows)) == 1
    faq = rows[0]
    assert faq["entry_ref"] == stable["FAQ-small-028"]
    out = _cli("snippets", "commander discretion", "--limit", "3")
    assert f"\n{stable['FAQ-small-028']} (FAQ) — {FDP_TITLE} [also in: faq-mid, faq-big]\n" in out
    assert "\nAMC1 ORO.FTL.205 (AMC) — Commander discretion\n" in out


def test_snippets_unmigrated_unchanged(raw_db):
    from claw_easa.retrieval.snippets import search_snippets

    db, _ = raw_db
    rows = search_snippets(db, "commander discretion", limit=3)
    assert [r["entry_ref"] for r in rows] == list(LEGACY)
    assert not any("also_in" in r for r in rows)


# ── 5. lookup --json / lookup text ──────────────────────────────────────

GROUP_KEYS = ("also_in", "content_hash", "group_size", "canonical_ref", "is_canonical",
              "legacy_refs", "resolved_from_alias")


@pytest.mark.parametrize("legacy", LEGACY)
def test_lookup_json_resolves_legacy_and_stable_ids(mig_db, legacy):
    import json

    db, ids, stable = mig_db
    for query, alias in ((legacy, legacy), (stable[legacy], None)):
        doc = json.loads(_cli("lookup", query, "--json"))
        assert doc["status"] == "found" and doc["schema_version"] == "1.0"
        entry, match = doc["entry"], doc["matches"][0]
        assert entry["entry_ref"] == stable[legacy]  # the copy itself
        for item in (entry, match):
            assert item["resolved_from_alias"] == alias
            assert item["canonical_ref"] == stable["FAQ-small-028"]
            assert item["is_canonical"] is (legacy == "FAQ-small-028")
            assert item["legacy_refs"] == [legacy] and item["group_size"] == 3
            assert len(item["also_in"]) == 2 and len(item["content_hash"]) == 64


def test_lookup_json_non_faq_keys(mig_db):
    from claw_easa.retrieval.entry_detail import entry_detail

    db, _, _ = mig_db
    doc = entry_detail(db, "GM1 ORO.FTL.205")
    for item in (doc["entry"], doc["matches"][0]):
        assert {k: item[k] for k in GROUP_KEYS} == {
            "also_in": [], "content_hash": None, "group_size": None, "canonical_ref": None,
            "is_canonical": None, "legacy_refs": [], "resolved_from_alias": None}
    assert list(doc["matches"][0])[:7] == ["entry_ref", "entry_type", "title", "slug",
                                            "revision", "part", "subpart"]


def test_lookup_text(mig_db):
    db, ids, stable = mig_db
    out = _cli("lookup", "FAQ-big-036")
    assert out.splitlines()[1] == (
        f"{stable['FAQ-big-036']} (FAQ) — {FDP_TITLE} [faq-big] [also in: faq-small, faq-mid]")
    assert _cli("lookup", "GM1 ORO.FTL.205") == (
        "\nGM1 ORO.FTL.205 (GM) — FDP extension [rules-ftl]\n"
        f"  FDP extension fatigue explanation.{RULE_PAD}\n")


# ── 6. hybrid ───────────────────────────────────────────────────────────

Q = "commander discretion"


@pytest.fixture
def fake_vector(monkeypatch):
    """Stub FAISS: four chunks of every FAQ copy rank above the rule chunks."""
    calls: list[int] = []

    def fake(db, query, top_k=10, *, slug=None):
        calls.append(top_k)
        with db.connection() as conn:
            entries = conn.execute(
                "SELECT e.id AS entry_id, e.entry_ref, e.entry_type, e.title, e.body_text, d.slug "
                "FROM regulation_entries e JOIN source_documents d ON d.id = e.document_id "
                "WHERE e.title = ? OR e.entry_type != 'FAQ' "
                "ORDER BY e.entry_type = 'FAQ' DESC, e.id",
                (FDP_TITLE,),
            ).fetchall()
        rows, score = [], 0.99
        for entry in entries:
            for n in range(4 if entry["entry_type"] == "FAQ" else 1):
                rows.append(dict(entry, chunk_text=f"{entry['entry_ref']} chunk {n}",
                                 vector_score=score))
                score -= 0.01
        return [r for r in rows if slug is None or r["slug"] == slug][:top_k]

    monkeypatch.setattr("claw_easa.retrieval.vector.vector_search", fake)
    return calls


def test_hybrid_groups_and_keeps_rules(mig_db, fake_vector):
    from claw_easa.retrieval.hybrid import hybrid_search

    db, ids, stable = mig_db
    raw = hybrid_search(db, Q, top_k=3, group_faq=False)
    assert [r["entry_type"] for r in raw] == ["FAQ"] * 3  # rules evicted by copies
    rows = hybrid_search(db, Q, top_k=3)
    assert fake_vector == [3, 30]
    assert len(rows) <= 3
    texts = _faq_texts(rows)
    assert len(texts) == len(set(texts)) == 1
    assert len({r["entry_ref"] for r in rows}) == len(rows)
    assert {r["entry_type"] for r in rows} >= {"AMC"}
    faq = rows[0]
    assert faq["entry_ref"] == stable["FAQ-small-028"]
    assert [m["slug"] for m in faq["also_in"]] == ["faq-mid", "faq-big"]
    every = hybrid_search(db, Q, top_k=100, group_faq=False)
    best = max(r["hybrid_score"] for r in every if r["title"] == FDP_TITLE)
    assert faq["hybrid_score"] == best  # best member, never a sum


def test_hybrid_slug_returns_in_slug_member(mig_db, fake_vector):
    from claw_easa.retrieval.hybrid import hybrid_search

    db, ids, stable = mig_db
    rows = hybrid_search(db, Q, top_k=5, slug="faq-mid")
    assert [r["entry_ref"] for r in rows if r["title"] == FDP_TITLE] == [stable["FAQ-mid-057"]]
    assert all(r["slug"] == "faq-mid" for r in rows)


def test_hybrid_unmigrated_unchanged(raw_db, fake_vector):
    from claw_easa.retrieval.hybrid import hybrid_search

    db, _ = raw_db
    rows = hybrid_search(db, Q, top_k=3)
    assert fake_vector == [3]
    assert [r["entry_ref"] for r in rows] == list(LEGACY)
    assert not any("also_in" in r for r in rows)


@pytest.mark.parametrize("legacy", LEGACY)
def test_hybrid_resolves_legacy_and_stable_ids(mig_db, fake_vector, legacy):
    from claw_easa.retrieval.hybrid import hybrid_search

    db, ids, stable = mig_db
    for query in (legacy, stable[legacy]):
        (row,) = hybrid_search(db, query)
        assert row["entry_ref"] == stable["FAQ-small-028"]
        assert (row["hybrid_score"], row["match_source"]) == (1.0, "exact")
        assert len(row["also_in"]) == 2
        expected = None if query == stable["FAQ-small-028"] else query
        assert row.get("resolved_from_alias") == expected
    assert fake_vector == []


# ── 7. ask / answering ──────────────────────────────────────────────────


def test_answer_lines_suffix_and_non_faq_identical():
    from claw_easa.retrieval.answering import _format_source_line, format_answer_answer

    rule = {"entry_ref": "GM1 X.1", "entry_type": "GM", "title": "T", "slug": "s",
            "hybrid_score": 0.2}
    assert _format_source_line(rule) == "- GM1 X.1 (GM) — T [s]"
    also = [{"entry_ref": "FAQ-b-1", "slug": "faq-b"}]
    faqs = [{"entry_ref": f"FAQ-a-{n}", "entry_type": "FAQ", "title": f"Q{n}", "slug": "faq-a",
             "hybrid_score": 0.9, "also_in": also} for n in (1, 2)]
    out = format_answer_answer([*faqs, rule], "q")
    assert "- FAQ-a-1 (FAQ) — Q1 [faq-a] [also in: faq-b]" in out.splitlines()
    assert out.splitlines()[-1] == "- Q2 [faq-a] [also in: faq-b]"
    assert "Resolved from alias" not in out


@pytest.mark.parametrize("legacy", LEGACY)
def test_ask_resolves_legacy_and_stable_ids(mig_db, fake_vector, legacy):
    db, ids, stable = mig_db
    canonical = stable["FAQ-small-028"]
    for query in (legacy, stable[legacy]):
        lines = _cli("ask", query).splitlines()
        assert lines[0] == (f"The most relevant interpretive source for '{query}' is "
                            f"{canonical} (FAQ) — {FDP_TITLE}.")
        alias_line = f"Resolved from alias: {query}"
        assert (alias_line in lines) is (query != canonical)
        assert f"- {canonical} (FAQ) — {FDP_TITLE} [faq-small] [also in: faq-mid, faq-big]" \
            in lines


def test_ask_collapses_copies(mig_db, fake_vector):
    db, ids, stable = mig_db
    out = _cli("ask", Q)
    assert out.count(FDP_TITLE) == 2  # lead sentence + one source line, no copy repeated
    assert "[also in: faq-mid, faq-big]" in out


def test_hybrid_cli_suffix(mig_db, fake_vector):
    db, ids, stable = mig_db
    lines = _cli("hybrid", Q, "--top-k", "3").splitlines()
    assert lines[0].startswith(f"  {stable['FAQ-small-028']} (FAQ) — {FDP_TITLE} [score=")
    assert lines[0].endswith(", via=hybrid] [also in: faq-mid, faq-big]")
    assert not any("also in" in line for line in lines[1:])


# ── 8. regressions over S1's legacy corpus ──────────────────────────────

from tests.faq_fixtures import BODIES, DUP, LEGACY_FAQ_COUNT, SAME_TITLE, legacy_corpus  # noqa: E402


@pytest.fixture
def legacy_mig(tmp_path, monkeypatch):
    db = open_db(tmp_path)
    monkeypatch.setattr("claw_easa.config._settings", db.settings)
    ids = legacy_corpus(db)
    up(db)
    yield db, ids
    db.close()


def _count(db):
    return db.fetch_one("SELECT COUNT(*) AS n FROM regulation_entries")["n"]


def test_same_title_different_bodies_stay_separate(legacy_mig):
    from claw_easa.retrieval.exact import search_references
    from claw_easa.retrieval.snippets import search_snippets

    db, _ = legacy_mig
    for rows in (search_references(db, "foreign certificate", limit=10),
                 search_snippets(db, "foreign certificate", limit=10)):
        assert sorted(r["body_text"] for r in rows if r["title"] == SAME_TITLE) == sorted(BODIES)
        assert all(r["group_size"] == 1 and r["also_in"] == [] for r in rows)


def test_intra_domain_duplicates_keep_ordinal_ids(legacy_mig):
    from claw_easa.retrieval.exact import lookup_reference, search_references

    db, ids = legacy_mig
    first, second = (_ref_of(db, ids[r]) for r in ("FAQ-grand-002", "FAQ-grand-003"))
    assert second == f"{first}-2"
    for legacy, ref in (("FAQ-grand-002", first), ("FAQ-grand-003", second)):
        assert [r["entry_ref"] for r in lookup_reference(db, legacy)] == [ref]
    rows = [r for r in search_references(db, "repeated question") if r["title"] == DUP[0]]
    assert [r["entry_ref"] for r in rows] == [first]
    assert rows[0]["also_in"] == [{"entry_ref": second, "slug": "faq-grand"}]


def test_retrieval_deletes_nothing(legacy_mig, fake_vector):
    from claw_easa.retrieval import service

    db, _ = legacy_mig
    before = _count(db)
    for call in (lambda: service.refs("shared question"), lambda: service.snippets("shared"),
                 lambda: service.hybrid("shared question"), lambda: service.lookup("FAQ-grand-001"),
                 lambda: service.lookup_detail("FAQ-grand-001")):
        call()
    assert _count(db) == before
    faq = db.fetch_one("SELECT COUNT(*) AS n FROM regulation_entries WHERE entry_type = 'FAQ'")
    assert faq["n"] == LEGACY_FAQ_COUNT
