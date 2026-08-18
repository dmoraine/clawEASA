from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from claw_easa.db.sqlite import Database
from claw_easa.ingest.regulations import Regulation
from claw_easa.references import (
    canonical_reference,
    like_escape,
    normalize_reference_text,
)

log = logging.getLogger(__name__)


def upsert_source_document_from_values(
    db: Database,
    slug: str,
    source_family: str,
    title: str,
    language: str = "en",
    page_url: str | None = None,
    source_url: str | None = None,
    revision: str | None = None,
) -> int:
    """Register or update a source document.

    *revision* is the dated revision EASA published for the ingested copy
    (e.g. ``'March 2026'``).  A caller that does not know it leaves the
    recorded revision untouched rather than erasing it.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM source_documents WHERE slug = ?", (slug,))
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE source_documents SET "
                    "source_family = ?, title = ?, language = ?, "
                    "page_url = ?, source_url = ?, "
                    "revision = COALESCE(?, revision), "
                    "updated_at = datetime('now') "
                    "WHERE slug = ?",
                    (source_family, title, language, page_url, source_url,
                     revision, slug),
                )
                conn.commit()
                return existing["id"]

            cur.execute(
                "INSERT INTO source_documents "
                "(slug, source_family, title, language, page_url, source_url, revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slug, source_family, title, language, page_url, source_url, revision),
            )
            doc_id = cur.lastrowid
            conn.commit()
            return doc_id


def record_source_regulations(
    db: Database, document_id: int, regulations: Sequence[Regulation],
) -> None:
    """Declare which regulations *document_id* is built from.

    The declaration is replaced wholesale, so re-registering a source cannot
    leave behind a regulation it no longer publishes.  Recording an empty
    sequence therefore clears the declaration, which is how a source with no
    regulation provenance is expressed.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM source_regulations WHERE document_id = ?",
                (document_id,),
            )
            for sort_order, regulation in enumerate(regulations):
                cur.execute(
                    "INSERT INTO source_regulations "
                    "(document_id, identifier, title, kind, part_codes, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        document_id, regulation.identifier, regulation.title,
                        regulation.kind, json.dumps(list(regulation.part_codes)),
                        sort_order,
                    ),
                )
        conn.commit()


def list_source_regulations(db: Database, slug: str) -> list[dict]:
    """The regulations recorded against *slug*, in the declared order."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sr.identifier, sr.title, sr.kind, sr.part_codes, "
                "       sr.sort_order, sr.document_id "
                "FROM source_regulations sr "
                "JOIN source_documents sd ON sd.id = sr.document_id "
                "WHERE sd.slug = ? "
                "ORDER BY sr.sort_order, sr.id",
                (slug,),
            )
            rows = cur.fetchall()

    for row in rows:
        row["part_codes"] = tuple(json.loads(row["part_codes"]))
    return rows


def document_regulations(db: Database, document_id: int) -> tuple[Regulation, ...]:
    """The declared regulations of *document_id*, ready to attribute parts."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT identifier, title, kind, part_codes FROM source_regulations "
                "WHERE document_id = ? ORDER BY sort_order, id",
                (document_id,),
            )
            rows = cur.fetchall()

    return tuple(
        Regulation(
            identifier=row["identifier"],
            title=row["title"],
            kind=row["kind"],
            part_codes=tuple(json.loads(row["part_codes"])),
        )
        for row in rows
    )


def parts_by_regulation(db: Database, identifier: str) -> list[dict]:
    """Every part attributed to *identifier*, with its source and size.

    Answers "what does this regulation state in the corpus?" — the question a
    citation has to survive when one document consolidates two regulations.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rp.part_code, rp.annex, rp.title, rp.regulation, "
                "       sd.slug, sd.title AS document_title, "
                "       (SELECT COUNT(*) FROM regulation_entries re "
                "        WHERE re.part_id = rp.id) AS entry_count "
                "FROM regulation_parts rp "
                "JOIN source_documents sd ON sd.id = rp.document_id "
                "WHERE rp.regulation = ? "
                "ORDER BY sd.slug, rp.sort_order, rp.id",
                (identifier,),
            )
            return cur.fetchall()


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
    """Whether *entry_ref* is in the corpus.

    Uses the same canonical matching as ``lookup_reference``: strict ref-only
    answering must not declare a rule out of corpus just because it was
    stored with its heading title attached.
    """
    wanted = normalize_reference_text(entry_ref)
    row = db.fetch_one(
        "SELECT 1 FROM regulation_entries WHERE entry_ref = ?", (wanted,)
    )
    if row is not None:
        return True

    canonical = canonical_reference(wanted)
    if not canonical:
        return False

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT entry_ref FROM regulation_entries "
                "WHERE entry_ref LIKE ? ESCAPE '\\'",
                (f"{like_escape(canonical)}%",),
            )
            return any(
                canonical_reference(r["entry_ref"]) == canonical
                for r in cur.fetchall()
            )
