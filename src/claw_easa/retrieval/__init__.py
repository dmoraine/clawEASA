"""Retrieval layer for clawEASA.

Keep package import lightweight: do not eagerly import FAISS/vector/indexing modules
from here, because simple CLI commands (lookup/refs/router) should not load heavy
optional dependencies at import time.
"""

__all__: list[str] = []
