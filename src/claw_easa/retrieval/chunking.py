from __future__ import annotations


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def build_whole_entry_chunk(entry: dict) -> dict:
    breadcrumbs = " > ".join(
        filter(None, [
            entry.get("slug", ""),
            entry.get("part_code", ""),
            entry.get("subpart_code", ""),
            entry.get("section_title", ""),
        ])
    )
    body = entry.get("body_text", "") or ""
    chunk_text = (
        f"{entry['entry_ref']} ({entry['entry_type']}) — {entry['title']}\n{body}"
    ).strip()

    return {
        "entry_id": entry["id"],
        "chunk_index": 0,
        "chunk_kind": "whole",
        "breadcrumbs_text": breadcrumbs,
        "chunk_text": chunk_text,
        "token_estimate": _estimate_tokens(chunk_text),
    }
