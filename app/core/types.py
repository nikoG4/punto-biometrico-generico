from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class FaceObservation:
    bbox: tuple[int, int, int, int]
    embedding: np.ndarray
    confidence: float
    landmarks: np.ndarray | None = None
    yaw_hint: float | None = None


@dataclass(slots=True)
class RecognitionMatch:
    employee_id: int
    name: str
    score: float
    bbox: tuple[int, int, int, int] | None = None
