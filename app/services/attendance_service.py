from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import AppConfig
from app.db.models import AttendanceLog, AttendanceType, OfflineEvent
from app.db.session import DatabaseManager


class AttendanceService:
    def __init__(self, config: AppConfig, db: DatabaseManager):
        self.config = config
        self.db = db

    def next_type(self, employee_id: int, at: datetime | None = None) -> AttendanceType:
        current = at or datetime.now()
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.db.primary_session() as session:
            last = session.scalar(
                select(AttendanceLog)
                .where(AttendanceLog.employee_id == employee_id, AttendanceLog.timestamp >= day_start)
                .order_by(desc(AttendanceLog.timestamp))
                .limit(1)
            )
        if last is None or last.type == AttendanceType.OUT:
            return AttendanceType.IN
        return AttendanceType.OUT

    def is_in_cooldown(self, employee_id: int, at: datetime | None = None) -> bool:
        current = at or datetime.now()
        boundary = current - timedelta(seconds=self.config.cooldown_seconds)
        with self.db.primary_session() as session:
            recent = session.scalar(
                select(AttendanceLog)
                .where(AttendanceLog.employee_id == employee_id, AttendanceLog.timestamp >= boundary)
                .order_by(desc(AttendanceLog.timestamp))
                .limit(1)
            )
        return recent is not None

    def time_until_next_mark(self, employee_id: int, at: datetime | None = None) -> tuple[timedelta | None, datetime | None]:
        min_interval = max(0, int(self.config.min_mark_interval_seconds))
        if min_interval <= 0:
            return None, None
        current = at or datetime.now()
        with self.db.primary_session() as session:
            last = session.scalar(
                select(AttendanceLog)
                .where(AttendanceLog.employee_id == employee_id)
                .order_by(desc(AttendanceLog.timestamp))
                .limit(1)
            )
        if last is None:
            return None, None
        elapsed = current - last.timestamp
        required = timedelta(seconds=min_interval)
        if elapsed >= required:
            return None, last.timestamp
        return required - elapsed, last.timestamp

    def mark(
        self,
        employee_id: int,
        attendance_type: AttendanceType,
        confidence: float,
        snapshot_path: str | None = None,
        at: datetime | None = None,
    ) -> AttendanceLog:
        timestamp = at or datetime.now()
        log = AttendanceLog(
            employee_id=employee_id,
            timestamp=timestamp,
            type=attendance_type,
            device_id=self.config.device_id,
            confidence=confidence,
            image_snapshot_path=snapshot_path,
            synced=not self.config.offline_mode and self.db.primary_available,
        )
        try:
            with self.db.primary_session() as session:
                session.add(log)
                session.flush()
                return log
        except SQLAlchemyError:
            self._queue_offline(log)
            return log

    def _queue_offline(self, log: AttendanceLog) -> None:
        payload = {
            "employee_id": log.employee_id,
            "timestamp": log.timestamp.isoformat(),
            "type": log.type.value,
            "device_id": log.device_id,
            "confidence": log.confidence,
            "image_snapshot_path": log.image_snapshot_path,
        }
        with self.db.local_session() as session:
            session.add(OfflineEvent(event_type="attendance_log", payload_json=json.dumps(payload)))
