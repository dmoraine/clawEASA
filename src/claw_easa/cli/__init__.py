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


@main.command("status")
def status_cmd() -> None:
    """Show project status."""
    from claw_easa.config import get_settings

    settings = get_settings()
    click.echo(f"Data dir:      {settings.data_dir}")
    click.echo(f"Database:      {settings.db_path}")
    click.echo(f"FAISS index:   {settings.faiss_index_path}")

    if settings.db_path.exists():
        import os

        size = os.path.getsize(settings.db_path)
        click.echo(f"DB size:       {size:,} bytes")

        db = Database()
        db.open()
        try:
            row = db.fetch_one("SELECT COUNT(*) AS cnt FROM source_documents")
            click.echo(f"Documents:     {row['cnt']}")
            row = db.fetch_one("SELECT COUNT(*) AS cnt FROM regulation_entries")
            click.echo(f"Entries:       {row['cnt']}")
            row = db.fetch_one("SELECT COUNT(*) AS cnt FROM entry_chunks")
            click.echo(f"Chunks:        {row['cnt']}")
        except Exception:
            click.echo("(schema not initialised)")
        finally:
            db.close()
    else:
        click.echo("(database not created — run 'claw-easa init')")

    if settings.faiss_index_path.exists():
        import os

        size = os.path.getsize(settings.faiss_index_path)
        click.echo(f"FAISS size:    {size:,} bytes")
    else:
        click.echo("(FAISS index not built — run 'claw-easa index build')")


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
def refs_cmd(query: str, limit: int) -> None:
    """Search regulation references."""
    from claw_easa.retrieval.service import refs

    rows = refs(query, limit=limit)
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
def snippets_cmd(query: str, limit: int) -> None:
    """Search and show text snippets."""
    from claw_easa.retrieval.service import snippets
    from claw_easa.retrieval.formatting import compact_snippet

    rows = snippets(query, limit=limit)
    if not rows:
        click.echo(f"No results for: {query}")
        return
    for row in rows:
        click.echo(f"\n{row['entry_ref']} ({row['entry_type']}) — {row['title']}")
        snippet = compact_snippet(row.get("body_text", ""), max_lines=3, max_chars=260)
        if snippet:
            click.echo(f"  {snippet}")


@main.command("hybrid")
@click.argument("query")
@click.option("--top-k", default=10, help="Max results")
def hybrid_cmd(query: str, top_k: int) -> None:
    """Hybrid search (FTS + vector)."""
    from claw_easa.retrieval.service import hybrid

    rows = hybrid(query, top_k=top_k)
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
def sources_list_cmd() -> None:
    """List ingested source documents."""
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
        for doc in docs:
            click.echo(
                f"  {doc['slug']:<25} {doc['status']:<10} {doc['title']}"
            )
    finally:
        db.close()
