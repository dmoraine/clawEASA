"""Freshness grading — is the edition held still the one EASA publishes?

clawEASA answers from the latest edition of each source it managed to
retrieve.  It is not a regulatory archive, so it cannot answer "what did this
rule say in 2023?".  What it must answer is "is what I hold still what EASA
publishes?", and it must say *I don't know* rather than guess.

That question is graded here from two independent facts:

``revision`` / ``published_at``
    the edition label of the artefact actually held.

``catalog_revision`` / ``catalog_published_at``
    the edition label EASA advertised the last time the catalogue was read.

The result is one of five **operational** labels.  None is a compliance
conclusion and none says anything about the content of a regulation — they
describe the state of the pipeline's copy:

``current``
    what is held matches the EASA revision last observed.
``freshness-unknown``
    the corpus is usable, but no comparable EASA revision was available to
    check it against.  The fallback whenever a comparison cannot be made.
``stale``
    EASA advertises something other than what is held.
``incomplete``
    an expected source is missing or contributed no entries.
``failed``
    the corpus holds nothing usable at all.

Grading is deliberately biased towards ``stale`` and ``freshness-unknown``
over ``current``: a false "up to date" is the only outcome that can mislead
someone into trusting a superseded rule.

These labels are graded per build in the corpus manifest, never on
``source_documents.status`` — that column is the ingestion lifecycle (fetched,
parsed, indexed), which says what the pipeline did, not what EASA publishes
today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CURRENT = "current"
FRESHNESS_UNKNOWN = "freshness-unknown"
STALE = "stale"
INCOMPLETE = "incomplete"
FAILED = "failed"

#: Every status that exists, ordered from healthiest to worst.  A build rolls
#: up to the worst status among its sources.
STATUS_SEVERITY: tuple[str, ...] = (
    CURRENT,
    FRESHNESS_UNKNOWN,
    STALE,
    INCOMPLETE,
    FAILED,
)

#: Statuses whose corpus is complete and usable, and therefore worth keeping
#: as the last healthy build to roll back to.  ``stale`` qualifies: outdated
#: rule text still beats no rule text.
USABLE_STATUSES: frozenset[str] = frozenset({CURRENT, FRESHNESS_UNKNOWN, STALE})

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_DAY_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ISO_MONTH_RE = re.compile(r"\b(\d{4})-(\d{2})\b")
_DAY_MONTH_YEAR_RE = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)


def normalize_label(value: str | None) -> str | None:
    """Collapse whitespace and trim punctuation, or ``None`` if nothing is left."""
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    cleaned = cleaned.strip(" \t-–—:,.;")
    return cleaned or None


def parse_published_date(text: str | None) -> str | None:
    """Read an EASA edition label as a sortable ``YYYY-MM-DD`` date.

    Month-precision labels ("March 2026") are padded to the first of the
    month.  Padding makes month- and day-precision labels comparable, at the
    cost of reading a same-month day-precision catalogue label as newer than a
    month-precision held label — which errs towards ``stale``, the safe
    direction.

    Returns ``None`` when nothing can be read as a date, which is what makes
    the caller fall back to ``freshness-unknown``.  A bare year is never
    accepted: regulation titles are full of them ("Regulation (EU) No
    965/2012") and matching those would invent a publication date.
    """
    if not text:
        return None

    match = _ISO_DAY_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return _iso(year, month, day)

    match = _DAY_MONTH_YEAR_RE.search(text)
    if match:
        day, month_name, year = match.groups()
        return _iso(int(year), _MONTHS[month_name.lower()], int(day))

    match = _MONTH_DAY_YEAR_RE.search(text)
    if match:
        month_name, day, year = match.groups()
        return _iso(int(year), _MONTHS[month_name.lower()], int(day))

    match = _MONTH_YEAR_RE.search(text)
    if match:
        month_name, year = match.groups()
        return _iso(int(year), _MONTHS[month_name.lower()], 1)

    match = _ISO_MONTH_RE.search(text)
    if match:
        year, month = (int(g) for g in match.groups())
        return _iso(year, month, 1)

    return None


def _iso(year: int, month: int, day: int) -> str | None:
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _comparable(value: str | None) -> str | None:
    normalized = normalize_label(value)
    return normalized.casefold() if normalized else None


@dataclass(frozen=True)
class Freshness:
    """The graded state of one source, with why it was graded that way."""

    status: str
    basis: str
    detail: str


def grade_freshness(
    *,
    revision: str | None,
    published_at: str | None = None,
    catalog_revision: str | None,
    catalog_published_at: str | None = None,
) -> Freshness:
    """Compare the edition held against the edition EASA advertises.

    Only ever returns ``current``, ``freshness-unknown`` or ``stale``.
    ``incomplete`` and ``failed`` describe a corpus that is missing or broken
    rather than possibly outdated, so the caller settles those first — a
    source that parsed to nothing cannot meaningfully be called up to date.
    """
    held_label = _comparable(revision)
    held_date = published_at or parse_published_date(revision)
    catalog_label = _comparable(catalog_revision)
    catalog_date = catalog_published_at or parse_published_date(catalog_revision)

    if catalog_label is None and catalog_date is None:
        return Freshness(
            FRESHNESS_UNKNOWN,
            "no-catalog-revision",
            "EASA advertised no comparable revision, so freshness is unknown.",
        )

    if held_label is None and held_date is None:
        return Freshness(
            FRESHNESS_UNKNOWN,
            "no-local-revision",
            "The artefact held carries no revision label to compare.",
        )

    if held_label is not None and held_label == catalog_label:
        return Freshness(
            CURRENT,
            "revision-label-match",
            f"Held revision matches the EASA catalogue "
            f"({normalize_label(revision)}).",
        )

    if held_date and catalog_date:
        if catalog_date > held_date:
            return Freshness(
                STALE,
                "catalog-newer",
                f"EASA advertises {catalog_date}, the artefact held is {held_date}.",
            )
        return Freshness(
            CURRENT,
            "catalog-not-newer",
            f"EASA advertises {catalog_date}, not newer than the {held_date} "
            f"artefact held.",
        )

    return Freshness(
        STALE,
        "revision-label-differs",
        "EASA advertises a different revision and neither label can be dated; "
        "assuming the held copy is behind.",
    )


def severity(status: str | None) -> int:
    """Rank of *status* on the severity ladder; anything unknown ranks worst."""
    try:
        return STATUS_SEVERITY.index(status)
    except ValueError:
        return len(STATUS_SEVERITY)


def rollup_status(statuses: list[str] | tuple[str, ...]) -> str:
    """The status implied by a set of sources: the worst one wins.

    An empty corpus is ``failed`` — there is nothing to be fresh about.
    """
    if not statuses:
        return FAILED
    return max(statuses, key=severity)
