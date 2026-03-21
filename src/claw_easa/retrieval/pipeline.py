from __future__ import annotations

import logging

from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.retrieval.indexing import RetrievalIndexer

log = logging.getLogger(__name__)


def _open_db() -> Database:
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    return db


def build_index() -> dict:
    db = _open_db()
    try:
        indexer = RetrievalIndexer(db)
        chunk_count = indexer.rebuild_chunks()
        embed_count = indexer.store_embeddings()
        return {"chunks": chunk_count, "embeddings": embed_count}
    finally:
        db.close()


def vector_lookup(query: str, top_k: int = 10) -> list[dict]:
    from claw_easa.retrieval.vector import vector_search

    db = _open_db()
    try:
        return vector_search(db, query, top_k=top_k)
    finally:
        db.close()
