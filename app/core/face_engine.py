from __future__ import annotations

import cv2
import logging
import numpy as np
from pathlib import Path

from app.config import AppConfig
from app.core.types import FaceObservation


class FaceEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = "demo"
        self.last_error = ""
        self._app = None
        self._cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self._init_insightface()

    @property
    def dimension(self) -> int:
        return 512

    def _init_insightface(self) -> None:
        if self.config.recognition.provider != "insightface":
            return
        try:
            from insightface.app import FaceAnalysis

            model_root = self._local_model_root()
            kwargs = {"root": str(model_root)} if model_root else {}
            self._app = FaceAnalysis(
                name=self.config.recognition.model_name,
                providers=["CPUExecutionProvider"],
                **kwargs,
            )
            self._app.prepare(ctx_id=0, det_size=self.config.recognition.det_size)
            self.provider = "insightface"
        except Exception as exc:
            self.last_error = str(exc)
            logging.getLogger(__name__).warning("InsightFace no disponible, usando demo: %s", exc)
            self._app = None
            self.provider = "demo"

    def _local_model_root(self) -> Path | None:
        root = Path("models") / "insightface"
        model_dir = root / "models" / self.config.recognition.model_name
        return root if model_dir.exists() else None

    def extract(self, frame_bgr: np.ndarray) -> list[FaceObservation]:
        if self._app is not None:
            return self._extract_insightface(frame_bgr)
        return self._extract_demo(frame_bgr)

    def _extract_insightface(self, frame_bgr: np.ndarray) -> list[FaceObservation]:
        faces = self._app.get(frame_bgr)
        observations: list[FaceObservation] = []
        for face in faces:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            if min(x2 - x1, y2 - y1) < self.config.recognition.min_face_size:
                continue
            embedding = np.asarray(face.embedding, dtype=np.float32)
            embedding = self._normalize(embedding)
            observations.append(
                FaceObservation(
                    bbox=(x1, y1, x2, y2),
                    embedding=embedding,
                    confidence=float(getattr(face, "det_score", 1.0)),
                    landmarks=np.asarray(getattr(face, "kps", None)) if getattr(face, "kps", None) is not None else None,
                )
            )
        return observations

    def _extract_demo(self, frame_bgr: np.ndarray) -> list[FaceObservation]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
        observations: list[FaceObservation] = []
        for x, y, w, h in faces:
            crop = gray[y : y + h, x : x + w]
            embedding = self._demo_embedding_from_crop(crop)
            observations.append(FaceObservation(bbox=(x, y, x + w, y + h), embedding=embedding, confidence=0.75))
        return observations

    def _demo_embedding_from_crop(self, crop_gray: np.ndarray) -> np.ndarray:
        face = cv2.resize(crop_gray, (32, 32), interpolation=cv2.INTER_AREA)
        face = cv2.equalizeHist(face)
        face = face.astype(np.float32) / 255.0

        low_res = cv2.resize(face, (16, 16), interpolation=cv2.INTER_AREA).flatten()
        grad_x = cv2.Sobel(face, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(face, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = cv2.magnitude(grad_x, grad_y)
        gradient_features = cv2.resize(magnitude, (16, 16), interpolation=cv2.INTER_AREA).flatten()

        vector = np.concatenate([low_res, gradient_features]).astype(np.float32)
        vector -= vector.mean()
        return self._normalize(vector)

    def average_embeddings(self, embeddings: list[np.ndarray]) -> np.ndarray:
        if not embeddings:
            raise ValueError("No embeddings captured")
        return self._normalize(np.mean(np.vstack(embeddings), axis=0))

    def embedding_consistency(self, embeddings: list[np.ndarray]) -> float:
        if len(embeddings) < 2:
            return 1.0
        matrix = np.vstack([self._normalize(item) for item in embeddings]).astype(np.float32)
        centroid = self._normalize(matrix.mean(axis=0))
        return float(np.min(matrix @ centroid))

    def face_quality(self, frame_bgr: np.ndarray, bbox: tuple[int, int, int, int]) -> tuple[bool, str, float]:
        x1, y1, x2, y2 = bbox
        height, width = frame_bgr.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return False, "No se pudo recortar el rostro", 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if brightness < 42:
            return False, "Iluminacion muy baja", brightness
        if brightness > 225:
            return False, "Iluminacion demasiado fuerte", brightness
        if sharpness < 35:
            return False, "Imagen borrosa, mantengase quieto", sharpness
        quality = min(1.0, sharpness / 180.0) * 0.65 + min(1.0, brightness / 120.0) * 0.35
        return True, "Calidad correcta", quality

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector
