"""Headless-browser fetcher for EASA documents.

EASA fronts its website with a Fastly JavaScript bot-challenge that a
plain HTTP client cannot solve.  A real browser engine runs the challenge
script, obtains the verified cookie, and can then reach the file.  This
backend uses Playwright and is opt-in (``ingest fetch --browser``) because
it requires the optional ``playwright`` dependency plus a Chromium install
(``playwright install chromium``).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from claw_easa.ingest.scraper import (
    DownloadedSource,
    pick_filename,
    reject_non_document,
    select_download_url,
)
from claw_easa.ingest.sources import SourceSpec

log = logging.getLogger(__name__)

# Time allowed for the Fastly challenge to clear and the real page to load.
_CHALLENGE_TIMEOUT_MS = 45_000
_DOWNLOAD_TIMEOUT_MS = 120_000


class BrowserSourceFetcher:
    """Fetch EASA documents through a headless Chromium browser."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless

    def fetch(self, source: SourceSpec, data_dir: Path) -> DownloadedSource:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser fetching requires the optional 'playwright' dependency. "
                "Install it with: pip install 'claw-easa[browser]' && "
                "playwright install chromium"
            ) from exc

        target_dir = data_dir / "downloads" / source.slug
        target_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self._headless)
            try:
                context = browser.new_context()
                page = context.new_page()

                download_url = source.source_url or self._resolve_download_url(page, source)

                log.info("Downloading %s (browser)", download_url)
                # context.request reuses the cookies the challenge set on `page`.
                resp = context.request.get(download_url, timeout=_DOWNLOAD_TIMEOUT_MS)
                if not resp.ok:
                    raise RuntimeError(
                        f"Browser download failed: HTTP {resp.status} for {download_url}"
                    )

                body = resp.body()
                headers = resp.headers  # dict[str, str], lower-cased keys

                filename = pick_filename(
                    headers.get("content-disposition", ""),
                    resp.url,
                    headers.get("content-type", ""),
                    source.slug,
                )
                local_path = target_dir / filename
                log.info("Saving to %s", local_path)
                local_path.write_bytes(body)

                reject_non_document(
                    local_path, download_url, headers.get("content-type", ""), body[:512]
                )

                return DownloadedSource(
                    checksum=hashlib.sha256(body).hexdigest(),
                    file_kind="primary",
                    local_path=local_path,
                    download_url=download_url,
                )
            finally:
                browser.close()

    def _resolve_download_url(self, page, source: SourceSpec) -> str:
        if not source.page_url:
            raise ValueError(
                f"No page URL to resolve a download from for {source.slug}; "
                f"pass --url explicitly."
            )
        page.goto(source.page_url, wait_until="domcontentloaded", timeout=_CHALLENGE_TIMEOUT_MS)
        # Let the Fastly challenge run, set its cookie, and reload the real page.
        try:
            page.wait_for_load_state("networkidle", timeout=_CHALLENGE_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - networkidle can time out on busy pages
            log.debug("networkidle wait timed out; proceeding with current DOM")

        url = select_download_url(page.content())
        if url is None:
            raise ValueError(
                f"Could not resolve download URL for {source.slug} from {source.page_url}"
            )
        return url
