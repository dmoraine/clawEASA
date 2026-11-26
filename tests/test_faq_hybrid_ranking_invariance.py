"""Hybrid over-fetch must not change non-FAQ ranking (finding F4, chunk accumulation).

BASE is the literal pre-grouping merge with fetch size ``top_k``; grouping may
only collapse FAQ copies and refill the freed slots with entries ranked after
every BASE-window entry.
"""
from __future__ import annotations

import re

import pytest

from claw_easa.db.faq_migration import up
from claw_easa.ingest.faq_identity import normalise
from tests.faq_fixtures import add_domain, add_entry, open_db

Q = "fuel planning"
PAD = " The operator shall keep records and apply the procedure in every case." * 4
_WORD_RE = re.compile(r"[a-z]{3,}")


def _boost(query, slug):
    overlap = set(_WORD_RE.findall(query.lower())) & set(slug.replace("-", " ").split())
    return min(0.15, 0.05 * len(overlap)) if overlap else 0.0


def base_ranking(db, query, vector, top_k, slug=None, fts_weight=0.4, vector_weight=0.6):
    """The old hybrid merge, verbatim, with fetch size top_k (no grouping)."""
    from claw_easa.retrieval.exact import search_references

    fts_results = search_references(db, query, limit=top_k, slug=slug, group_faq=False)
    vec_results = vector(db, query, top_k=top_k, slug=slug)
    merged = {}
    if fts_results:
        max_fts = max((r.get("fts_score", 0) for r in fts_results), default=1) or 1
        for row in fts_results:
            row["hybrid_score"] = (row.get("fts_score", 0) / max_fts) * fts_weight
            row["match_source"] = "fts"
            merged[row["id"]] = row
    if vec_results:
        max_vec = max((r.get("vector_score", 0) for r in vec_results), default=1) or 1
        for row in vec_results:
            eid = row.get("entry_id") or row.get("id")
            norm = (row.get("vector_score", 0) / max_vec) * vector_weight
            if eid in merged:
                merged[eid]["hybrid_score"] += norm
                merged[eid]["match_source"] = "hybrid"
            else:
                row["hybrid_score"] = norm
                row["match_source"] = "vector"
                row["id"] = eid
                merged[eid] = row
    if not slug:
        for row in merged.values():
            row["hybrid_score"] += _boost(query, row.get("slug", ""))
    return sorted(merged.values(), key=lambda r: r["hybrid_score"], reverse=True)


def _stub(monkeypatch, plan):
    """plan: [(entry_id, vector_score)] in descending score; one dict per chunk."""

    def fake(db, query, top_k=10, *, slug=None):
        rows = []
        for n, (eid, score) in enumerate(plan):
            e = db.fetch_one(
                "SELECT e.id AS entry_id, e.entry_ref, e.entry_type, e.title, e.body_text, "
                "d.slug FROM regulation_entries e JOIN source_documents d "
                "ON d.id = e.document_id WHERE e.id = ?", (eid,))
            if slug is None or e["slug"] == slug:
                rows.append(dict(e, chunk_text=f"{e['entry_ref']} chunk {n}", vector_score=score))
        rows.sort(key=lambda r: r["vector_score"], reverse=True)
        return rows[:top_k]

    monkeypatch.setattr("claw_easa.retrieval.vector.vector_search", fake)
    return fake


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = open_db(tmp_path)
    monkeypatch.setattr("claw_easa.config._settings", db.settings)
    yield db
    db.close()


def _rules_corpus(db, slug="rules-ops"):
    rules = add_domain(db, slug, family="ear")
    ids = {}
    for n, kind in enumerate(("AMC", "GM", "AMC", "GM", "AMC", "GM"), start=1):
        ref = f"{kind}1 OPS.{n:03d}"
        ids[ref] = add_entry(db, rules, ref, f"Fuel planning rule {n}",
                             f"Fuel planning guidance {n}." + PAD * n, entry_type=kind)
    ids["INFO"] = add_entry(db, rules, "INFO SUBJECT 1", "Subject page", "Unrelated text.",
                            entry_type="INFO")
    ids["DECOY"] = add_entry(db, rules, "GM9 OPS.999", "Decoy", "Unrelated decoy.",
                             entry_type="GM")
    return ids


def _faq_copies(db, title, body, slugs):
    out = []
    for s in slugs:
        dom = add_domain(db, s)
        out.append(add_entry(db, dom, f"FAQ-{s}-001", title, body))
    return out


def _chunks_beyond_window(ids, top_k):
    """Window = top_k rule chunks; then 8 INFO chunks and a decoy beyond it."""
    rules = [v for k, v in ids.items() if k not in ("INFO", "DECOY")]
    plan = [(eid, 0.90 - 0.01 * n) for n, eid in enumerate(rules[:top_k])]
    plan += [(ids["INFO"], 0.80 - 0.01 * n) for n in range(8)]
    plan.append((ids["DECOY"], 0.70))
    plan += [(eid, 0.60 - 0.01 * n) for n, eid in enumerate(rules[top_k:])]
    return plan


def _key(rows):
    return [(r["entry_ref"], r["hybrid_score"], r["match_source"]) for r in rows]


def _approx(rows):
    return [(ref, pytest.approx(score, abs=1e-9), src) for ref, score, src in rows]


def test_a_non_faq_order_and_scores_equal_base(db, monkeypatch):
    from claw_easa.retrieval.hybrid import hybrid_search

    top_k = 5
    ids = _rules_corpus(db)
    _faq_copies(db, "Is fuel planning answered?", "Yes.", ("faq-a", "faq-bb"))
    up(db)
    fake = _stub(monkeypatch, _chunks_beyond_window(ids, top_k))
    base = [r for r in base_ranking(db, Q, fake, top_k)[:top_k] if r["entry_type"] != "FAQ"]
    rows = hybrid_search(db, Q, top_k=top_k)
    got = [r for r in rows if r["entry_type"] != "FAQ"]
    assert _key(got[:len(base)]) == _approx(_key(base))
    assert all(r["entry_ref"] != "INFO SUBJECT 1" for r in rows[:len(base)])


def test_b_faq_collapse_refills_with_rules(db, monkeypatch):
    from claw_easa.retrieval.hybrid import hybrid_search

    texts = [(f"Is fuel planning question {g} answered?", f"Fuel planning answer {g}.")
             for g in range(3)]
    sizes = {"faq-s": 0, "faq-m": 1, "faq-l": 2}
    faq = []
    for s, extra in sizes.items():
        dom = add_domain(db, s)
        for g, (title, body) in enumerate(texts):
            faq.append(add_entry(db, dom, f"FAQ-{s}-00{g}", title, body))
        for n in range(extra):
            add_entry(db, dom, f"FAQ-{s}-10{n}", f"Other {n}?", "Other.")
    rules = add_domain(db, "rules-ops", family="ear")
    rule_ids = [add_entry(db, rules, f"{k}1 OPS.{n:03d}", f"Fuel planning {k} {n}",
                          "Fuel planning." + PAD, entry_type=k)
                for n, k in enumerate(("AMC", "GM", "AMC", "GM"))]
    up(db)
    plan = [(eid, 0.99 - 0.01 * n) for n, eid in enumerate(e for e in faq for _ in (0, 1))]
    plan += [(eid, 0.50 - 0.01 * n) for n, eid in enumerate(rule_ids)]
    _stub(monkeypatch, plan)
    rows = hybrid_search(db, Q, top_k=10)
    normed = [normalise(f"{r['title']} {r['body_text']}") for r in rows if r["entry_type"] == "FAQ"]
    assert len(normed) == len(set(normed)) == 3
    assert {"AMC", "GM"} <= {r["entry_type"] for r in rows}
    assert all(r["slug"] == "faq-s" for r in rows if r["entry_type"] == "FAQ")


def test_c_slug_without_faq_equals_base(db, monkeypatch):
    from claw_easa.retrieval.hybrid import hybrid_search

    top_k = 5
    ids = _rules_corpus(db)
    _faq_copies(db, "Is fuel planning answered?", "Yes.", ("faq-a", "faq-bb"))
    up(db)
    fake = _stub(monkeypatch, _chunks_beyond_window(ids, top_k))
    base = base_ranking(db, Q, fake, top_k, slug="rules-ops")[:top_k]
    assert len(base) == top_k
    rows = hybrid_search(db, Q, top_k=top_k, slug="rules-ops")
    assert _key(rows) == _approx(_key(base))


def test_d_beyond_window_never_outranks_window_entry(db, monkeypatch):
    from claw_easa.retrieval.hybrid import hybrid_search

    top_k = 4
    rules = add_domain(db, "rules-ops", family="ear")
    weak = add_entry(db, rules, "GM1 OPS.001", "Weak", "Unrelated.", entry_type="GM")
    faq = _faq_copies(db, "Is fuel planning answered?", "Fuel planning yes.",
                      ("faq-a", "faq-bb", "faq-ccc"))
    boosted = add_domain(db, "fuel-planning-info", family="ear")
    extra = add_entry(db, boosted, "INFO SUBJECT 2", "Subject", "Unrelated.", entry_type="INFO")
    plan = [(faq[0], 0.95), (faq[1], 0.94), (faq[2], 0.93), (weak, 0.81), (extra, 0.80)]
    up(db)
    fake = _stub(monkeypatch, plan)
    window = {r["id"] for r in base_ranking(db, Q, fake, top_k)}
    rows = hybrid_search(db, Q, top_k=top_k)
    refs = [r["entry_ref"] for r in rows]
    assert "INFO SUBJECT 2" in refs  # refilled a slot freed by the collapse
    by_ref = {r["entry_ref"]: r for r in rows}
    assert by_ref["INFO SUBJECT 2"]["hybrid_score"] > by_ref["GM1 OPS.001"]["hybrid_score"]
    in_window = [r["id"] in window for r in rows]
    assert in_window == sorted(in_window, reverse=True)


@pytest.mark.parametrize("slug", [None, "rules-ops"])
def test_e_slug_fetches_exactly_base(db, monkeypatch, slug):
    """With a slug the fetch is BASE's (top_k): over-fetching would move the
    FAISS pool (3*top_k, then slug-filtered) and change which in-slug chunks
    fall in the window."""
    from claw_easa.retrieval import exact
    from claw_easa.retrieval.faq_groups import overfetch_factor
    from claw_easa.retrieval.hybrid import hybrid_search

    top_k = 5
    ids = _rules_corpus(db)
    _faq_copies(db, "Is fuel planning answered?", "Yes.", ("faq-a", "faq-bb"))
    up(db)
    fake = _stub(monkeypatch, _chunks_beyond_window(ids, top_k))
    vec_calls, fts_calls = [], []

    def recording(db, query, top_k=10, *, slug=None):
        vec_calls.append(top_k)
        return fake(db, query, top_k=top_k, slug=slug)

    real_refs = exact.search_references

    def recording_refs(db, query, limit=20, **kw):
        fts_calls.append(limit)
        return real_refs(db, query, limit=limit, **kw)

    monkeypatch.setattr("claw_easa.retrieval.vector.vector_search", recording)
    monkeypatch.setattr("claw_easa.retrieval.hybrid.search_references", recording_refs)
    hybrid_search(db, Q, top_k=top_k, slug=slug)
    expected = top_k if slug else top_k * overfetch_factor()
    assert (vec_calls, fts_calls) == ([expected], [expected])
