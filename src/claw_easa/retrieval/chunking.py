from __future__ import annotations

import re

_LIST_ITEM_RE = re.compile(
    r'^[ \t]*[\-\u2013\u2022]?\s*\((\d+)\)\s+',
    re.MULTILINE,
)

_MIN_BODY_LENGTH = 800
_MIN_ITEMS = 3


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _breadcrumbs(entry: dict) -> str:
    return " > ".join(
        filter(None, [
            entry.get("slug", ""),
            entry.get("part_code", ""),
            entry.get("subpart_code", ""),
            entry.get("section_title", ""),
        ])
    )


def _context_prefix(entry: dict) -> str:
    return f"{entry['entry_ref']} ({entry['entry_type']}) — {entry['title']}"


def build_whole_entry_chunk(entry: dict) -> dict:
    body = entry.get("body_text", "") or ""
    chunk_text = f"{_context_prefix(entry)}\n{body}".strip()
    return {
        "entry_id": entry["id"],
        "chunk_index": 0,
        "chunk_kind": "whole",
        "breadcrumbs_text": _breadcrumbs(entry),
        "chunk_text": chunk_text,
        "token_estimate": _estimate_tokens(chunk_text),
    }


def build_list_item_chunks(entry: dict) -> list[dict]:
    """Split long entries containing numbered list items into individual chunks.

    Each chunk includes the parent entry context (ref + title) followed by
    the text of a single list item, making it a focused retrieval unit.
    The whole-entry chunk is still created separately.
    """
    body = entry.get("body_text", "") or ""
    if len(body) < _MIN_BODY_LENGTH:
        return []

    matches = list(_LIST_ITEM_RE.finditer(body))
    if len(matches) < _MIN_ITEMS:
        return []

    prefix = _context_prefix(entry)
    bc = _breadcrumbs(entry)
    chunks: list[dict] = []

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        item_text = body[match.start():end].strip()
        if not item_text:
            continue
        chunk_text = f"{prefix}\n{item_text}"
        chunks.append({
            "entry_id": entry["id"],
            "chunk_index": i + 1,
            "chunk_kind": "list_item",
            "breadcrumbs_text": bc,
            "chunk_text": chunk_text,
            "token_estimate": _estimate_tokens(chunk_text),
        })

    return chunks
