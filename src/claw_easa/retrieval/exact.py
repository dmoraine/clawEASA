from __future__ import annotations

import logging
from collections.abc import Callable

from claw_easa.db.sqlite import Database
from claw_easa.references import (
    canonical_reference,
    is_reference_query,
    like_escape,
    normalize_reference_text,
)
from claw_easa.retrieval.faq_groups import (
    annotate_faq_rows,
    collapse_faq_duplicates,
    faq_rows_for_ref,
    grouping_active,
    overfetch_factor,
)
from claw_easa.retrieval.fts_compat import to_fts5_query

log = logging.getLogger(__name__)

_ENTRY_COLUMNS = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
    "       e.body_markdown, d.slug, "
    "       p.part_code, sp.subpart_code "
    "FROM regulation_entries e "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
)

# What a reference search projects.  Beyond the entry itself it selects the
# provenance a result is cited from — the URL the source published for the
# entry, the locator saying where in the document it was parsed from, and the
# document's own URLs, slug and revision.  All three match paths below select
# it identically, so a result carries the same provenance however it matched.
_SEARCH_COLUMNS = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
    "       e.source_url AS entry_url, e.source_locator AS entry_locator, "
    "       d.slug, d.source_url AS document_url, "
    "       d.page_url AS document_page_url, d.revision AS document_revision, "
    "       p.part_code, sp.subpart_code"
)


def resolve_reference(
    ref: str, run: Callable[[str, tuple], list[dict]],
) -> list[dict]:
    """Resolve an exact regulation reference through *run*.

    *run* executes one WHERE fragment over ``regulation_entries`` aliased
    ``e`` and returns the rows it selected, so each caller decides which
    columns it needs and how the query is scoped.  What a reference *resolves
    to* is decided here and only here: the five-line extract and the detailed
    lookup must not disagree about which provision was asked for.

    Matching is exact first, then falls back to canonical matching so that
    entries persisted with the heading title still attached —
    ``'M.A.201 Responsibilities'`` — answer a lookup of ``M.A.201``.  The
    fallback re-checks every candidate against the canonical form, because the
    LIKE prefix it searches with also matches longer references:
    ``ORO.FTL.11`` must not be answered with ``ORO.FTL.110``.

    Between the two, a retired FAQ ref (``faq_aliases``, migration 005)
    answers with the entry that carried it; such rows are marked
    ``resolved_from_alias`` with the requested ref.
    """
    wanted = normalize_reference_text(ref)
    rows = run("e.entry_ref = ?", (wanted,))
    if rows:
        return rows

    rows = run("e.id IN (SELECT entry_id FROM faq_aliases WHERE alias_ref = ?)", (wanted,))
    if rows:
        for row in rows:
            row["resolved_from_alias"] = wanted
        return rows

    canonical = canonical_reference(wanted)
    if not canonical:
        return []

    return [
        row for row in run(
            "e.entry_ref LIKE ? ESCAPE '\\'", (f"{like_escape(canonical)}%",),
        )
        if canonical_reference(row["entry_ref"]) == canonical
    ]


def lookup_reference(
    db: Database, ref: str, *, slug: str | None = None,
) -> list[dict]:
    """Resolve an exact regulation reference, optionally within one corpus."""
    scope = "AND d.slug = ? " if slug is not None else ""

    with db.connection() as conn:
        with conn.cursor() as cur:
            def run(where: str, params: tuple) -> list[dict]:
                cur.execute(
                    _ENTRY_COLUMNS + f"WHERE {where} {scope}ORDER BY e.entry_type",
                    (*params, slug) if slug is not None else params,
                )
                return cur.fetchall()

            rows = resolve_reference(ref, run)
    return annotate_faq_rows(db, rows)


def _search_by_reference(
    db: Database, query: str, limit: int, slug: str | None,
) -> list[dict]:
    """Match a bare reference against ``entry_ref`` only.

    A reference query has to be answered by references.  Handing
    ``IS.I.OR.200`` to FTS degrades it to the tokens ``IS I OR 200``, which
    matches any entry numbered 200 — an answer about a completely different
    regulation, phrased as if it were about information security.
    """
    pattern = f"%{like_escape(normalize_reference_text(query))}%"
    sql = (
        f"{_SEARCH_COLUMNS}, 1.0 AS fts_score "
        "FROM regulation_entries e "
        "JOIN source_documents d ON d.id = e.document_id "
        "JOIN regulation_parts p ON p.id = e.part_id "
        "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
        "WHERE e.entry_ref LIKE ? ESCAPE '\\' "
        + ("AND d.slug = ? " if slug else "")
        + "ORDER BY e.entry_ref LIMIT ?"
    )
    params: tuple = (pattern, slug, limit) if slug else (pattern, limit)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _search_by_faq_ref(db: Database, query: str, limit: int, slug: str | None) -> list[dict]:
    """A current FAQ ref or a retired alias answers with that entry (score 1.0)."""
    rows = faq_rows_for_ref(db, query, slug=slug)[:limit]
    for row in rows:
        row.pop("body_markdown", None)
        row["fts_score"] = 1.0
    return annotate_faq_rows(db, rows)


def search_references(
    db: Database, query: str, limit: int = 20, *, slug: str | None = None,
    group_faq: bool = True,
) -> list[dict]:
    """Reference search; FAQ copies collapse to one row per content group.

    Grouping (and the FAQ-ref pre-step) only applies once migration 005
    populated the group tables; *group_faq* False returns raw rows, as
    ``hybrid_search`` needs before its own merge.
    """
    if is_reference_query(query):
        return _search_by_reference(db, query, limit, slug)

    grouped = group_faq and grouping_active(db)
    if grouped:
        exact = _search_by_faq_ref(db, query, limit, slug)
        if exact:
            return exact
    final_limit, limit = limit, limit * overfetch_factor() if grouped else limit

    fts = to_fts5_query(query)
    results: list[dict] = []

    slug_clause = "AND d.slug = ? " if slug else ""

    if fts.has_terms:
        fts_sql = (
            f"{_SEARCH_COLUMNS}, -fts.rank AS fts_score "
            "FROM entries_fts fts "
            "JOIN regulation_entries e ON e.id = fts.rowid "
            "JOIN source_documents d ON d.id = e.document_id "
            "JOIN regulation_parts p ON p.id = e.part_id "
            "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
            f"WHERE entries_fts MATCH ? {slug_clause}"
            "ORDER BY fts.rank "
            "LIMIT ?"
        )
        params: tuple = (fts.match_expr, slug, limit) if slug else (fts.match_expr, limit)
        with db.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(fts_sql, params)
                    results = cur.fetchall()
                except Exception:
                    log.warning("FTS query failed for: %s", fts.match_expr)

    if not results:
        like_pattern = f"%{query}%"
        like_sql = (
            f"{_SEARCH_COLUMNS}, 1.0 AS fts_score "
            "FROM regulation_entries e "
            "JOIN source_documents d ON d.id = e.document_id "
            "JOIN regulation_parts p ON p.id = e.part_id "
            "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
            f"WHERE (e.entry_ref LIKE ? OR e.title LIKE ? OR e.body_text LIKE ?) {slug_clause}"
            "LIMIT ?"
        )
        params = (like_pattern, like_pattern, like_pattern, slug, limit) if slug else (like_pattern, like_pattern, like_pattern, limit)
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(like_sql, params)
                results = cur.fetchall()

    if grouped:
        results = collapse_faq_duplicates(db, results, slug=slug, score_key="fts_score")
    return results[:final_limit]
