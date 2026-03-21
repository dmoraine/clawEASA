"""Tests for the catalog resolver — no network required."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from claw_easa.ingest.catalog import CatalogEntry, EasyAccessRulesCatalogScraper


FAKE_ENTRIES = [
    CatalogEntry(
        slug="air-operations-regulation-eu-no-9652012",
        title="Easy Access Rules for Air Operations (Regulation (EU) No 965/2012)",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-air-operations-regulation-eu-no-9652012",
    ),
    CatalogEntry(
        slug="aircrew-regulation-eu-no-11782011",
        title="Easy Access Rules for Aircrew (Regulation (EU) No 1178/2011)",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-aircrew-regulation-eu-no-11782011",
    ),
    CatalogEntry(
        slug="basic-regulation-regulation-eu-20181139",
        title="Easy Access Rules for the Basic Regulation (Regulation (EU) 2018/1139)",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-basic-regulation-regulation-eu-20181139",
    ),
    CatalogEntry(
        slug="continuing-airworthiness-regulation-eu-no",
        title="Easy Access Rules for Continuing Airworthiness",
        page_url="https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-continuing-airworthiness-regulation-eu-no",
    ),
]


@pytest.fixture
def scraper(tmp_path: Path) -> EasyAccessRulesCatalogScraper:
    s = EasyAccessRulesCatalogScraper(cache_dir=tmp_path)
    return s


class TestResolve:
    def _patched(self, scraper):
        return patch.object(scraper, "_scrape", return_value=list(FAKE_ENTRIES))

    def test_exact_catalog_slug(self, scraper):
        with self._patched(scraper):
            entry = scraper.resolve("aircrew-regulation-eu-no-11782011")
        assert entry.slug == "aircrew-regulation-eu-no-11782011"
        assert "1178/2011" in entry.title

    def test_alias_air_ops(self, scraper):
        with self._patched(scraper):
            entry = scraper.resolve("air-ops")
        assert "air-operations" in entry.slug
        assert entry.page_url.startswith("https://")

    def test_alias_aircrew(self, scraper):
        with self._patched(scraper):
            entry = scraper.resolve("aircrew")
        assert "aircrew" in entry.slug

    def test_alias_basic_regulation(self, scraper):
        with self._patched(scraper):
            entry = scraper.resolve("basic-regulation")
        assert "basic-regulation" in entry.slug

    def test_substring_fallback(self, scraper):
        with self._patched(scraper):
            entry = scraper.resolve("continuing-airworthiness")
        assert "continuing-airworthiness" in entry.slug

    def test_alias_fallback_url(self, scraper):
        """basic-regulation is not on the catalog page; resolved via fallback."""
        with self._patched(scraper):
            entry = scraper.resolve("basic-regulation")
        assert "basic-regulation" in entry.page_url

    def test_unknown_slug_raises(self, scraper):
        with self._patched(scraper):
            with pytest.raises(ValueError, match="not found"):
                scraper.resolve("nonexistent-regulation")


class TestCache:
    def test_cache_is_written_and_read(self, scraper, tmp_path):
        with patch.object(scraper, "_scrape", return_value=list(FAKE_ENTRIES)) as mock:
            first = scraper.discover(force_refresh=True)
            second = scraper.discover()

        assert mock.call_count == 1
        assert len(first) == len(second)
        assert (tmp_path / ".ear_catalog_cache.json").exists()

    def test_cache_expires(self, scraper, tmp_path):
        cache_path = tmp_path / ".ear_catalog_cache.json"
        cache_path.write_text(json.dumps({
            "ts": time.time() - 7200,
            "entries": [],
        }))

        with patch.object(scraper, "_scrape", return_value=list(FAKE_ENTRIES)) as mock:
            entries = scraper.discover()

        assert mock.call_count == 1
        assert len(entries) == len(FAKE_ENTRIES)

    def test_force_refresh_bypasses_cache(self, scraper, tmp_path):
        with patch.object(scraper, "_scrape", return_value=list(FAKE_ENTRIES)) as mock:
            scraper.discover(force_refresh=True)
            scraper.discover(force_refresh=True)

        assert mock.call_count == 2
