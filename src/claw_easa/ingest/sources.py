from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    """Internal representation of a source document for fetching/parsing."""
    slug: str
    source_family: str
    title: str
    language: str = "en"
    page_url: str = ""
    source_url: str | None = None


@dataclass(frozen=True)
class SourceAlias:
    """Maps a short user-friendly slug to catalog search keywords.

    URLs are resolved dynamically from the EASA catalog.
    ``fallback_page_url`` is only used for EARs that EASA doesn't list
    on the main catalog index (e.g. the Basic Regulation).
    """
    slug: str
    match_keywords: tuple[str, ...]
    source_family: str = "ear"
    language: str = "en"
    fallback_page_url: str = ""


SLUG_ALIASES: list[SourceAlias] = [
    SourceAlias("air-ops", ("air-operations",)),
    SourceAlias("aircrew", ("aircrew",)),
    SourceAlias(
        "basic-regulation",
        ("basic-regulation",),
        fallback_page_url=(
            "https://www.easa.europa.eu/en/document-library/easy-access-rules/"
            "easy-access-rules-basic-regulation-regulation-eu-20181139"
        ),
    ),
    SourceAlias("initial-airworthiness", ("initial-airworthiness",)),
    SourceAlias("continuing-airworthiness", ("continuing-airworthiness",)),
    SourceAlias("aerodromes", ("aerodromes",)),
    SourceAlias("atm-ans", ("air-traffic-managementair-navigation-services",)),
    SourceAlias("sera", ("standardised-european-rules",)),
    SourceAlias("occurrence-reporting", ("occurrence-reporting",), source_family="rulebook"),
]


def get_alias(slug: str) -> SourceAlias | None:
    for alias in SLUG_ALIASES:
        if alias.slug == slug:
            return alias
    return None


def list_aliases() -> list[SourceAlias]:
    return list(SLUG_ALIASES)
