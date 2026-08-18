"""Canonical EASA rule references.

A rule reference is the dotted code EASA cites a requirement by: ``M.A.201``,
``CAMO.A.200``, ``ORO.FTL.110``, ``IS.I.OR.200``, ``145.A.30``.  It may carry
an ``AMC1``/``GM2``/``CS`` prefix and a trailing paragraph selector such as
``(a)(1)``.

The same shape is needed in two places, so it lives here rather than in
either of them:

- the parser extracts the reference out of a heading, dropping the title that
  follows it;
- retrieval resolves a user-typed reference against stored entries, including
  corpora persisted before the parser knew how to strip that title.
"""
from __future__ import annotations

import re

# The reference itself, without any prefix.  Either a letter-led code
# ('M', 'ML', 'CAMO', 'ORO') or a numeric part code ('145', '21', '66'),
# followed by at least one dotted component.
REFERENCE_CORE = (
    r'(?:[A-Z]+[A-Z0-9]*|[0-9]+)(?:\.[A-Z0-9-]+)+'
    r'(?:\([^)]*\)(?:;\([^)]*\))*)?'
)

# 'AMC ORO.GEN.200', 'CS MMEL.050' — a bare document-type word.  The parser
# uses this one: it decides an entry's type from the paragraph style, so a
# numbered 'AMC1 ' prefix must stay out of its implementing-rule pattern.
REFERENCE_PREFIX = r'(?:[A-Z]{2,}\s+)?'

# Resolving a stored or user-typed reference also has to accept the numbered
# forms, 'AMC1 M.A.201' and 'GM2 CAMO.A.200'.
_CANONICAL_PREFIX = r'(?:(?:AMC|GM)\d+\s+|[A-Z]{2,}\s+)?'

REFERENCE_PATTERN = re.compile(f'^({_CANONICAL_PREFIX}{REFERENCE_CORE})')

_TRAILING_PUNCTUATION = '.,;:'


def normalize_reference_text(value: str) -> str:
    """Collapse whitespace and non-breaking spaces in *value*."""
    return re.sub(r'\s+', ' ', (value or '').replace('\xa0', ' ')).strip()


def canonical_reference(value: str) -> str:
    """The canonical rule reference at the start of *value*, or ``''``.

    ``'M.A.201 Responsibilities'`` and ``'M.A.201#2'`` both canonicalise to
    ``'M.A.201'``, so an entry stored under either form still answers a
    lookup of ``M.A.201``.
    """
    match = REFERENCE_PATTERN.match(normalize_reference_text(value))
    if not match:
        return ''
    return match.group(1).strip(_TRAILING_PUNCTUATION + ' ')


def is_reference_query(value: str) -> bool:
    """True when *value* is a bare reference rather than free text.

    ``'ORO.FTL'`` and ``'IS.I.OR.200'`` are reference queries; ``'crew
    fatigue'`` and ``'ORO.FTL.110 rest periods'`` are not.
    """
    text = normalize_reference_text(value).strip(_TRAILING_PUNCTUATION + ' ')
    if not text:
        return False
    return canonical_reference(text) == text


def like_escape(value: str) -> str:
    """Escape LIKE wildcards in *value* (use with ``ESCAPE '\\'``)."""
    return (
        value.replace('\\', '\\\\')
        .replace('%', '\\%')
        .replace('_', '\\_')
    )
