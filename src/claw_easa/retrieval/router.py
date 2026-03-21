from __future__ import annotations

from dataclasses import dataclass
import re

from claw_easa.retrieval.hybrid import looks_like_reference


@dataclass(frozen=True)
class RoutedQuery:
    raw_query: str
    normalized_query: str
    intent: str
    strict: bool = False


WRAPPER_PATTERNS = [
    r'^what does easa say about\s+',
    r'^what does the regulation say about\s+',
    r'^what do the rules say about\s+',
    r'^show me\s+',
    r'^tell me about\s+',
    r'^quelles sont les règles sur\s+',
    r'^que dit easa sur\s+',
    r'^que dit la réglementation sur\s+',
]


REFS_PATTERNS = [r'\brefs?\b', r'\breferences?\b', r'quelles références', r'which references']
SNIPPET_PATTERNS = [r'\bsnippets?\b', r'\bextracts?\b', r'\bshow text\b', r'\bquote\b']
SURVEY_PATTERNS = [
    r'\ball\b', r'\btoutes?\b', r'\bresponsibilities\b',
    r'\brequirements\b', r'\bobligations\b',
]


def normalize_query(query: str) -> str:
    q = re.sub(r'\s+', ' ', query).strip()
    for pattern in WRAPPER_PATTERNS:
        q = re.sub(pattern, '', q, flags=re.IGNORECASE).strip()
    q = re.sub(r'^[\s:,\-]+', '', q).strip()
    return q or query.strip()


def _matches_any(query: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, query, flags=re.IGNORECASE) for pattern in patterns)


def route_query(query: str, strict: bool = False) -> RoutedQuery:
    normalized = normalize_query(query)
    lower = normalized.lower()

    if looks_like_reference(normalized):
        intent = 'exact_lookup'
    elif _matches_any(lower, REFS_PATTERNS):
        intent = 'refs_only'
    elif _matches_any(lower, SNIPPET_PATTERNS):
        intent = 'snippets'
    elif _matches_any(lower, SURVEY_PATTERNS):
        intent = 'survey'
    else:
        intent = 'answer'

    if strict and intent == 'answer':
        intent = 'refs_only'

    return RoutedQuery(
        raw_query=query,
        normalized_query=normalized,
        intent=intent,
        strict=strict,
    )
