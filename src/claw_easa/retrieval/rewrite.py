from __future__ import annotations

import re

QUESTION_NOISE_PATTERNS = [
    r'\bwhat does easa say about\b',
    r'\bwhat does the regulation say about\b',
    r'\bwhat do the rules say about\b',
    r'\bwhich references talk about\b',
    r'\bwhich references discuss\b',
    r'\bwhat are all the\b',
    r'\bwhat are the\b',
    r'\bquelles références parlent de\b',
    r'\bque dit easa sur\b',
    r'\bque dit la réglementation sur\b',
]

TRAILING_NOISE_PATTERNS = [
    r'\?$',
]

REGULATION_CONTEXT_PATTERNS = [
    r'\s+du\s+R[èe]glement\s*(?:\(U[E|e]\)\s*)?(?:n[o°]?\s*)?\d+/\d+',
    r'\s+of\s+Regulation\s*(?:\(E[CU]\)\s*)?(?:No\.?\s*)?\d+/\d+',
    r'\s+Regulation\s*(?:\(E[CU]\)\s*)?(?:No\.?\s*)?\d+/\d+',
    r'\s+R[èe]glement\s*(?:\(U[E|e]\)\s*)?(?:n[o°]?\s*)?\d+/\d+',
    r'\s+de\s+la\s+r[ée]glementation\b.*$',
    r'\s+en\s+utilisant\s+\w+',
]


def rewrite_query(query: str, intent: str) -> str:
    q = re.sub(r'\s+', ' ', query).strip()
    rewritten = q

    for pattern in QUESTION_NOISE_PATTERNS:
        rewritten = re.sub(pattern, '', rewritten, flags=re.IGNORECASE).strip()

    if intent in ('exact_lookup', 'refs_only', 'answer', 'survey', 'snippets'):
        for pattern in REGULATION_CONTEXT_PATTERNS:
            rewritten = re.sub(pattern, '', rewritten, flags=re.IGNORECASE).strip()

    if intent == 'refs_only':
        rewritten = re.sub(r'\brefs?\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\breferences?\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\btalk about\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\bdiscuss\b', '', rewritten, flags=re.IGNORECASE).strip()

    if intent == 'survey':
        rewritten = re.sub(r'\ball\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\bthe\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\bof a\b', '', rewritten, flags=re.IGNORECASE).strip()
        rewritten = re.sub(r'\bof an\b', '', rewritten, flags=re.IGNORECASE).strip()

    for pattern in TRAILING_NOISE_PATTERNS:
        rewritten = re.sub(pattern, '', rewritten).strip()

    rewritten = re.sub(r'\s+', ' ', rewritten).strip(' ,:-')
    return rewritten or q
