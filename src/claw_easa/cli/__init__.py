from __future__ import annotations

import click
import logging
from pathlib import Path

from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sql import LIST_TABLES, HEALTHCHECK

log = logging.getLogger(__name__)


@click.group()
def main() -> None:
    """claw-easa — Query EASA Easy Access Rules."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@main.command("init")
def init_cmd() -> None:
    """Initialise the SQLite database and schema."""
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    click.echo(f"Database initialised at {db.db_path}")
    db.close()


def _fmt_size(nbytes: int) -> str:
    if nbytes >= 1_073_741_824:
        return f"{nbytes / 1_073_741_824:.1f} GB"
    if nbytes >= 1_048_576:
        return f"{nbytes / 1_048_576:.1f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes} B"


@main.command("status")
def status_cmd() -> None:
    """Show project status and corpus statistics."""
    import os
    from claw_easa.config import get_settings

    settings = get_settings()

    click.echo("=== clawEASA status ===\n")

    db_exists = settings.db_path.exists()
    faiss_exists = settings.faiss_index_path.exists()

    db_size = _fmt_size(os.path.getsize(settings.db_path)) if db_exists else "n/a"
    faiss_size = _fmt_size(os.path.getsize(settings.faiss_index_path)) if faiss_exists else "n/a"

    click.echo(f"Database:        {settings.db_path} ({db_size})")
    if faiss_exists:
        click.echo(f"FAISS index:     {settings.faiss_index_path} ({faiss_size})")
    else:
        click.echo("FAISS index:     not built — run 'claw-easa index build'")

    if not db_exists:
        click.echo("\n(database not created — run 'claw-easa init')")
        return

    db = Database()
    db.open()
    try:
        _status_corpus(db)
    except Exception:
        click.echo("\n(schema not initialised)")
    finally:
        db.close()


def _status_corpus(db: Database) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sd.id, sd.slug, sd.source_family, sd.status, sd.title, "
                "       COUNT(re.id) AS entry_count "
                "FROM source_documents sd "
                "LEFT JOIN regulation_parts rp ON rp.document_id = sd.id "
                "LEFT JOIN regulation_subparts rs ON rs.part_id = rp.id "
                "LEFT JOIN regulation_sections rsec ON rsec.subpart_id = rs.id "
                "LEFT JOIN regulation_entries re ON re.section_id = rsec.id "
                "GROUP BY sd.id ORDER BY sd.source_family, sd.slug"
            )
            docs = cur.fetchall()

            cur.execute(
                "SELECT entry_type, COUNT(*) AS cnt "
                "FROM regulation_entries GROUP BY entry_type ORDER BY cnt DESC"
            )
            type_counts = cur.fetchall()

            cur.execute(
                "SELECT chunk_kind, COUNT(*) AS cnt "
                "FROM entry_chunks GROUP BY chunk_kind"
            )
            chunk_counts = {r["chunk_kind"]: r["cnt"] for r in cur.fetchall()}

            total_chunks = sum(chunk_counts.values())

    ears = [d for d in docs if d["source_family"] != "faq"]
    faqs = [d for d in docs if d["source_family"] == "faq"]

    if ears:
        click.echo(f"\nEasy Access Rules ({len(ears)}):")
        for doc in ears:
            cnt = doc["entry_count"]
            info = f"{cnt} entries" if cnt else doc["status"]
            click.echo(f"  {doc['slug']:<35} {info:<15} {doc['title']}")

    if faqs:
        faq_with_content = sum(1 for d in faqs if d["entry_count"] > 0)
        total_faqs = sum(d["entry_count"] for d in faqs)
        click.echo(f"\nFAQ domains:         {faq_with_content} domains, {total_faqs} FAQs")

    if type_counts:
        click.echo("\nEntries by type:")
        parts = [f"{r['entry_type']:<6} {r['cnt']:>5}" for r in type_counts]
        for i in range(0, len(parts), 3):
            click.echo("  " + "    ".join(parts[i:i + 3]))

    if total_chunks:
        whole = chunk_counts.get("whole", 0)
        items = chunk_counts.get("list_item", 0)
        subheadings = chunk_counts.get("subheading", 0)
        parts = [f"{whole} whole", f"{items} list-item"]
        if subheadings:
            parts.append(f"{subheadings} subheading")
        click.echo(f"\nIndex:               {total_chunks} chunks ({' + '.join(parts)})")


# --- DB commands ---


@main.group("db")
def db_group() -> None:
    """Database management commands."""
    pass


@db_group.command("healthcheck")
def db_healthcheck_cmd() -> None:
    """Check database connectivity."""
    db = Database()
    db.open()
    try:
        row = db.fetch_one(HEALTHCHECK)
        click.echo("OK" if row else "FAIL")
    finally:
        db.close()


@db_group.command("list-tables")
def db_list_tables_cmd() -> None:
    """List database tables."""
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    try:
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(LIST_TABLES)
                for row in cur.fetchall():
                    click.echo(row["name"])
    finally:
        db.close()


# --- Ingest commands ---


@main.group("ingest")
def ingest_group() -> None:
    """Ingestion pipeline commands."""
    pass


@ingest_group.command("fetch")
@click.argument("slug")
@click.option("--url", default=None, help="Explicit download URL (skips catalog resolution)")
def ingest_fetch_cmd(slug: str, url: str | None) -> None:
    """Fetch a source document by slug.

    The download URL is resolved dynamically from the EASA catalog.
    Use --url to bypass catalog resolution and provide a direct link.
    """
    from claw_easa.ingest.service import fetch_source

    result = fetch_source(slug, url=url)
    click.echo(
        f"Fetched {slug}: document_id={result['document_id']}, "
        f"path={result['local_path']}"
    )


@ingest_group.command("diagnose")
@click.argument("slug")
def ingest_diagnose_cmd(slug: str) -> None:
    """Run coverage diagnostics on a parsed source.

    Cross-references the XML Table of Contents with parsed entries
    to detect missing articles, uncaptured headings, and empty bodies.
    """
    from claw_easa.ingest.repository import get_document_by_slug, get_latest_source_file
    from claw_easa.ingest.service import _open_db, _materialize_parse_path
    from claw_easa.ingest.diagnostics import coverage_report, format_report

    db = _open_db()
    try:
        doc = get_document_by_slug(db, slug)
        if not doc:
            click.echo(f"Document not found: {slug}", err=True)
            return

        source_file = get_latest_source_file(db, doc["id"])
        if not source_file:
            click.echo(f"No source file for: {slug}", err=True)
            return

        path = Path(source_file["local_path"])
        parse_path = _materialize_parse_path(path)
        report = coverage_report(parse_path, doc["title"])
        click.echo(format_report(report))
    finally:
        db.close()


@ingest_group.command("parse")
@click.argument("slug")
def ingest_parse_cmd(slug: str) -> None:
    """Parse a fetched source document."""
    from claw_easa.ingest.service import parse_source

    result = parse_source(slug)
    click.echo(
        f"Parsed {slug}: {result['entries']} entries "
        f"({result['parts']} parts, {result['subparts']} subparts, "
        f"{result['sections']} sections)"
    )
    if result["duplicate_entries_skipped"]:
        click.echo(f"  Duplicates skipped: {result['duplicate_entries_skipped']}")
    if result["empty_entries_skipped"]:
        click.echo(f"  Empty entries skipped: {result['empty_entries_skipped']}")


@ingest_group.command("faq-discover")
def ingest_faq_discover_cmd() -> None:
    """Discover available FAQ domains on the EASA website."""
    from claw_easa.ingest.faq_service import discover_faq_domains

    click.echo("Scanning EASA FAQ pages...")
    domains = discover_faq_domains()
    if not domains:
        click.echo("No FAQ domains found.")
        return
    click.echo(f"Found {len(domains)} FAQ domains:\n")
    for d in domains:
        click.echo(f"  {d.slug:<50} {d.title}")


@ingest_group.command("faq")
@click.argument("slug")
def ingest_faq_cmd(slug: str) -> None:
    """Ingest FAQs for a specific domain (e.g. 'air-operations').

    Use 'ingest faq-discover' to see available domains.
    """
    from claw_easa.ingest.faq_service import discover_faq_domains, ingest_faq_domain

    domains = discover_faq_domains()
    domain = next((d for d in domains if d.slug == slug), None)
    if not domain:
        click.echo(f"FAQ domain '{slug}' not found. Run 'ingest faq-discover'.", err=True)
        return

    click.echo(f"Ingesting FAQs for: {domain.title}...")
    result = ingest_faq_domain(domain)
    click.echo(f"Done: {result['ingested']} FAQs ingested for '{result['domain']}'.")


@ingest_group.command("faq-all")
@click.option("--delay", default=1.0, help="Seconds between requests (default: 1)")
def ingest_faq_all_cmd(delay: float) -> None:
    """Ingest all FAQ domains from the EASA regulations FAQ index.

    Crawls every sub-page linked from the regulations FAQ root and
    ingests all Q&A pairs.  Use --delay to control request pacing.
    """
    from claw_easa.ingest.faq_service import ingest_all_faqs

    def progress(slug: str, current: int, total: int) -> None:
        click.echo(f"[{current}/{total}] {slug}...")

    click.echo("Ingesting all FAQ domains from EASA regulations...")
    results = ingest_all_faqs(delay=delay, progress_cb=progress)

    total_faqs = sum(r["ingested"] for r in results)
    domains_with_content = sum(1 for r in results if r["ingested"] > 0)
    errors = sum(1 for r in results if r.get("error"))

    click.echo(f"\nDone: {total_faqs} FAQs from {domains_with_content} domains.")
    if errors:
        click.echo(f"  Errors: {errors} domains failed.")


# --- Index commands ---


@main.group("index")
def index_group() -> None:
    """Search index commands."""
    pass


@index_group.command("build")
def index_build_cmd() -> None:
    """Build the search index (chunks + embeddings)."""
    from claw_easa.retrieval.pipeline import build_index

    result = build_index()
    click.echo(
        f"Index built: {result['chunks']} chunks, "
        f"{result['embeddings']} embeddings"
    )


@index_group.command("rebuild")
def index_rebuild_cmd() -> None:
    """Rebuild the search index from scratch."""
    from claw_easa.retrieval.pipeline import build_index

    result = build_index()
    click.echo(
        f"Index rebuilt: {result['chunks']} chunks, "
        f"{result['embeddings']} embeddings"
    )


# --- Query commands ---


@main.command("lookup")
@click.argument("ref")
def lookup_cmd(ref: str) -> None:
    """Look up an exact regulation reference."""
    from claw_easa.retrieval.service import lookup

    rows = lookup(ref)
    if not rows:
        click.echo(f"No results for: {ref}")
        return
    for row in rows:
        click.echo(
            f"\n{row['entry_ref']} ({row['entry_type']}) "
            f"— {row['title']} [{row['slug']}]"
        )
        body = row.get("body_text", "")
        if body:
            lines = body.split("\n")[:5]
            for line in lines:
                click.echo(f"  {line}")
            if len(body.split("\n")) > 5:
                click.echo("  ...")


@main.command("refs")
@click.argument("query")
@click.option("--limit", default=10, help="Max results")
@click.option("--slug", default=None, help="Restrict search to a specific source slug")
def refs_cmd(query: str, limit: int, slug: str | None) -> None:
    """Search regulation references."""
    from claw_easa.retrieval.service import refs

    rows = refs(query, limit=limit, slug=slug)
    if not rows:
        click.echo(f"No results for: {query}")
        return
    for row in rows:
        score = row.get("fts_score", 0)
        click.echo(
            f"  {row['entry_ref']} ({row['entry_type']}) "
            f"— {row['title']} [score={score:.2f}]"
        )


@main.command("snippets")
@click.argument("query")
@click.option("--limit", default=5, help="Max results")
@click.option("--slug", default=None, help="Restrict search to a specific source slug")
def snippets_cmd(query: str, limit: int, slug: str | None) -> None:
    """Search and show text snippets."""
    from claw_easa.retrieval.service import snippets
    from claw_easa.retrieval.formatting import compact_snippet

    rows = snippets(query, limit=limit, slug=slug)
    if not rows:
        click.echo(f"No results for: {query}")
        return
    for row in rows:
        click.echo(f"\n{row['entry_ref']} ({row['entry_type']}) — {row['title']}")
        subref = row.get("matched_subref")
        if subref:
            click.echo(f"  [matched inside: {subref}]")
        text = row.get("chunk_text") or row.get("body_text", "")
        snippet = compact_snippet(text, max_lines=3, max_chars=260, query=query)
        if snippet:
            click.echo(f"  {snippet}")


@main.command("hybrid")
@click.argument("query")
@click.option("--top-k", default=10, help="Max results")
@click.option("--slug", default=None, help="Restrict search to a specific source slug")
def hybrid_cmd(query: str, top_k: int, slug: str | None) -> None:
    """Hybrid search (FTS + vector)."""
    from claw_easa.retrieval.service import hybrid

    rows = hybrid(query, top_k=top_k, slug=slug)
    if not rows:
        click.echo(f"No results for: {query}")
        return
    for row in rows:
        score = row.get("hybrid_score", 0)
        source = row.get("match_source", "?")
        click.echo(
            f"  {row.get('entry_ref', '?')} ({row.get('entry_type', '?')}) "
            f"— {row.get('title', '?')} [score={score:.3f}, via={source}]"
        )


@main.command("ask")
@click.argument("query")
@click.option("--strict", is_flag=True, help="Strict ref-only mode")
def ask_cmd(query: str, strict: bool) -> None:
    """Ask a question (routed hybrid search with formatted answer)."""
    from claw_easa.retrieval.router import route_query
    from claw_easa.retrieval.rewrite import rewrite_query
    from claw_easa.retrieval.service import hybrid, lookup, refs, snippets
    from claw_easa.retrieval.answering import (
        format_answer_answer,
        format_exact_answer,
        format_refs_answer,
        format_snippets_answer,
    )

    routed = route_query(query, strict=strict)
    rewritten = rewrite_query(routed.normalized_query, routed.intent)

    if routed.intent == "exact_lookup":
        rows = lookup(routed.normalized_query)
        click.echo(format_exact_answer(rows, routed.normalized_query, rewritten))
    elif routed.intent == "refs_only":
        rows = refs(rewritten)
        click.echo(format_refs_answer(rows, rewritten))
    elif routed.intent == "snippets":
        rows = snippets(rewritten)
        click.echo(format_snippets_answer(rows, rewritten))
    elif routed.intent == "survey":
        from claw_easa.retrieval.survey import shape_survey_results
        from claw_easa.retrieval.answering import format_survey_answer

        rows = hybrid(rewritten, top_k=30)
        shaped = shape_survey_results(rows)
        click.echo(format_survey_answer(shaped, rewritten))
    else:
        rows = hybrid(rewritten)
        click.echo(format_answer_answer(rows, rewritten))


# --- Source listing ---


@main.command("ear-list")
def ear_list_cmd() -> None:
    """List short aliases for common Easy Access Rules.

    These aliases are convenience shortcuts for 'ingest fetch'.
    URLs are resolved dynamically from the EASA catalog — not hardcoded.
    Run 'ear-discover' to see all available sources.
    """
    from claw_easa.ingest.sources import list_aliases

    click.echo("Aliases (use with 'ingest fetch <alias>'):\n")
    for alias in list_aliases():
        click.echo(f"  {alias.slug:<30} keywords: {', '.join(alias.match_keywords)}")


@main.command("ear-discover")
@click.option("--refresh", is_flag=True, help="Force refresh from EASA website")
def ear_discover_cmd(refresh: bool) -> None:
    """Discover Easy Access Rules available on the EASA website.

    Results are cached locally for 1 hour.  Use --refresh to force
    a fresh scrape.  Any slug shown here can be used directly with
    'ingest fetch'.
    """
    from claw_easa.ingest.catalog import EasyAccessRulesCatalogScraper
    from claw_easa.ingest.sources import get_alias

    click.echo("Scanning EASA website for Easy Access Rules...")
    try:
        scraper = EasyAccessRulesCatalogScraper()
        entries = scraper.discover(force_refresh=refresh)
        if not entries:
            click.echo("No Easy Access Rules found.")
            return
        click.echo(f"Found {len(entries)} Easy Access Rules:\n")
        for entry in entries:
            alias = _find_alias_for_catalog_slug(entry.slug)
            alias_hint = f"  (alias: {alias})" if alias else ""
            click.echo(f"  {entry.slug:<50} {entry.title}")
            click.echo(f"    {entry.page_url}{alias_hint}")
    except Exception as e:
        click.echo(f"Error discovering sources: {e}", err=True)


def _find_alias_for_catalog_slug(catalog_slug: str) -> str | None:
    from claw_easa.ingest.sources import SLUG_ALIASES
    for alias in SLUG_ALIASES:
        if any(kw in catalog_slug for kw in alias.match_keywords):
            return alias.slug
    return None


@main.command("sources-list")
@click.option("--type", "filter_type", default=None, type=click.Choice(["ear", "faq", "all"]),
              help="Filter by source type (default: all)")
def sources_list_cmd(filter_type: str | None) -> None:
    """List ingested source documents.

    Shows EAR (Easy Access Rules) and FAQ sources separately with
    entry counts.  Use --type ear or --type faq to filter.
    """
    from claw_easa.ingest.repository import list_documents

    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    try:
        docs = list_documents(db)
        if not docs:
            click.echo("No documents ingested yet.")
            return

        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sd.id, COUNT(re.id) as cnt "
                    "FROM source_documents sd "
                    "LEFT JOIN regulation_parts rp ON rp.document_id = sd.id "
                    "LEFT JOIN regulation_subparts rs ON rs.part_id = rp.id "
                    "LEFT JOIN regulation_sections rsec ON rsec.subpart_id = rs.id "
                    "LEFT JOIN regulation_entries re ON re.section_id = rsec.id "
                    "GROUP BY sd.id"
                )
                counts = {row["id"]: row["cnt"] for row in cur.fetchall()}

        ears = [d for d in docs if d["source_family"] != "faq"]
        faqs = [d for d in docs if d["source_family"] == "faq"]

        show_ear = filter_type in (None, "all", "ear")
        show_faq = filter_type in (None, "all", "faq")

        if show_ear and ears:
            click.echo(f"Easy Access Rules ({len(ears)}):\n")
            for doc in ears:
                cnt = counts.get(doc["id"], 0)
                entries_info = f"{cnt} entries" if cnt else doc["status"]
                click.echo(f"  {doc['slug']:<30} {entries_info:<15} {doc['title']}")
            click.echo()

        if show_faq and faqs:
            total_faq_entries = sum(counts.get(d["id"], 0) for d in faqs)
            domains_with_content = sum(1 for d in faqs if counts.get(d["id"], 0) > 0)
            click.echo(f"FAQ domains ({domains_with_content} with content, {total_faq_entries} total FAQs):\n")
            for doc in faqs:
                cnt = counts.get(doc["id"], 0)
                if cnt > 0:
                    click.echo(f"  {doc['slug']:<50} {cnt:>4} FAQs  {doc['title']}")
            empty = len(faqs) - domains_with_content
            if empty:
                click.echo(f"  ({empty} empty domains not shown)")
    finally:
        db.close()
