"""Tests for the shared scraper helpers and the browser fetcher's guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claw_easa.ingest.scraper import pick_filename, reject_non_document, select_download_url
from claw_easa.ingest.scraper_browser import BrowserSourceFetcher
from claw_easa.ingest.sources import SourceSpec


# --- select_download_url ----------------------------------------------------

def test_select_prefers_xml_download():
    html = """
    <a href="/en/downloads/100/en">Easy Access Rules (PDF)</a>
    <a href="/en/downloads/200/en">Easy Access Rules (XML)</a>
    """
    assert select_download_url(html) == "https://www.easa.europa.eu/en/downloads/200/en"


def test_select_falls_back_to_pdf():
    html = '<a href="/en/downloads/100/en">Easy Access Rules (PDF)</a>'
    assert select_download_url(html) == "https://www.easa.europa.eu/en/downloads/100/en"


def test_select_absolute_href_preserved():
    html = '<a href="https://cdn.example/file.xml">download XML</a>'
    # not under /downloads/ but ends with .xml -> fallback
    assert select_download_url(html) == "https://cdn.example/file.xml"


def test_select_returns_none_when_no_candidate():
    assert select_download_url("<a href='/about'>About</a>") is None


# --- pick_filename ----------------------------------------------------------

def test_pick_filename_from_disposition():
    assert pick_filename('attachment; filename="EAR.zip"', "https://x/en", "application/zip", "air-ops") == "EAR.zip"


def test_pick_filename_from_url_leaf():
    assert pick_filename("", "https://x/EAR-Air-Ops.xml", "", "air-ops") == "EAR-Air-Ops.xml"


def test_pick_filename_from_content_type():
    # leaf is "en" -> ignored, fall back to content-type extension
    assert pick_filename("", "https://x/downloads/136682/en", "application/zip", "air-ops") == "air-ops.zip"
    assert pick_filename("", "https://x/downloads/1/en", "text/xml", "aircrew") == "aircrew.xml"


def test_pick_filename_defaults_to_bin():
    assert pick_filename("", "https://x/downloads/1/en", "application/octet-stream", "x") == "x.bin"


# --- reject_non_document ----------------------------------------------------

def test_reject_challenge_page(tmp_path):
    f = tmp_path / "doc.bin"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="bot-challenge"):
        reject_non_document(f, "http://easa/dl", "text/html", b"<!DOCTYPE html><title>Client Challenge</title>")
    assert not f.exists()  # bogus file removed


def test_reject_generic_html(tmp_path):
    f = tmp_path / "doc.bin"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="HTML page"):
        reject_non_document(f, "http://easa/dl", "text/html", b"<html><body>nope</body></html>")
    assert not f.exists()


def test_reject_accepts_real_document(tmp_path):
    f = tmp_path / "doc.zip"
    body = b"PK\x03\x04rest"
    f.write_bytes(body)
    # Must not raise and must leave the file in place.
    reject_non_document(f, "http://easa/dl", "application/zip", body)
    assert f.exists()


# --- BrowserSourceFetcher guard --------------------------------------------

def test_browser_fetch_without_playwright_raises(tmp_path, monkeypatch):
    # Force the playwright import to fail regardless of whether it is installed.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    source = SourceSpec(slug="air-ops", source_family="ear", title="Air Ops",
                        source_url="https://www.easa.europa.eu/en/downloads/136682/en")
    with pytest.raises(RuntimeError, match="playwright"):
        BrowserSourceFetcher().fetch(source, Path(tmp_path))
