from __future__ import annotations

import logging

from claw_easa.db.sqlite import Database
from claw_easa.retrieval.fts_compat import to_fts5_query
from claw_easa.retrieval.queries import SNIPPET_SEARCH_FTS_SQL, SNIPPET_SEARCH_LIKE_SQL

log = logging.getLogger(__name__)


def search_snippets(db: Database, query: str, limit: int = 10) -> list[dict]:
    fts = to_fts5_query(query)

    if fts.has_terms:
        with db.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(SNIPPET_SEARCH_FTS_SQL, (fts.match_expr, limit))
                    results = cur.fetchall()
                    if results:
                        return results
                except Exception:
                    log.warning("FTS snippet query failed for: %s", fts.match_expr)

    like_pattern = f"%{query}%"
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                SNIPPET_SEARCH_LIKE_SQL,
                (like_pattern, like_pattern, like_pattern, limit),
            )
            return cur.fetchall()
