from __future__ import annotations

import logging

import numpy as np

from claw_easa.config import get_settings
from claw_easa.db.sqlite import Database
from claw_easa.retrieval.embedder import encode_texts
from claw_easa.retrieval.faiss_store import FAISSStore

log = logging.getLogger(__name__)


def vector_search(
    db: Database, query: str, top_k: int = 10, *, slug: str | None = None,
) -> list[dict]:
    settings = get_settings()
    store = FAISSStore(settings.faiss_index_path, settings.embedding_dimensions)
    store.load()

    vectors = encode_texts([query], settings.embedding_model)
    query_vec = np.array(vectors[0], dtype=np.float32)

    fetch_k = top_k * 3 if slug else top_k
    results = store.search(query_vec, top_k=fetch_k)
    if not results:
        return []

    positions = [pos for pos, _ in results]
    score_map = {pos: score for pos, score in results}

    placeholders = ",".join("?" * len(positions))
    slug_clause = " AND d.slug = ?" if slug else ""
    sql = (
        f"SELECT fm.faiss_position, ec.id AS chunk_id, ec.chunk_text, "
        f"       ec.breadcrumbs_text, ec.entry_id, "
        f"       e.entry_ref, e.entry_type, e.title, e.body_text, "
        f"       d.slug "
        f"FROM faiss_mapping fm "
        f"JOIN entry_chunks ec ON ec.id = fm.chunk_id "
        f"JOIN regulation_entries e ON e.id = ec.entry_id "
        f"JOIN source_documents d ON d.id = e.document_id "
        f"WHERE fm.faiss_position IN ({placeholders}){slug_clause}"
    )
    params = tuple(positions) + ((slug,) if slug else ())

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    for row in rows:
        row["vector_score"] = score_map.get(row["faiss_position"], 0.0)

    rows.sort(key=lambda r: r["vector_score"], reverse=True)
    return rows[:top_k]
