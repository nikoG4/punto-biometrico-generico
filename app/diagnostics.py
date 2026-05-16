from __future__ import annotations

import cv2
from sqlalchemy import create_engine, text

from app.config import AppConfig, load_config
from app.db.models import AttendanceLog, Employee, FaceEmbedding, OfflineEvent
from app.db.session import DatabaseManager


def check_ok(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "ERROR"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def normalize_video_source(source: int | str) -> int | str:
    if isinstance(source, str):
        text_value = source.strip()
        return int(text_value) if text_value.isdigit() else text_value
    return source


def run_diagnostics(config: AppConfig | None = None) -> int:
    config = config or load_config()
    exit_code = 0

    print(f"device_id: {config.device_id}")
    print(f"provider configurado: {config.recognition.provider}")
    print(f"offline_mode: {config.offline_mode}")

    for source in config.active_camera_sources:
        video_source = normalize_video_source(source.source)
        cap = cv2.VideoCapture(video_source)
        camera_ok = cap.isOpened()
        check_ok("camara", camera_ok, f"{source.name} ({video_source})")
        if camera_ok:
            ok, _frame = cap.read()
            check_ok("lectura camara", ok, source.name)
            exit_code = exit_code or (0 if ok else 1)
        else:
            exit_code = 1
        cap.release()

    try:
        db = DatabaseManager(config)
        with db.primary_session() as session:
            employees = session.query(Employee).count()
            faces = session.query(FaceEmbedding).count()
            logs = session.query(AttendanceLog).count()
            pending = session.query(OfflineEvent).filter(OfflineEvent.synced_at.is_(None)).count()
        check_ok("base biometrica", True, f"empleados={employees}, rostros={faces}, marcaciones={logs}, cola={pending}")
        db.dispose()
    except Exception as exc:
        check_ok("base biometrica", False, f"{type(exc).__name__}: {exc}")
        exit_code = 1

    if config.rrhh_mysql_url:
        try:
            engine = create_engine(config.rrhh_mysql_url, pool_pre_ping=True, future=True)
            with engine.connect() as conn:
                employees = conn.execute(text("SELECT COUNT(*) FROM empleados")).scalar()
                try:
                    pending_faces = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM biometric_faces "
                            "WHERE status='PENDING' OR employee_marker_code IS NULL"
                        )
                    ).scalar()
                except Exception:
                    pending_faces = "tabla no creada"
                marks = conn.execute(text("SELECT COUNT(*) FROM marcacion1")).scalar()
            check_ok("SCT/RRHH", True, f"empleados={employees}, rostros_pendientes={pending_faces}, marcacion1={marks}")
            engine.dispose()
        except Exception as exc:
            check_ok("SCT/RRHH", False, f"{type(exc).__name__}: {exc}")
            exit_code = 1
    else:
        check_ok("SCT/RRHH", False, "rrhh_mysql_url vacia")

    return exit_code
