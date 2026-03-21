"""Shared HTTP session for EASA website requests.

The EASA CDN often returns 403 while still delivering content.
This module provides a pre-configured session and a ``get`` helper
that only raises on truly failed requests (empty body or 5xx).
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; claw-easa/1.0; +https://github.com/openclaw)"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = _USER_AGENT
    return s


def get(url: str, *, timeout: int = 30, stream: bool = False) -> requests.Response:
    """GET with User-Agent and lenient status handling.

    Raises only on 5xx or empty responses.  EASA frequently returns
    403 while still delivering the full HTML page.
    """
    resp = _session().get(url, timeout=timeout, stream=stream)

    if resp.status_code >= 500:
        resp.raise_for_status()

    if not stream and resp.status_code != 200:
        if len(resp.content) < 500:
            resp.raise_for_status()
        log.debug(
            "EASA returned %d for %s but content looks valid (%d bytes)",
            resp.status_code, url, len(resp.content),
        )

    return resp
