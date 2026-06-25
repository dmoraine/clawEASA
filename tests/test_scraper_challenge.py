"""Tests for the fetcher's bot-challenge / non-document detection.

EASA fronts its downloads with a JavaScript bot-challenge that returns a
small HTML page with HTTP 200.  The fetcher must reject such responses
instead of saving them as a document (which later crashes the parser).
"""
from __future__ import annotations

import pytest

from claw_easa.ingest import http, scraper
from claw_easa.ingest.sources import SourceSpec


_CHALLENGE_HTML = (
    b"<!DOCTYPE html>\n<html lang=\"en\">\n  <head>\n"
    b"    <title>Client Challenge</title>\n"
    b"  </head>\n  <body>\n"
    b"    <script src=\"/_fs-ch-abc/script.js\"></script>\n"
    b"  </body>\n</html>\n"
)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, url: str) -> None:
        self._body = body
        self.headers = {"content-type": content_type}
        self.url = url

    def iter_content(self, chunk_size: int = 8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def _patch_http(monkeypatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(http, "get", lambda *a, **k: response)


def test_rejects_bot_challenge_page(monkeypatch, tmp_path):
    resp = _FakeResponse(
        _CHALLENGE_HTML, "text/html; charset=utf-8",
        "https://www.easa.europa.eu/en/downloads/136682/en",
    )
    _patch_http(monkeypatch, resp)
    source = SourceSpec(
        slug="air-ops", source_family="ear", title="Air Operations",
        source_url="https://www.easa.europa.eu/en/downloads/136682/en",
    )

    with pytest.raises(RuntimeError, match="bot-challenge"):
        scraper.EASASourceFetcher().fetch(source, tmp_path)

    # The bogus file must not be left behind.
    assert not list((tmp_path / "downloads" / "air-ops").glob("*.bin"))


def test_rejects_generic_html_page(monkeypatch, tmp_path):
    resp = _FakeResponse(
        b"<html><body>Not found</body></html>", "text/html",
        "https://www.easa.europa.eu/en/downloads/1/en",
    )
    _patch_http(monkeypatch, resp)
    source = SourceSpec(
        slug="x", source_family="ear", title="X",
        source_url="https://www.easa.europa.eu/en/downloads/1/en",
    )

    with pytest.raises(RuntimeError, match="HTML page"):
        scraper.EASASourceFetcher().fetch(source, tmp_path)


def test_accepts_zip_document(monkeypatch, tmp_path):
    # PK\x03\x04 magic — a real ZIP archive payload.
    body = b"PK\x03\x04" + b"\x00" * 100
    resp = _FakeResponse(
        body, "application/zip",
        "https://www.easa.europa.eu/en/downloads/136682/en",
    )
    _patch_http(monkeypatch, resp)
    source = SourceSpec(
        slug="air-ops", source_family="ear", title="Air Operations",
        source_url="https://www.easa.europa.eu/en/downloads/136682/en",
    )

    result = scraper.EASASourceFetcher().fetch(source, tmp_path)
    assert result.local_path.exists()
    assert result.local_path.read_bytes() == body
