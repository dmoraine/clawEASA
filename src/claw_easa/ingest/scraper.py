from __future__ import annotations

import hashlib
import logging
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

        filename = download_url.rsplit("/", 1)[-1] or f"{source.slug}.docx"
        local_path = target_dir / filename

        log.info("Downloading %s -> %s", download_url, local_path)
        resp = requests.get(download_url, timeout=120, stream=True)
        resp.raise_for_status()

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

    def _resolve_download_url(self, source: SourceSpec) -> str:
        resp = requests.get(source.page_url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.endswith(".docx") or "download" in href.lower():
                if not href.startswith("http"):
                    href = f"https://www.easa.europa.eu{href}"
                return href

        raise ValueError(
            f"Could not resolve download URL for {source.slug} from {source.page_url}"
        )
