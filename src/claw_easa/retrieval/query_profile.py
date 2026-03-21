from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class QueryProfile:
    concept_buckets: tuple[str, ...] = ()
    entity_hints: tuple[str, ...] = ()
    breadth: str = "narrow"


ENTITY_PATTERNS = [
    (r'\b(ORO|CAT|SPA|ARO|ORA|ARA|MED|FCL|ATCO|ATS|ADR|SERA)\b', 'regulation_domain'),
    (r'\b(FTL|flight time|duty time|rest)\b', 'ftl'),
    (r'\b(crew|pilot|commander|co-pilot|cabin crew)\b', 'crew'),
    (r'\b(operator|airline|AOC holder)\b', 'operator'),
    (r'\b(medical|fitness|health)\b', 'medical'),
    (r'\b(training|examination|proficiency)\b', 'training'),
]


def build_query_profile(query: str) -> QueryProfile:
    entities: list[str] = []

    for pattern, label in ENTITY_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            entities.append(label)

    words = query.lower().split()
    broad_markers = ("all", "every", "responsibilities", "requirements", "obligations")
    breadth = "broad" if len(words) >= 5 or any(w in words for w in broad_markers) else "narrow"

    return QueryProfile(
        concept_buckets=(),
        entity_hints=tuple(entities),
        breadth=breadth,
    )
