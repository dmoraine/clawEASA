from __future__ import annotations

import logging

from claw_easa.db.sqlite import Database

log = logging.getLogger(__name__)


def upsert_source_document_from_values(
    db: Database,
    slug: str,
    source_family: str,
    title: str,
    language: str = "en",
    page_url: str | None = None,
    source_url: str | None = None,
) -> int:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM source_documents WHERE slug = ?", (slug,))
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE source_documents SET "
                    "source_family = ?, title = ?, language = ?, "
                    "page_url = ?, source_url = ?, updated_at = datetime('now') "
                    "WHERE slug = ?",
                    (source_family, title, language, page_url, source_url, slug),
                )
                conn.commit()
                return existing["id"]

            cur.execute(
                "INSERT INTO source_documents "
                "(slug, source_family, title, language, page_url, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (slug, source_family, title, language, page_url, source_url),
            )
            doc_id = cur.lastrowid
            conn.commit()
            return doc_id


def record_download(
    db: Database,
    document_id: int,
    checksum: str | None = None,
    local_path: str | None = None,
    download_url: str | None = None,
    file_kind: str = "primary",
) -> int:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_files "
                "(document_id, file_kind, checksum, local_path, download_url) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, file_kind, checksum, local_path, download_url),
            )
            file_id = cur.lastrowid
            cur.execute(
                "UPDATE source_documents SET status = 'fetched', "
                "updated_at = datetime('now') WHERE id = ?",
                (document_id,),
            )
            conn.commit()
            return file_id


def upsert_faq_entry(
    db: Database,
    document_id: int,
    part_id: int,
    subpart_id: int,
    section_id: int,
    entry_ref: str,
    title: str,
    body_text: str,
    source_url: str | None = None,
) -> int:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM regulation_entries "
                "WHERE entry_ref = ? AND document_id = ? AND entry_type = 'FAQ'",
                (entry_ref, document_id),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE regulation_entries SET "
                    "title = ?, body_text = ?, body_markdown = ?, "
                    "source_url = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (title, body_text, body_text, source_url, existing["id"]),
                )
                conn.commit()
                return existing["id"]

            cur.execute(
                "INSERT INTO regulation_entries "
                "(document_id, part_id, subpart_id, section_id, "
                " entry_ref, entry_type, title, body_markdown, body_text, "
                " source_url, sort_order) "
                "VALUES (?, ?, ?, ?, ?, 'FAQ', ?, ?, ?, ?, 0)",
                (
                    document_id, part_id, subpart_id, section_id,
                    entry_ref, title, body_text, body_text, source_url,
                ),
            )
            entry_id = cur.lastrowid
            conn.commit()
            return entry_id


def link_faq_ref(db: Database, faq_entry_id: int, target_ref: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO faq_regulation_refs (faq_entry_id, target_ref) "
                "VALUES (?, ?)",
                (faq_entry_id, target_ref),
            )
        conn.commit()


def get_document_by_slug(db: Database, slug: str) -> dict | None:
    return db.fetch_one(
        "SELECT * FROM source_documents WHERE slug = ?", (slug,)
    )


def get_latest_source_file(db: Database, document_id: int) -> dict | None:
    return db.fetch_one(
        "SELECT * FROM source_files WHERE document_id = ? "
        "ORDER BY downloaded_at DESC LIMIT 1",
        (document_id,),
    )


def list_documents(db: Database) -> list[dict]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, source_family, title, status, "
                "       created_at, updated_at, parsed_at "
                "FROM source_documents ORDER BY slug"
            )
            return cur.fetchall()


def reference_exists(db: Database, entry_ref: str) -> bool:
    row = db.fetch_one(
        "SELECT 1 FROM regulation_entries WHERE entry_ref = ?", (entry_ref,)
    )
    return row is not None
