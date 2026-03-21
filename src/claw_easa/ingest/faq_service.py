from __future__ import annotations

import logging

from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.faq_parser import FAQRegulationsRootParser, FAQIndexParser, FAQDetailParser
from claw_easa.ingest.faq_sources import REGULATIONS_FAQ_ROOT_URL, FAQDomain, make_faq_domain
from claw_easa.ingest.repository import (
    link_faq_ref,
    upsert_faq_entry,
    upsert_source_document_from_values,
)

log = logging.getLogger(__name__)


def _open_db() -> Database:
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    return db


def discover_faq_domains() -> list[FAQDomain]:
    import requests

    resp = requests.get(REGULATIONS_FAQ_ROOT_URL, timeout=30)
    resp.raise_for_status()

    parser = FAQRegulationsRootParser(REGULATIONS_FAQ_ROOT_URL)
    parser.feed(resp.text)

    return [make_faq_domain(d.slug, d.title, d.url) for d in parser.domains]


def ingest_faq_domain(domain: FAQDomain) -> dict:
    import requests

    db = _open_db()
    try:
        doc_id = upsert_source_document_from_values(
            db,
            slug=domain.source_doc_slug,
            source_family="faq",
            title=f"FAQ — {domain.title}",
            page_url=domain.url,
        )

        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT OR IGNORE INTO regulation_parts "
                    "(document_id, part_code, annex, title, sort_order) "
                    "VALUES (?, 'FAQ', '', ?, 0)",
                    (doc_id, domain.title),
                )
                cur.execute(
                    "SELECT id FROM regulation_parts "
                    "WHERE document_id = ? AND part_code = 'FAQ'",
                    (doc_id,),
                )
                part_id = cur.fetchone()["id"]

                cur.execute(
                    "INSERT OR IGNORE INTO regulation_subparts "
                    "(part_id, subpart_code, title, sort_order) "
                    "VALUES (?, 'FAQ', ?, 0)",
                    (part_id, domain.title),
                )
                cur.execute(
                    "SELECT id FROM regulation_subparts "
                    "WHERE part_id = ? AND subpart_code = 'FAQ'",
                    (part_id,),
                )
                subpart_id = cur.fetchone()["id"]

                cur.execute(
                    "INSERT OR IGNORE INTO regulation_sections "
                    "(subpart_id, section_code, title, sort_order) "
                    "VALUES (?, NULL, ?, 0)",
                    (subpart_id, domain.title),
                )
                cur.execute(
                    "SELECT id FROM regulation_sections WHERE subpart_id = ?",
                    (subpart_id,),
                )
                section_id = cur.fetchone()["id"]
            conn.commit()

        resp = requests.get(domain.url, timeout=30)
        resp.raise_for_status()

        index_parser = FAQIndexParser(domain.url)
        index_parser.feed(resp.text)

        detail_parser = FAQDetailParser()
        ingested = 0

        for candidate in index_parser.candidates:
            try:
                detail_resp = requests.get(candidate.url, timeout=30)
                detail_resp.raise_for_status()
                detail_parser.feed(detail_resp.text)

                entry = detail_parser.build(
                    candidate.url,
                    category=candidate.category,
                )

                entry_ref = f"FAQ-{domain.slug}-{ingested + 1:03d}"
                entry_id = upsert_faq_entry(
                    db,
                    document_id=doc_id,
                    part_id=part_id,
                    subpart_id=subpart_id,
                    section_id=section_id,
                    entry_ref=entry_ref,
                    title=entry.question or candidate.question,
                    body_text=entry.answer_text,
                    source_url=entry.url,
                )

                for ref in entry.detected_refs:
                    link_faq_ref(db, entry_id, ref)

                ingested += 1
            except Exception:
                log.warning("Failed to ingest FAQ: %s", candidate.url, exc_info=True)

        return {"domain": domain.slug, "ingested": ingested}
    finally:
        db.close()
