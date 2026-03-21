"""FAQ ingestion service.

Discovers FAQ domains from the EASA website and ingests individual
Q&A pairs into the regulation database.

Use ``ingest_all_faqs()`` to crawl every FAQ sub-page reachable from
the regulations root.  Each sub-page becomes its own source document.
"""
from __future__ import annotations

import logging
import time

from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.ingest.faq_parser import parse_faq_root_page, parse_faq_page, FAQDomainLink
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


_AGGREGATION_SLUGS = frozenset({"regulations", "faq", "faqs", "website"})


def discover_faq_domains() -> list[FAQDomain]:
    """Discover FAQ sub-domains from the EASA regulations FAQ index.

    Aggregation pages (like ``/regulations`` itself) are excluded
    because they duplicate content from their children.
    """
    from claw_easa.ingest import http

    resp = http.get(REGULATIONS_FAQ_ROOT_URL)

    links = parse_faq_root_page(resp.text, REGULATIONS_FAQ_ROOT_URL)
    return [
        make_faq_domain(d.slug, d.title, d.url)
        for d in links
        if d.slug not in _AGGREGATION_SLUGS
    ]


def ingest_faq_domain(domain: FAQDomain) -> dict:
    from claw_easa.ingest import http

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

        resp = http.get(domain.url)
        faq_items = parse_faq_page(resp.text)

        ingested = 0
        for item in faq_items:
            try:
                entry_ref = f"FAQ-{domain.slug}-{ingested + 1:03d}"
                entry_id = upsert_faq_entry(
                    db,
                    document_id=doc_id,
                    part_id=part_id,
                    subpart_id=subpart_id,
                    section_id=section_id,
                    entry_ref=entry_ref,
                    title=item.question,
                    body_text=item.answer_text,
                    source_url=domain.url,
                )

                for ref in item.detected_refs:
                    link_faq_ref(db, entry_id, ref)

                ingested += 1
            except Exception:
                log.warning("Failed to ingest FAQ: %s", item.question[:60], exc_info=True)

        return {"domain": domain.slug, "ingested": ingested}
    finally:
        db.close()


def ingest_all_faqs(*, delay: float = 1.0, progress_cb=None) -> list[dict]:
    """Discover and ingest all FAQ domains from the regulations root.

    Iterates over every sub-page linked from the EASA regulations FAQ
    index, fetches FAQ items, and persists them.

    ``delay`` seconds are waited between HTTP requests to avoid
    rate-limiting.  ``progress_cb``, if provided, is called with
    ``(domain_slug, domain_count, total_domains)`` after each domain.
    """
    domains = discover_faq_domains()
    results: list[dict] = []

    for i, domain in enumerate(domains, 1):
        if progress_cb:
            progress_cb(domain.slug, i, len(domains))
        try:
            result = ingest_faq_domain(domain)
            results.append(result)
            if result["ingested"] > 0:
                log.info(
                    "[%d/%d] %s: %d FAQs",
                    i, len(domains), domain.slug, result["ingested"],
                )
            else:
                log.debug("[%d/%d] %s: 0 FAQs (skipped)", i, len(domains), domain.slug)
        except Exception:
            log.warning("Failed domain %s", domain.slug, exc_info=True)
            results.append({"domain": domain.slug, "ingested": 0, "error": True})

        if delay and i < len(domains):
            time.sleep(delay)

    return results
