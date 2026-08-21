"""Provenance projected onto a reference search result.

A ``refs`` result is a citation, so it has to say where the rule can be read
back: the URL the source published for the entry itself where there is one,
the URL of the document holding it otherwise, the locator saying where in that
document the entry was parsed from, the slug of the source, and the EASA
revision held.  Ingestion already records all of it — this module only
projects what is stored.

No URL is ever derived from a reference or a slug.  EASA publishes no stable
per-rule address that ``ORO.FTL.110`` could be spelled into, so a constructed
link would be a guess presented as a citation.  An entry whose provenance
holds no URL reports none, and the locator is what narrows it down instead.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

#: ``url`` addresses the entry itself.
URL_KIND_ENTRY = "entry"
#: ``url`` addresses the document holding the entry; ``locator`` says where in
#: it the entry was parsed from.
URL_KIND_DOCUMENT = "document"


@dataclass(frozen=True)
class EntryProvenance:
    """Where one retrieved entry can be read back.

    ``url_kind`` says what ``url`` addresses, and is ``None`` exactly when
    ``url`` is — nothing recorded an address for the entry or for its
    document, which is the ordinary state of a source imported from a local
    file.
    """
    slug: str | None = None
    url: str | None = None
    url_kind: str | None = None
    locator: str | None = None
    revision: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _stored(value: object) -> str | None:
    """*value* as a stored string, or ``None`` when nothing was stored.

    An empty column is not provenance: it reports as absent rather than as an
    empty citation.
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None


def entry_provenance(row: Mapping) -> EntryProvenance:
    """Project the provenance columns of a reference search *row*.

    The entry's own URL is preferred over the document's because it addresses
    the text that matched, where the document's only addresses the artefact it
    was parsed from.  A document registered without a download URL still
    carries the EASA page it was resolved from, so that is read next: both are
    recorded provenance of the same document.
    """
    entry_url = _stored(row.get("entry_url"))
    document_url = (
        _stored(row.get("document_url")) or _stored(row.get("document_page_url"))
    )
    return EntryProvenance(
        slug=_stored(row.get("slug")),
        url=entry_url or document_url,
        url_kind=(
            URL_KIND_ENTRY if entry_url
            else URL_KIND_DOCUMENT if document_url
            else None
        ),
        locator=_stored(row.get("entry_locator")),
        revision=_stored(row.get("document_revision")),
    )


def refs_payload(
    rows: Sequence[Mapping], query: str, *, limit: int, slug: str | None = None,
) -> dict:
    """The JSON contract of ``claw-easa refs``.

    Carries the results the text output prints, in the same order and under
    the same limit, each with the score retrieval ranked it by and the
    provenance to cite it from.  ``score`` is that internal ranking score
    verbatim — comparable within one search, not a confidence.

    The rule text is deliberately left out: a reference search answers with
    references, and the wording is served by ``lookup`` and ``snippets``,
    which return the entry in full rather than a copy of it.
    """
    results = [
        {
            "entry_ref": row.get("entry_ref"),
            "entry_type": row.get("entry_type"),
            "title": row.get("title"),
            "score": row.get("fts_score", 0),
            "source": entry_provenance(row).to_dict(),
        }
        for row in rows
    ]
    return {
        "query": query,
        "slug": slug,
        "limit": limit,
        "count": len(results),
        "results": results,
    }
