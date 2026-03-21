from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FTSQuery:
    match_expr: str
    has_terms: bool


_PHRASE_RE = re.compile(r'"([^"]+)"')
_WORD_RE = re.compile(r'[A-Za-z0-9_]+')


def to_fts5_query(query: str) -> FTSQuery:
    if not query or not query.strip():
        return FTSQuery(match_expr="", has_terms=False)

    phrases: list[str] = []
    for m in _PHRASE_RE.finditer(query):
        phrases.append(f'"{m.group(1)}"')

    remainder = _PHRASE_RE.sub("", query)

    terms: list[str] = []
    negated: list[str] = []

    tokens = remainder.split()
    negate_next = False
    for token in tokens:
        lower = token.lower()
        if lower == "or":
            if terms:
                terms.append("OR")
            continue

        if lower in ("-", "not") or token.startswith("-"):
            if token.startswith("-") and len(token) > 1:
                word = token[1:]
                words = _WORD_RE.findall(word)
                negated.extend(words)
            else:
                negate_next = True
            continue

        if negate_next:
            words = _WORD_RE.findall(token)
            negated.extend(words)
            negate_next = False
            continue

        words = _WORD_RE.findall(token)
        terms.extend(words)

    parts: list[str] = []
    parts.extend(phrases)
    parts.extend(terms)
    for neg in negated:
        parts.append(f"NOT {neg}")

    expr = " ".join(parts)
    has_terms = bool(phrases or terms)

    return FTSQuery(match_expr=expr.strip(), has_terms=has_terms)
