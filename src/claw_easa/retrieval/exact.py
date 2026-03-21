from __future__ import annotations

import logging

from claw_easa.db.sqlite import Database
from claw_easa.retrieval.fts_compat import to_fts5_query

log = logging.getLogger(__name__)


def lookup_reference(db: Database, ref: str) -> list[dict]:
    sql = (
        "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
        "       e.body_markdown, d.slug, "
        "       p.part_code, sp.subpart_code "
        "FROM regulation_entries e "
        "JOIN source_documents d ON d.id = e.document_id "
        "JOIN regulation_parts p ON p.id = e.part_id "
        "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
        "WHERE e.entry_ref = ? "
        "ORDER BY e.entry_type"
    )
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ref,))
            return cur.fetchall()


def search_references(db: Database, query: str, limit: int = 20) -> list[dict]:
    fts = to_fts5_query(query)
    results: list[dict] = []

    if fts.has_terms:
        fts_sql = (
            "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
            "       d.slug, p.part_code, sp.subpart_code, "
            "       -fts.rank AS fts_score "
            "FROM entries_fts fts "
            "JOIN regulation_entries e ON e.id = fts.rowid "
            "JOIN source_documents d ON d.id = e.document_id "
            "JOIN regulation_parts p ON p.id = e.part_id "
            "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
            "WHERE entries_fts MATCH ? "
            "ORDER BY fts.rank "
            "LIMIT ?"
        )
        with db.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(fts_sql, (fts.match_expr, limit))
                    results = cur.fetchall()
                except Exception:
                    log.warning("FTS query failed for: %s", fts.match_expr)

    if not results:
        like_pattern = f"%{query}%"
        like_sql = (
            "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
            "       d.slug, p.part_code, sp.subpart_code, "
            "       1.0 AS fts_score "
            "FROM regulation_entries e "
            "JOIN source_documents d ON d.id = e.document_id "
            "JOIN regulation_parts p ON p.id = e.part_id "
            "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
            "WHERE e.entry_ref LIKE ? OR e.title LIKE ? OR e.body_text LIKE ? "
            "LIMIT ?"
        )
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(like_sql, (like_pattern, like_pattern, like_pattern, limit))
                results = cur.fetchall()

    return results
