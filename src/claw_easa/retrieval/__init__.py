"""Retrieval layer for clawEASA."""

from .pipeline import build_index, vector_lookup
from .query_profile import build_query_profile
from .rewrite import rewrite_query
from .router import normalize_query, route_query
from .service import hybrid, lookup, refs, snippets
from .survey import shape_survey_results

__all__ = [
    "build_index",
    "build_query_profile",
    "vector_lookup",
    "hybrid",
    "lookup",
    "normalize_query",
    "rewrite_query",
    "refs",
    "route_query",
    "shape_survey_results",
    "snippets",
]
