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
    raw_k = top_k * overfetch_factor() if grouped else top_k

    fts_results = search_references(db, query, limit=raw_k, slug=slug, group_faq=False)

    try:
        from claw_easa.retrieval.vector import vector_search

        vec_results = vector_search(db, query, top_k=raw_k, slug=slug)
    except FileNotFoundError:
        log.warning("FAISS index not found — falling back to FTS only")
        vec_results = []

    merged: dict[int, dict] = {}

    if fts_results:
        max_fts = max((r.get("fts_score", 0) for r in fts_results), default=1) or 1
        for row in fts_results:
            eid = row["id"]
            norm_score = (row.get("fts_score", 0) / max_fts) * fts_weight
            row["hybrid_score"] = norm_score
            row["match_source"] = "fts"
            merged[eid] = row

    if vec_results:
        max_vec = max((r.get("vector_score", 0) for r in vec_results), default=1) or 1
        for row in vec_results:
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

    if not slug:
        query_words = set(_WORD_RE.findall(query.lower()))
        if query_words:
            for row in merged.values():
                boost = _source_relevance_boost(query_words, row.get("slug", ""))
                if boost > 0:
                    row["hybrid_score"] += boost

    ranked = sorted(merged.values(), key=lambda r: r["hybrid_score"], reverse=True)
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
