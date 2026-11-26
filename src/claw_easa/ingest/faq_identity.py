"""Content-based FAQ identity (pure, no I/O).

The normalisation rule N1 is frozen: it defines every stable FAQ id, so
changing it means a new migration (see docs/faq-dedup-design.md, section 2).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")
_LEGACY_REF = re.compile(r"^FAQ-(.+)-(\d{3,})$")


class StableIdCollision(ValueError):
    """Two different texts would share one stable id (hash8 collision)."""


def _is_edge(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def normalise(text: str) -> str:
    """N1: NFKC, casefold, collapse whitespace, strip edge whitespace/punctuation."""
    text = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold())
    start, end = 0, len(text)
    while start < end and _is_edge(text[start]):
        start += 1
    while end > start and _is_edge(text[end - 1]):
        end -= 1
    return text[start:end]


def content_hash(title: str, body: str) -> str:
    """sha256(N1(title) + "\\n" + N1(body)) as 64 hex chars."""
    payload = normalise(title or "") + "\n" + normalise(body or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash8(full_hash: str) -> str:
    return full_hash[:8]


def assign_stable_ids(prefix: str, hashes: Iterable[str]) -> list[str]:
    """Stable ids for *hashes* (in order) under one prefix.

    The first occurrence of a text gets ``{prefix}-{hash8}``; repeats of the
    same text get ``-2``, ``-3``...  A different text sharing the hash8 raises.
    """
    owner: dict[str, str] = {}
    seen: dict[str, int] = {}
    ids: list[str] = []
    for full in hashes:
        short = hash8(full)
        if owner.setdefault(short, full) != full:
            raise StableIdCollision(f"hash8 collision under {prefix}: {short}")
        seen[full] = seen.get(full, 0) + 1
        base = f"{prefix}-{short}"
        ids.append(base if seen[full] == 1 else f"{base}-{seen[full]}")
    return ids


def parse_legacy_ref(ref: str) -> tuple[str, int] | None:
    """``FAQ-{slug}-NNN`` -> ``("FAQ-{slug}", NNN)``; anything else -> None."""
    match = _LEGACY_REF.match(ref)
    if match is None:
        return None
    return f"FAQ-{match.group(1)}", int(match.group(2))
