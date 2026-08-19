from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from claw_easa.freshness import parse_published_date

log = logging.getLogger(__name__)

# EASA advertises each Easy Access Rules document as a dated revision,
# e.g. "Revision from March 2026".
REVISION_RE = re.compile(r'Revision\s+from\s+([A-Za-z]+\s+\d{4})', re.IGNORECASE)

# How far up from the link to look for the card that carries the revision.
_CARD_LOOKUP_DEPTH = 6

EASA_EAR_INDEX_URL = (
    "https://www.easa.europa.eu/en/document-library/easy-access-rules"
)

CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class CatalogEntry:
    """One Easy Access Rules document as the EASA catalogue advertises it.

    ``revision`` and ``published_at`` are what EASA says the document is;
    ``checked_at`` is when the catalogue was read, without which a revision
    label says nothing about how recently it was true.  All three stay
    ``None`` for an entry no catalogue reading produced — a fallback URL, for
    instance — because nothing observed them.
    """
    slug: str
    title: str
    page_url: str
    source_url: str | None = None
    revision: str | None = None
    published_at: str | None = None
    checked_at: str | None = None


class EasyAccessRulesCatalogScraper:
    _MAX_PAGES = 20

    def __init__(
        self,
        base_url: str = EASA_EAR_INDEX_URL,
        cache_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url
        self._cache_dir = cache_dir

    @property
    def _cache_path(self) -> Path | None:
        if self._cache_dir is None:
            from claw_easa.config import get_settings
            self._cache_dir = get_settings().data_path
        if self._cache_dir is not None:
            return self._cache_dir / ".ear_catalog_cache.json"
        return None

    # ── Public API ────────────────────────────────────────────────────

    def discover(self, *, force_refresh: bool = False) -> list[CatalogEntry]:
        """Return all Easy Access Rules currently listed on the EASA website.

        Results are cached locally for ``CACHE_TTL_SECONDS`` to avoid
        unnecessary requests to the EASA servers.
        """
        if not force_refresh:
            cached = self._load_cache()
            if cached is not None:
                return cached

        entries = self._scrape()
        self._save_cache(entries)
        return entries

    def resolve(self, slug_or_alias: str) -> CatalogEntry:
        """Resolve a slug (or known alias) to a catalog entry with live URL.

        Resolution order:
        1. Exact match on catalog slug
        2. Known alias → keyword match against catalog slugs
        3. Substring match on catalog slug
        4. Alias fallback URL (for EARs not listed on the catalog page)
        """
        entries = self.discover()

        for entry in entries:
            if entry.slug == slug_or_alias:
                return entry

        from claw_easa.ingest.sources import get_alias
        alias = get_alias(slug_or_alias)
        if alias:
            for entry in entries:
                if any(kw in entry.slug for kw in alias.match_keywords):
                    return entry

        for entry in entries:
            if slug_or_alias in entry.slug:
                return entry

        if alias and alias.fallback_page_url:
            log.info(
                "Catalog miss for '%s', using fallback URL", slug_or_alias,
            )
            return CatalogEntry(
                slug=alias.slug,
                title=alias.slug,
                page_url=alias.fallback_page_url,
            )

        available = ", ".join(e.slug for e in entries[:8])
        raise ValueError(
            f"'{slug_or_alias}' not found in EASA catalog. "
            f"Available: {available}... "
            f"Run 'claw-easa ear-discover' to see all."
        )

    # ── Scraping ──────────────────────────────────────────────────────

    def _scrape(self) -> list[CatalogEntry]:
        from claw_easa.ingest import http
        log.info("Scraping EASA catalog at %s", self.base_url)

        seen: set[str] = set()
        all_entries: list[CatalogEntry] = []
        # One reading of the catalogue, so every entry it yields carries the
        # same timestamp.  Taken before the first request: what it claims is
        # that EASA was read no *later* than this, which is the safe
        # direction for a check whose age decides how much it is worth.
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for page_num in range(self._MAX_PAGES):
            url = self.base_url if page_num == 0 else f"{self.base_url}?page={page_num}"
            resp = http.get(url)
            soup = BeautifulSoup(resp.text, "html.parser")

            page_entries = self._extract_entries(soup)
            if not page_entries:
                break

            for entry in page_entries:
                if entry.slug not in seen:
                    seen.add(entry.slug)
                    entry.checked_at = checked_at
                    all_entries.append(entry)

            log.debug("Page %d: %d entries", page_num, len(page_entries))

        log.info("Discovered %d Easy Access Rules across %d page(s)",
                 len(all_entries), min(page_num + 1, self._MAX_PAGES))
        return all_entries

    @staticmethod
    def _extract_entries(soup: BeautifulSoup) -> list[CatalogEntry]:
        entries: list[CatalogEntry] = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if "easy-access-rules" not in href or not text or len(text) <= 10:
                continue
            if "/document-library/easy-access-rules/" not in href:
                continue
            if not href.startswith("http"):
                href = f"https://www.easa.europa.eu{href}"
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            slug = slug.replace("easy-access-rules-", "")
            card = EasyAccessRulesCatalogScraper._card_for(link)
            revision = EasyAccessRulesCatalogScraper._extract_revision(card)
            entries.append(CatalogEntry(
                slug=slug,
                title=text,
                page_url=href,
                revision=revision,
                published_at=EasyAccessRulesCatalogScraper._extract_published_at(
                    card, revision,
                ),
            ))
        return entries

    @staticmethod
    def _card_for(link):
        """The catalogue card holding *link*, or ``None`` if there is none.

        Only the card's own subtree may be read — taking anything from the
        whole listing would label every document with the first card's
        metadata.
        """
        node = link
        for _ in range(_CARD_LOOKUP_DEPTH):
            node = node.parent
            if node is None or node.name in (None, "body", "html"):
                return None
            classes = node.get("class") or []
            if node.name == "article" or any("content-item" in cls for cls in classes):
                return node
        return None

    @staticmethod
    def _extract_revision(card) -> str | None:
        """The revision EASA advertises on *card*."""
        if card is None:
            return None
        match = REVISION_RE.search(card.get_text(" ", strip=True))
        return match.group(1) if match else None

    @staticmethod
    def _extract_published_at(card, revision: str | None) -> str | None:
        """The date *card* gives for *revision*, to the day, where they agree.

        A card carries a machine-readable date next to the revision it
        advertises.  It is read as that revision's publication date only when
        the revision label puts it in the same month: a date the label does
        not corroborate is some other date — a page edit, an applicability
        deadline — and dating an edition by it would invent a publication.
        Nothing readable leaves the date unknown, and the month the label
        itself names is what the comparison then falls back on.
        """
        if card is None or not revision:
            return None
        label_month = (parse_published_date(revision) or "")[:7]
        if not label_month:
            return None
        for node in card.find_all("time"):
            published = parse_published_date(
                node.get("datetime") or node.get_text(" ", strip=True)
            )
            if published and published[:7] == label_month:
                return published
        return None

    # ── File cache ────────────────────────────────────────────────────

    def _load_cache(self) -> list[CatalogEntry] | None:
        path = self._cache_path
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("ts", 0) > CACHE_TTL_SECONDS:
                return None
            return [CatalogEntry(**e) for e in data.get("entries", [])]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def _save_cache(self, entries: list[CatalogEntry]) -> None:
        path = self._cache_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "ts": time.time(),
                "entries": [asdict(e) for e in entries],
            }))
        except OSError:
            pass
