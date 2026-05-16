from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.config import AppConfig, GenericDbConfig, RecognitionConfig, load_config
from app.core.face_engine import FaceEngine
from app.core.vector_index import VectorIndex
from app.db.session import DatabaseManager
from app.services.attendance_service import AttendanceService
from app.services.employee_service import EmployeeService
from app.services.recognition_service import RecognitionService
from app.services.rrhh_service import RrhhService
from app.services.sync_service import SyncService
from app.db.models import AttendanceType


class CoreServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        sqlite_url = f"sqlite:///{db_path.as_posix()}"
        self.config = AppConfig(
            offline_mode=True,
            sqlite_url=sqlite_url,
            mysql_url=sqlite_url,
            recognition=RecognitionConfig(provider="demo"),
            threshold=0.65,
            cooldown_seconds=60,
        )
        self.db = DatabaseManager(self.config)

    def tearDown(self) -> None:
        self.db.dispose()
        self.temp_dir.cleanup()

    def test_vector_index_finds_cosine_match(self) -> None:
        index = VectorIndex(dimension=4)
        alice = np.array([1, 0, 0, 0], dtype=np.float32)
        bob = np.array([0, 1, 0, 0], dtype=np.float32)
        index.rebuild([alice, bob], [1, 2], ["Alice", "Bob"])

        result = index.search(np.array([0.9, 0.1, 0, 0], dtype=np.float32))[0]

        self.assertEqual(result[0], 1)
        self.assertEqual(result[1], "Alice")
        self.assertGreater(result[2], 0.9)

    def test_config_loads_multi_camera_sources(self) -> None:
        config_path = Path(self.temp_dir.name) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "camera_index": 0,
                    "camera_sources": [
                        {"id": "FRONT", "name": "Frontal", "source": 0, "enabled": True, "primary": True},
                        {
                            "id": "IP_SIDE",
                            "name": "IP lateral",
                            "source": "rtsp://user:pass@192.168.1.50:554/stream1",
                            "enabled": True,
                            "fps_limit": 15,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        config = load_config(config_path)

        self.assertEqual(len(config.active_camera_sources), 2)
        self.assertEqual(config.primary_camera_source.id, "FRONT")
        self.assertEqual(config.active_camera_sources[1].source, "rtsp://user:pass@192.168.1.50:554/stream1")

    def test_demo_embedding_is_stable_for_small_crop_changes(self) -> None:
        engine = FaceEngine(self.config)
        crop = np.zeros((120, 120), dtype=np.uint8)
        crop[28:92, 35:85] = 160
        crop[48:58, 48:58] = 30
        crop[48:58, 68:78] = 30
        crop[70:78, 55:75] = 60
        shifted = np.roll(crop, shift=2, axis=1)

        first = engine._demo_embedding_from_crop(crop)
        second = engine._demo_embedding_from_crop(shifted)

        self.assertGreater(float(first @ second), 0.85)

    def test_employee_embedding_and_attendance_flow(self) -> None:
        employees = EmployeeService(self.db)
        face_engine = FaceEngine(self.config)
        recognition = RecognitionService(self.config, self.db, face_engine)
        attendance = AttendanceService(self.config, self.db)

        employee = employees.create_employee("Ana Perez", "123", "RRHH-123")
        embedding = np.zeros(face_engine.dimension, dtype=np.float32)
        embedding[0] = 1.0
        recognition.register_embedding(employee.id, embedding)

        match = recognition.find_match(embedding)

        self.assertIsNotNone(match)
        self.assertEqual(match.employee_id, employee.id)
        self.assertFalse(attendance.is_in_cooldown(employee.id))
        first_type = attendance.next_type(employee.id)
        log = attendance.mark(employee.id, first_type, confidence=0.99)
        self.assertEqual(log.employee_id, employee.id)
        self.assertTrue(attendance.is_in_cooldown(employee.id))

    def test_rrhh_service_lists_active_employees(self) -> None:
        rrhh_path = Path(self.temp_dir.name) / "rrhh.db"
        config = AppConfig(
            rrhh_mysql_url=f"sqlite:///{rrhh_path.as_posix()}",
            rrhh_only_active=True,
        )
        from sqlalchemy import create_engine, text

        engine = create_engine(config.rrhh_mysql_url, future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE empleados (id INTEGER PRIMARY KEY, ci TEXT, nombre TEXT, estado TEXT)"))
            conn.execute(
                text("INSERT INTO empleados (id, ci, nombre, estado) VALUES (1, '100', 'Ana Perez', 'Vigente')")
            )
            conn.execute(
                text("INSERT INTO empleados (id, ci, nombre, estado) VALUES (2, '200', 'Luis Gomez', 'Renuncio')")
            )

        service = RrhhService(config)
        employees = service.list_employees()

        self.assertEqual(len(employees), 1)
        self.assertEqual(employees[0].name, "Ana Perez")
        service.dispose()
        engine.dispose()

    def test_rrhh_service_uploads_pending_face_snapshot(self) -> None:
        rrhh_path = Path(self.temp_dir.name) / "rrhh_faces.db"
        image_path = Path(self.temp_dir.name) / "face.jpg"
        image_path.write_bytes(b"fake-jpeg")
        config = AppConfig(
            rrhh_mysql_url=f"sqlite:///{rrhh_path.as_posix()}",
            device_id="TEST_DEVICE",
        )
        from sqlalchemy import create_engine, text

        service = RrhhService(config)
        uploaded = service.upload_biometric_face(
            local_employee_id=7,
            local_face_embedding_id=11,
            person_name="Pendiente",
            marker_code=None,
            embedding=b"embedding",
            dimension=512,
            provider="demo",
            image_snapshot_path=str(image_path),
        )

        self.assertTrue(uploaded, service.last_error)
        engine = create_engine(config.rrhh_mysql_url, future=True)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT device_id, status, image_snapshot FROM biometric_faces")).mappings().one()
        self.assertEqual(row["device_id"], "TEST_DEVICE")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["image_snapshot"], b"fake-jpeg")
        service.dispose()
        engine.dispose()

    def test_sync_updates_pending_face_after_rrhh_link(self) -> None:
        rrhh_path = Path(self.temp_dir.name) / "rrhh_link.db"
        image_path = Path(self.temp_dir.name) / "face-link.jpg"
        image_path.write_bytes(b"fake-jpeg")
        config = AppConfig(
            offline_mode=True,
            sqlite_url=self.config.sqlite_url,
            mysql_url=self.config.mysql_url,
            rrhh_mysql_url=f"sqlite:///{rrhh_path.as_posix()}",
            device_id="TEST_DEVICE",
            recognition=RecognitionConfig(provider="demo"),
        )
        from sqlalchemy import create_engine, text

        rrhh_engine = create_engine(config.rrhh_mysql_url, future=True)
        with rrhh_engine.begin() as conn:
            conn.execute(text("CREATE TABLE empleados (id INTEGER PRIMARY KEY, ci INTEGER, nombre TEXT, estado TEXT)"))
            conn.execute(text("INSERT INTO empleados (id, ci, nombre, estado) VALUES (26, 43, 'Ana SCT', 'Vigente')"))

        employees = EmployeeService(self.db)
        face_engine = FaceEngine(config)
        recognition = RecognitionService(config, self.db, face_engine)
        employee = employees.create_pending_employee()
        embedding = np.zeros(face_engine.dimension, dtype=np.float32)
        embedding[0] = 1.0
        face_row = recognition.register_embedding(employee.id, embedding, str(image_path))

        rrhh = RrhhService(config)
        self.assertTrue(
            rrhh.upload_biometric_face(
                local_employee_id=employee.id,
                local_face_embedding_id=face_row.id,
                person_name=employee.name,
                marker_code=None,
                embedding=face_row.embedding,
                dimension=face_row.dimension,
                provider=face_row.provider,
                image_snapshot_path=str(image_path),
            ),
            rrhh.last_error,
        )
        with rrhh_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE biometric_faces "
                    "SET employee_marker_code=43, status='LINKED', linked_at=CURRENT_TIMESTAMP "
                    "WHERE local_face_embedding_id=:id"
                ),
                {"id": face_row.id},
            )

        updated = SyncService(self.db).sync_linked_biometric_faces(rrhh)
        recognition.refresh_index()
        match = recognition.find_match(embedding)

        self.assertEqual(updated, 1)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Ana SCT")
        linked_employee = employees.get(employee.id)
        self.assertEqual(linked_employee.document_id, "43")
        self.assertEqual(linked_employee.external_id, "SCT:26")
        rrhh.dispose()
        rrhh_engine.dispose()

    def test_rrhh_record_attendance_pairs_in_and_out(self) -> None:
        rrhh_path = Path(self.temp_dir.name) / "rrhh_attendance.db"
        config = AppConfig(rrhh_mysql_url=f"sqlite:///{rrhh_path.as_posix()}")
        from datetime import datetime
        from sqlalchemy import create_engine, text

        engine = create_engine(config.rrhh_mysql_url, future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE empleados (ci INTEGER PRIMARY KEY, nombre TEXT, almuerzo1 TEXT)"))
            conn.execute(text("CREATE TABLE marcacion1 (id INTEGER PRIMARY KEY AUTOINCREMENT, id_empleado INTEGER, entrada DATETIME, salida DATETIME, almuerzo TEXT, id_usuario INTEGER)"))
            conn.execute(text("INSERT INTO empleados (ci, nombre, almuerzo1) VALUES (43, 'Ana SCT', '00:30:00')"))

        service = RrhhService(config)
        at = datetime(2026, 5, 3, 8, 0, 0)
        ok_in, _ = service.record_attendance("43", AttendanceType.IN, at)
        ok_out, _ = service.record_attendance("43", AttendanceType.OUT, at.replace(hour=17))

        self.assertTrue(ok_in, service.last_error)
        self.assertTrue(ok_out, service.last_error)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id_empleado, entrada, salida, almuerzo FROM marcacion1")).mappings().one()
        self.assertEqual(row["id_empleado"], 43)
        self.assertIsNotNone(row["entrada"])
        self.assertIsNotNone(row["salida"])
        self.assertEqual(row["almuerzo"], "00:30:00")
        service.dispose()
        engine.dispose()

    def test_generic_db_integration_lists_employees_and_records_attendance(self) -> None:
        generic_path = Path(self.temp_dir.name) / "generic.db"
        config = AppConfig(
            integration_mode="generic_db",
            generic_db=GenericDbConfig(
                url=f"sqlite:///{generic_path.as_posix()}",
                employee_query=(
                    "SELECT id, code AS marker_code, name, status "
                    "FROM employees "
                    "WHERE (:query = '' OR name LIKE :query_like OR code LIKE :query_like) "
                    "ORDER BY name LIMIT :limit"
                ),
                attendance_insert_sql=(
                    "INSERT INTO attendance_events "
                    "(employee_code, timestamp, type, device_id, confidence) "
                    "VALUES (:marker_code, :timestamp, :type, :device_id, :confidence)"
                ),
            ),
            device_id="GENERIC_DEVICE",
        )
        from datetime import datetime
        from sqlalchemy import create_engine, text

        engine = create_engine(config.generic_db.url, future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE employees (id INTEGER PRIMARY KEY, code TEXT, name TEXT, status TEXT)"))
            conn.execute(
                text(
                    "CREATE TABLE attendance_events ("
                    "employee_code TEXT, timestamp DATETIME, type TEXT, device_id TEXT, confidence REAL)"
                )
            )
            conn.execute(text("INSERT INTO employees (id, code, name, status) VALUES (1, 'E-100', 'Ana Demo', 'ACTIVE')"))

        service = RrhhService(config)
        employees = service.list_employees("Ana")
        ok, message = service.record_attendance("E-100", AttendanceType.IN, datetime(2026, 5, 16, 8, 30), 0.91)

        self.assertEqual(len(employees), 1)
        self.assertEqual(employees[0].marker_code, "E-100")
        self.assertTrue(ok, message)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT employee_code, type, device_id, confidence FROM attendance_events")).mappings().one()
        self.assertEqual(row["employee_code"], "E-100")
        self.assertEqual(row["type"], "IN")
        self.assertEqual(row["device_id"], "GENERIC_DEVICE")
        self.assertAlmostEqual(float(row["confidence"]), 0.91)
        service.dispose()
        engine.dispose()



if __name__ == "__main__":
    unittest.main()
