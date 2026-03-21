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
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                hasher.update(chunk)

        return DownloadedSource(
            checksum=hasher.hexdigest(),
            file_kind="primary",
            local_path=local_path,
            download_url=download_url,
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
