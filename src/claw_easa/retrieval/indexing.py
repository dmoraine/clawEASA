from __future__ import annotations

import logging

import numpy as np

from claw_easa.config import get_settings
from claw_easa.db.sqlite import Database
from claw_easa.retrieval.chunking import build_list_item_chunks, build_whole_entry_chunk
from claw_easa.retrieval.embedder import encode_texts
from claw_easa.retrieval.faiss_store import FAISSStore

log = logging.getLogger(__name__)


class RetrievalIndexer:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.settings = get_settings()

    def rebuild_chunks(self) -> int:
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM faiss_mapping")
                cur.execute("DELETE FROM entry_chunks")
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
                    "       e.document_id, d.slug, "
                    "       p.part_code, sp.subpart_code, s.title AS section_title "
                    "FROM regulation_entries e "
                    "JOIN source_documents d ON d.id = e.document_id "
                    "JOIN regulation_parts p ON p.id = e.part_id "
                    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
                    "JOIN regulation_sections s ON s.id = e.section_id "
                    "ORDER BY e.id"
                )
                entries = cur.fetchall()

            chunk_count = 0
            item_chunk_count = 0
            insert_sql = (
                "INSERT INTO entry_chunks "
                "(entry_id, chunk_index, chunk_kind, breadcrumbs_text, "
                " chunk_text, token_estimate) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            with conn.cursor() as cur:
                for entry in entries:
                    whole = build_whole_entry_chunk(entry)
                    cur.execute(insert_sql, (
                        whole["entry_id"], whole["chunk_index"],
                        whole["chunk_kind"], whole["breadcrumbs_text"],
                        whole["chunk_text"], whole["token_estimate"],
                    ))
                    chunk_count += 1

                    for item in build_list_item_chunks(entry):
                        cur.execute(insert_sql, (
                            item["entry_id"], item["chunk_index"],
                            item["chunk_kind"], item["breadcrumbs_text"],
                            item["chunk_text"], item["token_estimate"],
                        ))
                        chunk_count += 1
                        item_chunk_count += 1
            conn.commit()

        log.info(
            "Built %d chunks from %d entries (%d list-item sub-chunks)",
            chunk_count, len(entries), item_chunk_count,
        )
        return chunk_count

    def store_embeddings(self) -> int:
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, chunk_text FROM entry_chunks ORDER BY id")
                chunks = cur.fetchall()

        if not chunks:
            log.warning("No chunks to embed")
            return 0

        texts = [c["chunk_text"] for c in chunks]
        chunk_ids = [c["id"] for c in chunks]

        log.info("Encoding %d chunks with %s", len(texts), self.settings.embedding_model)
        vectors = encode_texts(texts, self.settings.embedding_model)
        matrix = np.array(vectors, dtype=np.float32)

        store = FAISSStore(self.settings.faiss_index_path, self.settings.embedding_dimensions)
        store.build(matrix)
        store.save()

        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM faiss_mapping")
                for pos, cid in enumerate(chunk_ids):
                    cur.execute(
                        "INSERT INTO faiss_mapping (faiss_position, chunk_id) VALUES (?, ?)",
                        (pos, cid),
                    )
            conn.commit()

        log.info("Stored %d embeddings in FAISS index", len(chunk_ids))
        return len(chunk_ids)
