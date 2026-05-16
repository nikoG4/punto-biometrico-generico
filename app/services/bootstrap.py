from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.core.face_engine import FaceEngine
from app.core.liveness import LivenessChecker
from app.db.session import DatabaseManager
from app.services.attendance_service import AttendanceService
from app.services.employee_service import EmployeeService
from app.services.maintenance import MaintenanceService
from app.services.recognition_service import RecognitionService
from app.services.rrhh_service import RrhhService
from app.services.sync_service import SyncService


@dataclass(slots=True)
class RuntimeServices:
    face_engine: FaceEngine
    liveness: LivenessChecker
    employees: EmployeeService
    attendance: AttendanceService
    recognition: RecognitionService
    rrhh: RrhhService
    sync: SyncService
    maintenance: MaintenanceService


def bootstrap_runtime(config: AppConfig, db: DatabaseManager) -> RuntimeServices:
    face_engine = FaceEngine(config)
    return RuntimeServices(
        face_engine=face_engine,
        liveness=LivenessChecker(config),
        employees=EmployeeService(db),
        attendance=AttendanceService(config, db),
        recognition=RecognitionService(config, db, face_engine),
        rrhh=RrhhService(config),
        sync=SyncService(db),
        maintenance=MaintenanceService(config),
    )
