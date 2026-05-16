from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.db.models import AttendanceLog, AttendanceType, Employee, FaceEmbedding, OfflineEvent
from app.db.session import DatabaseManager
from app.services.rrhh_service import RrhhService


class SyncService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def sync_pending(self) -> int:
        if not self.db.can_reach_primary():
            return 0
        synced = 0
        with self.db.local_session() as local:
            local_logs = list(local.scalars(select(AttendanceLog).where(AttendanceLog.synced.is_(False)).limit(100)))
            for log in local_logs:
                with self.db.remote_session() as primary:
                    primary.add(
                        AttendanceLog(
                            employee_id=log.employee_id,
                            timestamp=log.timestamp,
                            type=log.type,
                            device_id=log.device_id,
                            confidence=log.confidence,
                            image_snapshot_path=log.image_snapshot_path,
                            synced=True,
                        )
                    )
                log.synced = True
                synced += 1

            events = list(
                local.scalars(
                    select(OfflineEvent)
                    .where(OfflineEvent.event_type == "attendance_log", OfflineEvent.synced_at.is_(None))
                    .limit(100)
                )
            )
            for event in events:
                payload = json.loads(event.payload_json)
                with self.db.remote_session() as primary:
                    primary.add(
                        AttendanceLog(
                            employee_id=payload["employee_id"],
                            timestamp=datetime.fromisoformat(payload["timestamp"]),
                            type=AttendanceType(payload["type"]),
                            device_id=payload["device_id"],
                            confidence=float(payload["confidence"]),
                            image_snapshot_path=payload.get("image_snapshot_path"),
                            synced=True,
                        )
                    )
                event.synced_at = datetime.now()
                synced += 1
        return synced

    def queue_rrhh_attendance(
        self,
        marker_code: str,
        attendance_type: AttendanceType,
        timestamp: datetime,
        confidence: float,
    ) -> None:
        payload = {
            "marker_code": marker_code,
            "type": attendance_type.value,
            "timestamp": timestamp.isoformat(),
            "confidence": confidence,
        }
        with self.db.local_session() as session:
            session.add(OfflineEvent(event_type="rrhh_attendance", payload_json=json.dumps(payload)))

    def sync_rrhh_attendance(self, rrhh: RrhhService, limit: int = 100) -> int:
        if not rrhh.enabled:
            return 0
        synced = 0
        with self.db.local_session() as session:
            events = list(
                session.scalars(
                    select(OfflineEvent)
                    .where(OfflineEvent.event_type == "rrhh_attendance", OfflineEvent.synced_at.is_(None))
                    .order_by(OfflineEvent.created_at)
                    .limit(limit)
                )
            )
            for event in events:
                payload = json.loads(event.payload_json)
                ok, _message = rrhh.record_attendance(
                    payload.get("marker_code"),
                    AttendanceType(payload["type"]),
                    datetime.fromisoformat(payload["timestamp"]),
                    float(payload.get("confidence", 0.0)),
                )
                if ok:
                    event.synced_at = datetime.now()
                    synced += 1
        return synced

    def sync_biometric_faces(self, rrhh: RrhhService, limit: int = 100) -> int:
        if not rrhh.enabled:
            return 0
        synced = 0
        synced += self.sync_rrhh_attendance(rrhh)
        synced += self.sync_linked_biometric_faces(rrhh, limit=limit)
        synced += self.sync_unlinked_biometric_faces(rrhh, limit=limit)
        with self.db.primary_session() as session:
            rows = session.execute(
                select(FaceEmbedding, Employee)
                .join(Employee, Employee.id == FaceEmbedding.employee_id)
                .where(FaceEmbedding.image_snapshot_path.is_not(None))
                .order_by(FaceEmbedding.created_at.desc())
                .limit(limit)
            ).all()
            for face_embedding, employee in rows:
                marker_code = employee.document_id if employee.external_id else None
                if rrhh.upload_biometric_face(
                    local_employee_id=employee.id,
                    local_face_embedding_id=face_embedding.id,
                    person_name=employee.name,
                    marker_code=marker_code,
                    embedding=face_embedding.embedding,
                    dimension=face_embedding.dimension,
                    provider=face_embedding.provider,
                    image_snapshot_path=face_embedding.image_snapshot_path,
                ):
                    pass
        return synced

    def sync_linked_biometric_faces(self, rrhh: RrhhService, limit: int = 500) -> int:
        if not rrhh.enabled:
            return 0
        linked_faces = rrhh.list_linked_biometric_faces(limit=limit)
        if not linked_faces:
            return 0
        updated = 0
        prefix = "SCT" if rrhh.mode == "sct" else "GENERIC"
        with self.db.primary_session() as session:
            for linked in linked_faces:
                face_embedding = session.get(FaceEmbedding, linked.local_face_embedding_id)
                if face_embedding is None:
                    continue
                employee = session.get(Employee, face_embedding.employee_id)
                if employee is None:
                    continue
                external_id = f"{prefix}:{linked.rrhh_employee_id}"
                target = session.scalar(
                    select(Employee).where(
                        Employee.id != employee.id,
                        (Employee.external_id == external_id) | (Employee.document_id == linked.marker_code),
                    )
                )
                if target is not None:
                    target.name = linked.employee_name
                    target.document_id = linked.marker_code
                    target.external_id = external_id
                    face_embedding.employee_id = target.id
                    if employee.name.startswith("Pendiente de vincular"):
                        employee.active = False
                    updated += 1
                    continue
                changed = False
                if employee.name != linked.employee_name:
                    employee.name = linked.employee_name
                    changed = True
                if employee.document_id != linked.marker_code:
                    employee.document_id = linked.marker_code
                    changed = True
                if employee.external_id != external_id:
                    employee.external_id = external_id
                    changed = True
                if changed:
                    updated += 1
        return updated

    def sync_unlinked_biometric_faces(self, rrhh: RrhhService, limit: int = 500) -> int:
        if not rrhh.enabled:
            return 0
        unlinked_faces = rrhh.list_unlinked_biometric_faces(limit=limit)
        if not unlinked_faces:
            return 0
        changed = 0
        with self.db.primary_session() as session:
            for unlinked in unlinked_faces:
                face_embedding = session.get(FaceEmbedding, unlinked.local_face_embedding_id)
                if face_embedding is None:
                    continue
                employee = session.get(Employee, face_embedding.employee_id)
                prefix = "SCT" if rrhh.mode == "sct" else "GENERIC"
                if employee is None or not (employee.external_id or "").startswith(f"{prefix}:"):
                    continue
                pending = Employee(name=f"Pendiente de vincular {datetime.now().strftime('%Y%m%d%H%M%S')}")
                session.add(pending)
                session.flush()
                face_embedding.employee_id = pending.id
                changed += 1
        return changed
