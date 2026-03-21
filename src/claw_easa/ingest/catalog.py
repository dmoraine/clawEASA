from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

EASA_EAR_INDEX_URL = (
    "https://www.easa.europa.eu/en/document-library/easy-access-rules"
)

CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class CatalogEntry:
    slug: str
    title: str
    page_url: str
    source_url: str | None = None


class EasyAccessRulesCatalogScraper:
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
            self._cache_dir = Path(get_settings().data_dir)
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
        log.info("Scraping EASA catalog at %s", self.base_url)
        resp = requests.get(self.base_url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

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
            entries.append(CatalogEntry(slug=slug, title=text, page_url=href))

        seen: set[str] = set()
        unique: list[CatalogEntry] = []
        for entry in entries:
            if entry.slug not in seen:
                seen.add(entry.slug)
                unique.append(entry)

        return unique

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
