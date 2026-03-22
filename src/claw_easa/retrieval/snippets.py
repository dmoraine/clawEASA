from __future__ import annotations

import logging

from claw_easa.db.sqlite import Database
from claw_easa.retrieval.fts_compat import to_fts5_query

log = logging.getLogger(__name__)

_BASE_SELECT = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
    "       d.slug, p.part_code, sp.subpart_code"
)

_BASE_JOINS = (
    " FROM entries_fts fts "
    "JOIN regulation_entries e ON e.id = fts.rowid "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
)

_BASE_LIKE_JOINS = (
    " FROM regulation_entries e "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
)


def search_snippets(
    db: Database, query: str, limit: int = 10, *, slug: str | None = None,
) -> list[dict]:
    fts = to_fts5_query(query)
    slug_clause = "AND d.slug = ? " if slug else ""

    if fts.has_terms:
        sql = (
            f"{_BASE_SELECT}, -fts.rank AS fts_score"
            f"{_BASE_JOINS}"
            f"WHERE entries_fts MATCH ? {slug_clause}"
            "ORDER BY fts.rank LIMIT ?"
        )
        params: tuple = (fts.match_expr, slug, limit) if slug else (fts.match_expr, limit)
        with db.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, params)
                    results = cur.fetchall()
                    if results:
                        return results
                except Exception:
                    log.warning("FTS snippet query failed for: %s", fts.match_expr)

    like_pattern = f"%{query}%"
    sql = (
        f"{_BASE_SELECT}, 1.0 AS fts_score"
        f"{_BASE_LIKE_JOINS}"
        f"WHERE (e.entry_ref LIKE ? OR e.title LIKE ? OR e.body_text LIKE ?) {slug_clause}"
        "LIMIT ?"
    )
    params = (like_pattern, like_pattern, like_pattern, slug, limit) if slug else (like_pattern, like_pattern, like_pattern, limit)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
