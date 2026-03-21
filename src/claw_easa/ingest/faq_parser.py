"""EASA FAQ page parser.

EASA FAQ pages use a consistent structure:
  - ``div.faq-child.expand`` contains each Q&A pair
  - ``<h4>`` inside is the question
  - ``div.body.field`` sibling contains the answer HTML

All Q&A pairs are on a single page (no per-question detail pages).
A ``div.faq-category`` wrapper (when present) groups questions by topic.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

EASA_REF_PATTERN = re.compile(
    r'(?:AMC\d?|GM\d?|IR|CS|CAT|ORO|SPA|ARO|ORA|ARA|MED|FCL|ATCO|ATS|ADR|SERA)'
    r'(?:\.[A-Z]+)*(?:\.\d+)?(?:\([a-z]\))?'
)


@dataclass
class FAQItem:
    question: str
    answer_text: str
    category: str = ""
    detected_refs: list[str] = field(default_factory=list)


@dataclass
class FAQDomainLink:
    slug: str
    title: str
    url: str


def parse_faq_root_page(html: str, base_url: str) -> list[FAQDomainLink]:
    """Extract FAQ domain links from the EASA FAQ root page."""
    soup = BeautifulSoup(html, "html.parser")
    domains: list[FAQDomainLink] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        if not text or len(text) <= 5:
            continue
        if "/faqs/" not in href and "/faq/" not in href:
            continue

        absolute = urljoin(base_url, href)
        slug = absolute.rstrip("/").rsplit("/", 1)[-1]
        slug = slug.split("#")[0]

        if slug in seen or slug in ("faq", "faqs", "website"):
            continue
        seen.add(slug)
        domains.append(FAQDomainLink(slug=slug, title=text, url=absolute))

    return domains


def parse_faq_page(html: str) -> list[FAQItem]:
    """Extract all FAQ items from an EASA FAQ page.

    Works with the ``div.faq-child`` accordion structure used on
    https://www.easa.europa.eu/en/the-agency/faqs/* pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[FAQItem] = []

    faq_children = soup.find_all(class_="faq-child")
    if not faq_children:
        faq_children = soup.find_all("div", class_="expand")

    current_category = ""

    for child in faq_children:
        cat_parent = child.find_parent(class_="faq-category")
        if cat_parent:
            cat_title = cat_parent.find(class_="category-title")
            if cat_title:
                current_category = cat_title.get_text(strip=True)

        h4 = child.find("h4")
        if not h4:
            continue
        question = h4.get_text(strip=True)
        if not question or len(question) < 10:
            continue

        body_div = child.find(class_="body")
        if not body_div:
            body_div = child.find(class_="field")
        answer = body_div.get_text("\n", strip=True) if body_div else ""

        if not answer or len(answer) < 10:
            continue

        detected = EASA_REF_PATTERN.findall(question + " " + answer)

        items.append(FAQItem(
            question=question,
            answer_text=answer,
            category=current_category,
            detected_refs=list(set(detected)),
        ))

    return items
