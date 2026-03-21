from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None


class FAISSStore:
    def __init__(self, index_path: Path, dimension: int = 384) -> None:
        self.index_path = index_path
        self.dimension = dimension
        self._index = None

    def build(self, vectors: np.ndarray) -> None:
        if faiss is None:
            raise ImportError("faiss-cpu is required for vector search")

        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = vectors / norms

        self._index = faiss.IndexFlatIP(self.dimension)
        self._index.add(normalized.astype(np.float32))

    def save(self) -> None:
        if self._index is None:
            raise RuntimeError("No index to save — call build() first")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self.index_path))

    def load(self) -> None:
        if faiss is None:
            raise ImportError("faiss-cpu is required for vector search")
        if not self.index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {self.index_path}")
        self._index = faiss.read_index(str(self.index_path))

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[tuple[int, float]]:
        if self._index is None:
            self.load()

        norm = np.linalg.norm(query_vector)
        if norm > 0:
            query_vector = query_vector / norm
        query_vector = query_vector.reshape(1, -1).astype(np.float32)

        scores, indices = self._index.search(query_vector, top_k)
        results = []
        for i, idx in enumerate(indices[0]):
            if idx >= 0:
                results.append((int(idx), float(scores[0][i])))
        return results

    @property
    def ntotal(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal
