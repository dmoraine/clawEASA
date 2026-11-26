from __future__ import annotations

import logging
import re

from claw_easa.db.sqlite import Database
from claw_easa.retrieval.exact import lookup_reference, search_references
from claw_easa.retrieval.faq_groups import (
    collapse_faq_duplicates,
    faq_rows_for_ref,
    grouping_active,
    overfetch_factor,
)

log = logging.getLogger(__name__)

REFERENCE_PATTERN = re.compile(
    r'^(?:AMC\d?|GM\d?|IR|CS|CAT|ORO|SPA|ARO|ORA|ARA|MED|FCL|ATCO|ATS|ADR|'
    r'SERA|Part-|Annex\s|Article\s|Appendix\s|Regulation\s)'
    r'|^\d{3}/\d{4}',
    re.IGNORECASE,
)

_WORD_RE = re.compile(r'[a-z]{3,}')


def looks_like_reference(query: str) -> bool:
    return bool(REFERENCE_PATTERN.match(query.strip()))


def _source_relevance_boost(query_words: set[str], slug: str) -> float:
    """Generic boost when query words overlap with a source slug."""
    slug_words = set(slug.replace("-", " ").split())
    overlap = query_words & slug_words
    if not overlap:
        return 0.0
    return min(0.15, 0.05 * len(overlap))


def hybrid_search(
    db: Database,
    query: str,
    fts_weight: float = 0.4,
    vector_weight: float = 0.6,
    top_k: int = 15,
    *,
    slug: str | None = None,
    group_faq: bool = True,
) -> list[dict]:
    """FTS + vector search, merged; FAQ copies collapse to one row per group.

    Grouping (FAQ-ref pre-step, ``top_k * faq_overfetch_factor`` raw fetch,
    collapse after the merge) only applies once migration 005 populated the
    group tables and *group_faq* is true; otherwise this is the plain merge.
    """
    if looks_like_reference(query):
        exact = lookup_reference(db, query.strip())
        if exact:
            for row in exact:
                row["hybrid_score"] = 1.0
                row["match_source"] = "exact"
            return exact

    grouped = group_faq and grouping_active(db)
    if grouped and len(query.split()) == 1:
        exact = _faq_ref_rows(db, query.strip(), slug)
        if exact:
            return exact[:top_k]
    # With a slug, fetch exactly BASE's top_k: vector_search filters a 3*top_k
    # FAISS pool by slug, so a wider fetch would change which in-slug chunks
    # fall in the window (design §5; in-slug copies are not refilled).
    raw_k = top_k * overfetch_factor() if grouped and not slug else top_k

    fts_results = search_references(db, query, limit=raw_k, slug=slug, group_faq=False)

    try:
        from claw_easa.retrieval.vector import vector_search

        vec_results = vector_search(db, query, top_k=raw_k, slug=slug)
    except FileNotFoundError:
        log.warning("FAISS index not found — falling back to FTS only")
        vec_results = []

    # Window = what the plain merge sees (both lists come sorted by score), so
    # window scores and normalisers equal the ungrouped ranking.  Rows beyond
    # it only refill slots freed by the FAQ collapse (finding F4, design §5).
    fts_win, vec_win = fts_results[:top_k], vec_results[:top_k]
    max_fts = max((r.get("fts_score", 0) for r in fts_win), default=1) or 1
    max_vec = max((r.get("vector_score", 0) for r in vec_win), default=1) or 1

    merged: dict[int, dict] = {}
    for row in fts_win:
        row["hybrid_score"] = (row.get("fts_score", 0) / max_fts) * fts_weight
        row["match_source"] = "fts"
        merged[row["id"]] = row

    for row in vec_win:
        eid = row.get("entry_id") or row.get("id")
        norm_score = (row.get("vector_score", 0) / max_vec) * vector_weight
        if eid in merged:
            merged[eid]["hybrid_score"] += norm_score
            merged[eid]["match_source"] = "hybrid"
        else:
            row["hybrid_score"] = norm_score
            row["match_source"] = "vector"
            row["id"] = eid
            merged[eid] = row

    # Beyond-window candidates: single best contribution, never summed.
    extras: dict[int, dict] = {}
    for rows, key, top, weight, source in (
        (fts_results[top_k:], "fts_score", max_fts, fts_weight, "fts"),
        (vec_results[top_k:], "vector_score", max_vec, vector_weight, "vector"),
    ):
        for row in rows:
            eid = row.get("entry_id") or row.get("id")
            score = (row.get(key, 0) / top) * weight
            if eid in merged or (eid in extras and extras[eid]["hybrid_score"] >= score):
                continue
            row["hybrid_score"], row["match_source"], row["id"] = score, source, eid
            extras[eid] = row

    if not slug:
        query_words = set(_WORD_RE.findall(query.lower()))
        if query_words:
            for row in (*merged.values(), *extras.values()):
                boost = _source_relevance_boost(query_words, row.get("slug", ""))
                if boost > 0:
                    row["hybrid_score"] += boost

    by_score = lambda r: r["hybrid_score"]  # noqa: E731
    ranked = sorted(merged.values(), key=by_score, reverse=True)
    ranked += sorted(extras.values(), key=by_score, reverse=True)
    if grouped:
        ranked = collapse_faq_duplicates(db, ranked, slug=slug, score_key="hybrid_score")
    return ranked[:top_k]


def _faq_ref_rows(db: Database, ref: str, slug: str | None) -> list[dict]:
    """A FAQ ref (current or retired) answers with its group, canonical displayed."""
    rows = faq_rows_for_ref(db, ref, slug=slug)
    for row in rows:
        row["hybrid_score"] = 1.0
        row["match_source"] = "exact"
    rows = collapse_faq_duplicates(db, rows, slug=slug, score_key="hybrid_score")
    for row in rows:
        if row["entry_ref"] != ref:
            row["resolved_from_alias"] = ref
    return rows
