from __future__ import annotations

from collections import deque

from app.config import AppConfig
from app.core.types import FaceObservation


class LivenessChecker:
    def __init__(self, config: AppConfig):
        self.config = config
        self._recent_centers: deque[tuple[float, float]] = deque(maxlen=12)

    def update(self, observation: FaceObservation | None) -> tuple[bool, str]:
        if not self.config.recognition.anti_spoofing_required:
            return True, "OK"
        if observation is None:
            return False, "Sin rostro"

        x1, y1, x2, y2 = observation.bbox
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        self._recent_centers.append(center)

        if self.config.recognition.movement_required and len(self._recent_centers) >= 6:
            xs = [p[0] for p in self._recent_centers]
            ys = [p[1] for p in self._recent_centers]
            moved = (max(xs) - min(xs)) > 8 or (max(ys) - min(ys)) > 8
            if not moved:
                return False, "Mueva levemente la cabeza"

        if self.config.recognition.blink_required:
            return False, "Parpadeo pendiente"

        return True, "OK"
