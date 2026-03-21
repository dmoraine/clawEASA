from __future__ import annotations

from dataclasses import dataclass

REGULATIONS_FAQ_ROOT_URL = "https://www.easa.europa.eu/en/faq"


@dataclass(frozen=True)
class FAQDomain:
    slug: str
    title: str
    url: str
    source_doc_slug: str


def make_faq_domain(slug: str, title: str, url: str) -> FAQDomain:
    source_doc_slug = f"faq-{slug}"
    return FAQDomain(
        slug=slug,
        title=title,
        url=url,
        source_doc_slug=source_doc_slug,
    )
