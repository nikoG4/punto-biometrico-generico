from __future__ import annotations

import numpy as np


class VectorIndex:
    def __init__(self, dimension: int = 512):
        self.dimension = dimension
        self.employee_ids: list[int] = []
        self.names: list[str] = []
        self._matrix = np.empty((0, dimension), dtype=np.float32)
        self._faiss = None
        self._index = None
        try:
            import faiss

            self._faiss = faiss
            self._index = faiss.IndexFlatIP(dimension)
        except Exception:
            self._faiss = None
            self._index = None

    def rebuild(self, vectors: list[np.ndarray], employee_ids: list[int], names: list[str]) -> None:
        self.employee_ids = employee_ids
        self.names = names
        if not vectors:
            self._matrix = np.empty((0, self.dimension), dtype=np.float32)
            if self._faiss:
                self._index = self._faiss.IndexFlatIP(self.dimension)
            return
        matrix = np.vstack([self._normalize(v) for v in vectors]).astype(np.float32)
        self._matrix = matrix
        if self._faiss:
            self._index = self._faiss.IndexFlatIP(self.dimension)
            self._index.add(matrix)

    def search(self, vector: np.ndarray, top_k: int = 1) -> list[tuple[int, str, float]]:
        if not self.employee_ids:
            return []
        query = self._normalize(vector).reshape(1, -1).astype(np.float32)
        if self._index is not None:
            scores, indexes = self._index.search(query, top_k)
            return [
                (self.employee_ids[i], self.names[i], float(score))
                for score, i in zip(scores[0], indexes[0])
                if i >= 0
            ]
        scores = self._matrix @ query[0]
        best_indexes = np.argsort(scores)[::-1][:top_k]
        return [(self.employee_ids[i], self.names[i], float(scores[i])) for i in best_indexes]

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector
