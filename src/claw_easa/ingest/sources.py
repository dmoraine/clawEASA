from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    slug: str
    source_family: str
    title: str
    language: str = "en"
    page_url: str = ""
    source_url: str | None = None


KNOWN_SOURCES: list[SourceSpec] = [
    SourceSpec(
        slug="air-ops",
        source_family="ear",
        title="Easy Access Rules for Air Operations",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-air-operations",
    ),
    SourceSpec(
        slug="aircrew",
        source_family="ear",
        title="Easy Access Rules for Aircrew",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-aircrew-regulation",
    ),
    SourceSpec(
        slug="basic-regulation",
        source_family="ear",
        title="Easy Access Rules for the Basic Regulation",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-basic-regulation",
    ),
    SourceSpec(
        slug="occurrence-reporting",
        source_family="rulebook",
        title="Occurrence Reporting Rule Book",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-occurrence-reporting",
    ),
]


def get_source(slug: str) -> SourceSpec:
    for source in KNOWN_SOURCES:
        if source.slug == slug:
            return source
    raise ValueError(f"Unknown source: {slug}")


def list_sources() -> list[SourceSpec]:
    return list(KNOWN_SOURCES)
