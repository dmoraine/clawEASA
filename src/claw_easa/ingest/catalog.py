from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

EASA_EAR_INDEX_URL = (
    "https://www.easa.europa.eu/en/document-library/easy-access-rules"
)


@dataclass
class CatalogEntry:
    slug: str
    title: str
    page_url: str
    source_url: str | None = None


class EasyAccessRulesCatalogScraper:
    def __init__(self, base_url: str = EASA_EAR_INDEX_URL) -> None:
        self.base_url = base_url

    def discover(self) -> list[CatalogEntry]:
        resp = requests.get(self.base_url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        entries: list[CatalogEntry] = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if "easy-access-rules" in href and text and len(text) > 10:
                if not href.startswith("http"):
                    href = f"https://www.easa.europa.eu{href}"
                slug = href.rstrip("/").rsplit("/", 1)[-1]
                slug = slug.replace("easy-access-rules-", "")
                entries.append(CatalogEntry(
                    slug=slug,
                    title=text,
                    page_url=href,
                ))

        seen: set[str] = set()
        unique: list[CatalogEntry] = []
        for entry in entries:
            if entry.slug not in seen:
                seen.add(entry.slug)
                unique.append(entry)

        return unique
