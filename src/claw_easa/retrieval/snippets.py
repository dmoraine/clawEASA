from __future__ import annotations

import logging
import re

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

_ENRICH_WORD_RE = re.compile(r'[a-z]{3,}')

_SUBREF_DETECT_RE = re.compile(
    r'^((?:AMC|GM|CS)\d*\s+[A-Z][A-Za-z0-9._]+(?:\([a-z]\))?\s+\S.{2,100})\s*$',
)


def search_snippets(
    db: Database, query: str, limit: int = 10, *, slug: str | None = None,
) -> list[dict]:
    fts = to_fts5_query(query)
    slug_clause = "AND d.slug = ? " if slug else ""

    results: list[dict] = []

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
                except Exception:
                    log.warning("FTS snippet query failed for: %s", fts.match_expr)

    if not results:
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
                results = cur.fetchall()

    return _enrich_with_chunks(db, results, query)


def _enrich_with_chunks(
    db: Database, rows: list[dict], query: str,
) -> list[dict]:
    """Post-process snippet results: prefer chunk-level text when available."""
    if not rows:
        return rows

    entry_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(entry_ids))

    sql = (
        f"SELECT ec.entry_id, ec.chunk_kind, ec.chunk_index, ec.chunk_text "
        f"FROM entry_chunks ec "
        f"WHERE ec.entry_id IN ({placeholders}) "
        f"AND ec.chunk_kind != 'whole' "
        f"ORDER BY ec.entry_id, ec.chunk_index"
    )

    try:
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(entry_ids))
                chunks = cur.fetchall()
    except Exception:
        return rows

    if not chunks:
        return rows

    chunks_by_entry: dict[int, list[dict]] = {}
    for c in chunks:
        chunks_by_entry.setdefault(c["entry_id"], []).append(c)

    query_words = set(_ENRICH_WORD_RE.findall(query.lower()))
    if not query_words:
        return rows

    for row in rows:
        entry_chunks = chunks_by_entry.get(row["id"], [])
        if not entry_chunks:
            continue

        best_chunk = None
        best_score = 0

        for chunk in entry_chunks:
            chunk_lower = chunk["chunk_text"].lower()
            score = sum(1 for w in query_words if w in chunk_lower)
            if query.lower() in chunk_lower:
                score += len(query_words)
            if score > best_score:
                best_score = score
                best_chunk = chunk

        if best_chunk and best_score > 0:
            row["chunk_text"] = best_chunk["chunk_text"]
            row["chunk_kind"] = best_chunk["chunk_kind"]
            row["chunk_index"] = best_chunk["chunk_index"]
            if best_chunk["chunk_kind"] == "subheading":
                subref = _extract_subref(best_chunk["chunk_text"])
                if subref:
                    row["matched_subref"] = subref

    return rows


def _extract_subref(chunk_text: str) -> str | None:
    lines = chunk_text.strip().split("\n")
    if len(lines) < 2:
        return None
    for line in lines[1:3]:
        m = _SUBREF_DETECT_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return None
