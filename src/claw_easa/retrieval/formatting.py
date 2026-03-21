from __future__ import annotations


def compact_snippet(text: str, max_lines: int = 3, max_chars: int = 260) -> str:
    if not text:
        return ""
    lines = text.strip().split("\n")
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
