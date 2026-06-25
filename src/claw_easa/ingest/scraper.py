from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from claw_easa.ingest.sources import SourceSpec

log = logging.getLogger(__name__)


@dataclass
class DownloadedSource:
    checksum: str
    file_kind: str
    local_path: Path
    download_url: str


class EASASourceFetcher:
    def fetch(self, source: SourceSpec, data_dir: Path) -> DownloadedSource:
        download_url = source.source_url or self._resolve_download_url(source)

        target_dir = data_dir / "downloads" / source.slug
        target_dir.mkdir(parents=True, exist_ok=True)

        from claw_easa.ingest import http
        log.info("Downloading %s", download_url)
        resp = http.get(download_url, timeout=120, stream=True)

        filename = self._filename_from_response(resp, source.slug)
        local_path = target_dir / filename
        log.info("Saving to %s", local_path)

        hasher = hashlib.sha256()
        first_chunk = b""
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if not first_chunk:
                    first_chunk = chunk
                f.write(chunk)
                hasher.update(chunk)

        self._reject_non_document(
            local_path, download_url, resp, first_chunk
        )

        return DownloadedSource(
            checksum=hasher.hexdigest(),
            file_kind="primary",
            local_path=local_path,
            download_url=download_url,
        )

    @staticmethod
    def _reject_non_document(
        local_path: Path,
        download_url: str,
        resp: requests.Response,
        first_chunk: bytes,
    ) -> None:
        """Fail loudly when EASA serves an HTML page instead of a document.

        EASA now fronts its downloads with a JavaScript bot-challenge
        (Fastly ``Client Challenge``).  A plain HTTP client cannot solve
        it, so the server returns a small HTML page with HTTP 200.  Left
        unchecked it would be saved as ``<slug>.bin`` and later crash the
        XML parser with a cryptic ``XMLSyntaxError``.  Detect it here and
        remove the bogus file so nothing downstream treats it as valid.
        """
        content_type = resp.headers.get("content-type", "").lower()
        head = first_chunk[:512].lstrip()
        head_lower = head.lower()

        is_html = (
            "text/html" in content_type
            or head_lower.startswith(b"<!doctype html")
            or head_lower.startswith(b"<html")
        )
        if not is_html:
            return

        local_path.unlink(missing_ok=True)

        challenge = (
            b"client challenge" in head_lower
            or b"_fs-ch-" in first_chunk
            or b"challenge" in head_lower
        )
        if challenge:
            raise RuntimeError(
                f"EASA returned a bot-challenge page instead of a document "
                f"for {download_url}. The EASA website is now behind a "
                f"JavaScript anti-bot challenge that this fetcher cannot "
                f"solve automatically. Download the file manually from the "
                f"EASA document library and ingest it with "
                f"'claw-easa ingest parse <slug>' against the local file."
            )
        raise RuntimeError(
            f"Expected a document but EASA served an HTML page "
            f"(content-type={content_type or 'unknown'}) for {download_url}."
        )

    def _filename_from_response(self, resp: requests.Response, slug: str) -> str:
        disposition = resp.headers.get("content-disposition", "")
        match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        if match:
            return match.group(1)

        final_url = str(resp.url)
        leaf = final_url.rstrip("/").rsplit("/", 1)[-1]
        if leaf and leaf.lower() != "en":
            return leaf

        content_type = resp.headers.get("content-type", "").lower()
        if "zip" in content_type:
            return f"{slug}.zip"
        if "xml" in content_type:
            return f"{slug}.xml"
        if "pdf" in content_type:
            return f"{slug}.pdf"
        return f"{slug}.bin"

    def _resolve_download_url(self, source: SourceSpec) -> str:
        from claw_easa.ingest import http
        resp = http.get(source.page_url)

        soup = BeautifulSoup(resp.text, "html.parser")

        preferred_links: list[str] = []
        fallback_links: list[str] = []

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            text = " ".join(link.get_text(" ", strip=True).split())
            href_lower = href.lower()
            text_lower = text.lower()

            if not href.startswith("http"):
                absolute_href = f"https://www.easa.europa.eu{href}"
            else:
                absolute_href = href

            if "xml" in text_lower and "/downloads/" in href_lower:
                preferred_links.append(absolute_href)
            elif any(token in text_lower for token in ["easy access rules", "pdf", "download"]) and "/downloads/" in href_lower:
                fallback_links.append(absolute_href)
            elif href_lower.endswith((".xml", ".docx", ".pdf")):
                fallback_links.append(absolute_href)

        if preferred_links:
            return preferred_links[0]
        if fallback_links:
            return fallback_links[0]

        raise ValueError(
            f"Could not resolve download URL for {source.slug} from {source.page_url}"
        )
