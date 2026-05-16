from __future__ import annotations

import numpy as np


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    return np.asarray(embedding, dtype=np.float32).tobytes()


def embedding_from_bytes(data: bytes, dimension: int) -> np.ndarray:
    vector = np.frombuffer(data, dtype=np.float32)
    if vector.size != dimension:
        raise ValueError(f"Invalid embedding dimension: expected {dimension}, got {vector.size}")
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector
