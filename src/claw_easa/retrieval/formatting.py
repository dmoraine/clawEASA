from __future__ import annotations

import re

_SNIPPET_WORD_RE = re.compile(r'[a-z]{3,}')


def compact_snippet(
    text: str, max_lines: int = 3, max_chars: int = 260, *, query: str = "",
) -> str:
    if not text:
        return ""
    lines = text.strip().split("\n")

    if query and len(lines) > max_lines:
        query_words = set(_SNIPPET_WORD_RE.findall(query.lower()))
        if query_words:
            best_idx = _best_matching_line(lines, query_words)
            if best_idx > 0:
                start = max(0, best_idx - 1)
                lines = lines[start:]

    selected: list[str] = []
    total_chars = 0
    for line in lines:
        if len(selected) >= max_lines:
            break
        line = line.strip()
        if not line:
            continue
        if total_chars + len(line) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 20:
                selected.append(line[:remaining] + "...")
            break
        selected.append(line)
        total_chars += len(line) + 1
    return " | ".join(selected)


def _best_matching_line(lines: list[str], query_words: set[str]) -> int:
    best_idx = 0
    best_score = 0
    for i, line in enumerate(lines):
        line_lower = line.lower()
        score = sum(1 for w in query_words if w in line_lower)
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx
