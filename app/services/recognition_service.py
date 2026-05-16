from __future__ import annotations

from sqlalchemy import select

from app.config import AppConfig
from app.core.face_engine import FaceEngine
from app.core.types import RecognitionMatch
from app.core.vector_index import VectorIndex
from app.db.models import Employee, FaceEmbedding
from app.db.serialization import embedding_from_bytes, embedding_to_bytes
from app.db.session import DatabaseManager


class RecognitionService:
    def __init__(self, config: AppConfig, db: DatabaseManager, engine: FaceEngine):
        self.config = config
        self.db = db
        self.engine = engine
        self.index = VectorIndex(dimension=engine.dimension)
        self.refresh_index()

    def refresh_index(self) -> None:
        vectors = []
        employee_ids = []
        names = []
        with self.db.primary_session() as session:
            rows = session.execute(
                select(FaceEmbedding, Employee)
                .join(Employee, Employee.id == FaceEmbedding.employee_id)
                .where(Employee.active.is_(True))
            ).all()
            for face_embedding, employee in rows:
                vectors.append(embedding_from_bytes(face_embedding.embedding, face_embedding.dimension))
                employee_ids.append(employee.id)
                names.append(employee.name)
        self.index.rebuild(vectors, employee_ids, names)

    @property
    def registered_count(self) -> int:
        return len(self.index.employee_ids)

    def find_match(self, embedding) -> RecognitionMatch | None:
        candidate = self.best_candidate(embedding)
        if candidate is None:
            return None
        threshold = self.config.threshold
        if self.engine.provider == "demo":
            threshold = min(threshold, 0.45)
        if candidate.score < threshold:
            return None
        return candidate

    def find_duplicate(self, embedding, same_employee_id: int | None = None) -> RecognitionMatch | None:
        candidates = self.index.search(embedding, top_k=3)
        threshold = max(self.config.threshold, 0.72)
        if self.engine.provider == "demo":
            threshold = max(min(self.config.threshold, 0.55), 0.48)
        for employee_id, name, score in candidates:
            if same_employee_id is not None and employee_id == same_employee_id:
                continue
            if score >= threshold:
                return RecognitionMatch(employee_id=employee_id, name=name, score=score)
        return None

    def best_candidate(self, embedding) -> RecognitionMatch | None:
        candidates = self.index.search(embedding, top_k=1)
        if not candidates:
            return None
        employee_id, name, score = candidates[0]
        return RecognitionMatch(employee_id=employee_id, name=name, score=score)

    def register_embedding(self, employee_id: int, embedding, image_snapshot_path: str | None = None) -> FaceEmbedding:
        duplicate = self.find_duplicate(embedding, same_employee_id=employee_id)
        if duplicate:
            raise ValueError(f"El rostro parece pertenecer a {duplicate.name} ({duplicate.score:.2f})")
        with self.db.primary_session() as session:
            row = FaceEmbedding(
                employee_id=employee_id,
                embedding=embedding_to_bytes(embedding),
                dimension=self.engine.dimension,
                provider=self.engine.provider,
                image_snapshot_path=image_snapshot_path,
            )
            session.add(row)
            session.flush()
        self.refresh_index()
        return row
