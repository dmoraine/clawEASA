from __future__ import annotations

import re

_LIST_ITEM_RE = re.compile(
    r'^[ \t]*[\-\u2013\u2022]?\s*\((\d+)\)\s+',
    re.MULTILINE,
)

_SUBHEADING_RE = re.compile(
    r'^((?:AMC|GM|CS)\d*\s+[A-Z][A-Za-z0-9._]+(?:\([a-z]\))?\s+\S.{2,100})\s*$',
    re.MULTILINE,
)

_MIN_BODY_LENGTH = 800
_MIN_ITEMS = 3
_MIN_SUBHEADING_BODY = 500
_MIN_SUBHEADINGS = 2


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


def build_subheading_chunks(entry: dict) -> list[dict]:
    """Split entries containing embedded regulatory sub-headings (CS, AMC, GM).

    Some EASA entries embed multiple logical regulatory units under a single
    parent entry.  This detects those sub-headings and creates separate chunks
    with proper sub-reference attribution for finer-grained retrieval.
    """
    body = entry.get("body_text", "") or ""
    if len(body) < _MIN_SUBHEADING_BODY:
        return []

    matches = list(_SUBHEADING_RE.finditer(body))
    if len(matches) < _MIN_SUBHEADINGS:
        return []

    parent_ref = entry.get("entry_ref", "").strip()
    prefix = _context_prefix(entry)
    bc = _breadcrumbs(entry)
    chunks: list[dict] = []

    for i, match in enumerate(matches):
        subheading = match.group(1).strip()
        if parent_ref and subheading.lower().startswith(parent_ref.lower()):
            continue

        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_text = body[match.start():end].strip()
        if not section_text or len(section_text) < 20:
            continue

        chunk_text = f"{prefix}\n{section_text}"
        chunks.append({
            "entry_id": entry["id"],
            "chunk_index": 200 + i,
            "chunk_kind": "subheading",
            "breadcrumbs_text": f"{bc} > {subheading}",
            "chunk_text": chunk_text,
            "token_estimate": _estimate_tokens(chunk_text),
        })

    return chunks
