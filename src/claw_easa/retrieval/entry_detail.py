"""Detailed lookup of one provision, as JSON an agent can quote from.

``claw-easa lookup <REF>`` prints a five-line extract for a human reader.  An
agent quoting a rule needs the whole provision *and* the provenance it has to
cite with it — which corpus, which EASA revision, where in the document.

Two things are deliberate here:

- provenance the corpus does not hold is reported as ``null``.  Nothing
  reconstructs a page URL from a slug or dates an unlabelled revision: a
  citation the corpus cannot support is worse than no citation at all;
- a reference held by more than one corpus is reported as ambiguous rather
  than answered with one of the candidates, which would silently attribute a
  rule to a source that may not state it.  The candidates are listed so the
  caller can re-ask with the corpus named.
"""
from __future__ import annotations

import json

from claw_easa.db.sqlite import Database
from claw_easa.references import normalize_reference_text
from claw_easa.retrieval.exact import resolve_reference

#: Shape of the document this module emits.  Bumped when a consumer written
#: against the previous shape would misread the current one.
SCHEMA_VERSION = "1.0"

#: Entry columns added after the first corpora were ingested.  ``CREATE TABLE
#: IF NOT EXISTS`` leaves an existing table alone, so a database built by an
#: earlier version still holds the rule text in the shape it was created with.
#: Selecting the missing ones as NULL reads that corpus as it is, rather than
#: failing a lookup the corpus can answer.
_OPTIONAL_ENTRY_COLUMNS = ("body_markdown", "source_locator", "source_url")

_DETAIL_COLUMNS = (
    "e.entry_ref, e.entry_type, e.title, e.body_text, "
    "d.slug, d.title AS document_title, d.page_url, d.revision, "
    "p.part_code, p.annex, p.regulation, sp.subpart_code"
)

_DETAIL_FROM = (
    "FROM regulation_entries e "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
)


def entry_detail(db: Database, reference: str, *, slug: str | None = None) -> dict:
    """The whole provision *reference* names, with the provenance to cite it.

    Restricted to the corpus *slug* names when one is given.  ``status`` is
    ``found`` only when exactly one entry answers: ``not_found`` and
    ``ambiguous`` are answers too, and both leave ``entry`` null.
    """
    wanted = normalize_reference_text(reference)
    rows = _matching_rows(db, wanted, slug)

    if len(rows) == 1:
        status, entry = "found", _entry(rows[0])
    else:
        status, entry = ("ambiguous" if rows else "not_found"), None

    return {
        "schema_version": SCHEMA_VERSION,
        "query": {"reference": wanted, "slug": slug},
        "status": status,
        "match_count": len(rows),
        "matches": [_match(row) for row in rows],
        "entry": entry,
    }


def render_json(document: dict) -> str:
    """*document* as one line of JSON, safe to hand to any consumer.

    ``ensure_ascii`` is what makes it safe.  Entry text is data and is carried
    verbatim, so a provision holding U+2028 or an ANSI escape would otherwise
    be emitted raw — ending the line for a JavaScript consumer, or moving the
    cursor of the terminal reading it.
    """
    return json.dumps(document, ensure_ascii=True)


# ── Internals ───────────────────────────────────────────────────────────────


def _matching_rows(db: Database, reference: str, slug: str | None) -> list[dict]:
    """Every entry *reference* resolves to, in a stable order.

    Scoped by value, never by interpolation: a slug is user input and reaches
    the query as a parameter like any other.
    """
    scope = "AND d.slug = ? " if slug is not None else ""

    with db.connection() as conn:
        with conn.cursor() as cur:
            optional = _optional_columns(cur)

            def run(where: str, params: tuple) -> list[dict]:
                cur.execute(
                    f"SELECT {_DETAIL_COLUMNS}, {optional} {_DETAIL_FROM}"
                    f"WHERE {where} {scope}ORDER BY d.slug, e.entry_ref",
                    (*params, slug) if slug is not None else params,
                )
                return cur.fetchall()

            return resolve_reference(reference, run)


def _optional_columns(cur) -> str:
    """Select list for the columns an older corpus may not have."""
    cur.execute("PRAGMA table_info(regulation_entries)")
    present = {row["name"] for row in cur.fetchall()}
    return ", ".join(
        f"e.{column}" if column in present else f"NULL AS {column}"
        for column in _OPTIONAL_ENTRY_COLUMNS
    )


def _match(row: dict) -> dict:
    """What identifies one candidate — enough to re-ask with the corpus named."""
    return {
        "entry_ref": row["entry_ref"],
        "entry_type": row["entry_type"],
        "title": row["title"],
        "slug": row["slug"],
        "revision": row["revision"],
        "part": row["part_code"],
        "subpart": row["subpart_code"],
    }


def _entry(row: dict) -> dict:
    """One candidate, with its full text and the rest of its provenance."""
    markdown = row["body_markdown"]
    text = markdown if markdown else (row["body_text"] or "")

    return {
        **_match(row),
        "text": text,
        "text_format": "markdown" if markdown else "text",
        "body_markdown": markdown,
        "body_text": row["body_text"],
        "document_title": row["document_title"],
        "page_url": row["page_url"],
        "source_url": row["source_url"],
        "source_locator": row["source_locator"],
        "annex": row["annex"],
        "regulation": row["regulation"],
    }
