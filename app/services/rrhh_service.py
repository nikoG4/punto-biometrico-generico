from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import AppConfig
from app.db.models import AttendanceType


RRHH_ATTENDANCE_TABLE = "marcacion1"


@dataclass(slots=True)
class RrhhEmployee:
    id: int
    marker_code: str
    name: str
    status: str

    @property
    def label(self) -> str:
        code_label = f"Codigo marcador {self.marker_code}" if self.marker_code else "Sin codigo marcador"
        return f"{self.name} - {code_label} - ID externo {self.id}"


@dataclass(slots=True)
class LinkedBiometricFace:
    local_face_embedding_id: int
    marker_code: str
    rrhh_employee_id: int
    employee_name: str


@dataclass(slots=True)
class UnlinkedBiometricFace:
    local_face_embedding_id: int


class RrhhService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._engine = None
        self.last_error = ""
        self.mode = (config.integration_mode or "sct").strip().lower()
        if self.mode == "generic_db" and config.generic_db.url:
            self._engine = create_engine(config.generic_db.url, pool_pre_ping=True, future=True)
        elif self.mode == "generic_rest":
            self._engine = None
        elif config.rrhh_mysql_url:
            self._engine = create_engine(config.rrhh_mysql_url, pool_pre_ping=True, future=True)

    @property
    def enabled(self) -> bool:
        if self.mode == "generic_rest":
            return bool(self.config.generic_rest.base_url)
        return self._engine is not None

    def list_employees(self, query: str = "", limit: int = 100) -> list[RrhhEmployee]:
        self.last_error = ""
        if self.mode == "generic_rest":
            return self._list_employees_rest(query=query, limit=limit)
        if self._engine is None:
            self.last_error = "URL integracion vacia"
            return []
        if self.mode == "generic_db":
            return self._list_employees_generic_db(query=query, limit=limit)
        return self._list_employees(query=query, limit=limit, only_active=self.config.rrhh_only_active)

    def upload_biometric_face(
        self,
        *,
        local_employee_id: int,
        local_face_embedding_id: int,
        person_name: str,
        marker_code: str | None,
        embedding: bytes,
        dimension: int,
        provider: str,
        image_snapshot_path: str,
    ) -> bool:
        self.last_error = ""
        if self.mode == "generic_rest":
            return self._upload_biometric_face_rest(
                local_employee_id=local_employee_id,
                local_face_embedding_id=local_face_embedding_id,
                person_name=person_name,
                marker_code=marker_code,
                embedding=embedding,
                dimension=dimension,
                provider=provider,
                image_snapshot_path=image_snapshot_path,
            )
        if self._engine is None:
            self.last_error = "URL integracion vacia"
            return False
        try:
            image_bytes = Path(image_snapshot_path).read_bytes()
            with self._engine.begin() as conn:
                self._ensure_biometric_faces_table(conn)
                table = "biometric_faces" if self.mode == "sct" else self._identifier(self.config.generic_db.biometric_faces_table)
                insert_sql = (
                    f"INSERT INTO {table} ("
                    "local_employee_id, local_face_embedding_id, device_id, person_name, "
                    "employee_marker_code, embedding, dimension, provider, image_snapshot, "
                    "local_snapshot_path, status"
                    ") VALUES ("
                    ":local_employee_id, :local_face_embedding_id, :device_id, :person_name, "
                    ":employee_marker_code, :embedding, :dimension, :provider, :image_snapshot, "
                    ":local_snapshot_path, :status"
                    ")"
                )
                if conn.engine.dialect.name in {"mysql", "mariadb"}:
                    insert_sql += (
                        " ON DUPLICATE KEY UPDATE "
                        "person_name=VALUES(person_name), "
                        "employee_marker_code=COALESCE(VALUES(employee_marker_code), employee_marker_code), "
                        "embedding=VALUES(embedding), "
                        "dimension=VALUES(dimension), "
                        "provider=VALUES(provider), "
                        "image_snapshot=VALUES(image_snapshot), "
                        "local_snapshot_path=VALUES(local_snapshot_path), "
                        "status=IF(employee_marker_code IS NULL AND VALUES(employee_marker_code) IS NULL, 'PENDING', 'LINKED')"
                    )
                elif conn.engine.dialect.name == "sqlite":
                    insert_sql = "INSERT OR REPLACE " + insert_sql.removeprefix("INSERT ")
                conn.execute(
                    text(insert_sql),
                    {
                        "local_employee_id": local_employee_id,
                        "local_face_embedding_id": local_face_embedding_id,
                        "device_id": self.config.device_id,
                        "person_name": person_name,
                        "employee_marker_code": marker_code,
                        "embedding": embedding,
                        "dimension": dimension,
                        "provider": provider,
                        "image_snapshot": image_bytes,
                        "local_snapshot_path": image_snapshot_path,
                        "status": "LINKED" if marker_code else "PENDING",
                    },
                )
            return True
        except (OSError, SQLAlchemyError) as exc:
            self.last_error = str(exc)
            return False

    def list_linked_biometric_faces(self, limit: int = 500) -> list[LinkedBiometricFace]:
        self.last_error = ""
        if self.mode == "generic_rest":
            return self._list_linked_biometric_faces_rest(limit=limit)
        if self._engine is None:
            self.last_error = "URL integracion vacia"
            return []
        if self.mode == "generic_db":
            return self._list_linked_biometric_faces_generic_db(limit=limit)
        try:
            with self._engine.begin() as conn:
                self._ensure_biometric_faces_table(conn)
                rows = conn.execute(
                    text(
                        "SELECT bf.local_face_embedding_id, bf.employee_marker_code, "
                        "e.id AS rrhh_employee_id, e.nombre AS employee_name "
                        "FROM biometric_faces bf "
                        "JOIN empleados e ON e.ci = bf.employee_marker_code "
                        "WHERE bf.device_id = :device_id "
                        "AND bf.status = 'LINKED' "
                        "AND bf.employee_marker_code IS NOT NULL "
                        "AND bf.local_face_embedding_id IS NOT NULL "
                        "ORDER BY bf.linked_at DESC, bf.updated_at DESC, bf.created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"device_id": self.config.device_id, "limit": limit},
                ).mappings().all()
        except SQLAlchemyError as exc:
            self.last_error = str(exc)
            return []
        return [
            LinkedBiometricFace(
                local_face_embedding_id=int(row["local_face_embedding_id"]),
                marker_code=str(row["employee_marker_code"]),
                rrhh_employee_id=int(row["rrhh_employee_id"]),
                employee_name=str(row["employee_name"]),
            )
            for row in rows
        ]

    def list_unlinked_biometric_faces(self, limit: int = 500) -> list[UnlinkedBiometricFace]:
        self.last_error = ""
        if self.mode == "generic_rest":
            return self._list_unlinked_biometric_faces_rest(limit=limit)
        if self._engine is None:
            self.last_error = "URL integracion vacia"
            return []
        try:
            table = "biometric_faces" if self.mode == "sct" else self._identifier(self.config.generic_db.biometric_faces_table)
            with self._engine.begin() as conn:
                self._ensure_biometric_faces_table(conn)
                rows = conn.execute(
                    text(
                        "SELECT local_face_embedding_id "
                        f"FROM {table} "
                        "WHERE device_id = :device_id "
                        "AND status = 'PENDING' "
                        "AND employee_marker_code IS NULL "
                        "AND local_face_embedding_id IS NOT NULL "
                        "ORDER BY updated_at DESC, created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"device_id": self.config.device_id, "limit": limit},
                ).mappings().all()
        except (ValueError, SQLAlchemyError) as exc:
            self.last_error = str(exc)
            return []
        return [UnlinkedBiometricFace(local_face_embedding_id=int(row["local_face_embedding_id"])) for row in rows]

    def record_attendance(
        self,
        marker_code: str | int | None,
        attendance_type: AttendanceType,
        at: datetime | None = None,
        confidence: float | None = None,
    ) -> tuple[bool, str]:
        self.last_error = ""
        if not marker_code:
            return False, "Empleado sin codigo marcador"
        if self.mode == "generic_rest":
            return self._record_attendance_rest(marker_code, attendance_type, at, confidence)
        if self._engine is None:
            self.last_error = "URL integracion vacia"
            return False, self.last_error
        if self.mode == "generic_db":
            return self._record_attendance_generic_db(marker_code, attendance_type, at, confidence)
        timestamp = at or datetime.now()
        boundary = timestamp - timedelta(hours=30)
        try:
            with self._engine.begin() as conn:
                open_row = conn.execute(
                    text(
                        f"SELECT id FROM {RRHH_ATTENDANCE_TABLE} "
                        "WHERE id_empleado = :marker_code "
                        "AND salida IS NULL "
                        "AND entrada >= :boundary "
                        "ORDER BY entrada DESC LIMIT 1"
                    ),
                    {"marker_code": int(marker_code), "boundary": boundary},
                ).mappings().first()
                if attendance_type == AttendanceType.OUT and open_row:
                    conn.execute(
                        text(f"UPDATE {RRHH_ATTENDANCE_TABLE} SET salida = :timestamp WHERE id = :id"),
                        {"timestamp": timestamp, "id": open_row["id"]},
                    )
                    return True, "Salida SCT marcacion1 actualizada"
                if attendance_type == AttendanceType.OUT and not open_row:
                    return False, "No hay entrada abierta en SCT marcacion1 para registrar salida"
                if attendance_type == AttendanceType.IN and open_row:
                    return True, "Entrada SCT marcacion1 ya estaba abierta"

                almuerzo = conn.execute(
                    text("SELECT COALESCE(almuerzo1, '01:00:00') AS almuerzo FROM empleados WHERE ci = :marker_code"),
                    {"marker_code": int(marker_code)},
                ).scalar_one_or_none() or "01:00:00"
                conn.execute(
                    text(
                        f"INSERT INTO {RRHH_ATTENDANCE_TABLE} (id_empleado, entrada, salida, almuerzo, id_usuario) "
                        "VALUES (:marker_code, :timestamp, NULL, :almuerzo, :id_usuario)"
                    ),
                    {
                        "marker_code": int(marker_code),
                        "timestamp": timestamp,
                        "almuerzo": almuerzo,
                        "id_usuario": self.config.rrhh_attendance_user_id,
                    },
                )
                return True, "Entrada SCT marcacion1 registrada"
        except (ValueError, SQLAlchemyError) as exc:
            self.last_error = str(exc)
            return False, self.last_error

    def _list_employees(self, query: str, limit: int, only_active: bool) -> list[RrhhEmployee]:
        where = []
        params: dict[str, object] = {"limit": limit}
        if only_active:
            where.append("LOWER(TRIM(COALESCE(estado, ''))) = 'vigente'")
        if query.strip():
            where.append("(nombre LIKE :query OR ci LIKE :query OR CAST(id AS CHAR) LIKE :query)")
            params["query"] = f"%{query.strip()}%"
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = text(
            "SELECT id, COALESCE(ci, '') AS marker_code, nombre, COALESCE(estado, '') AS estado "
            f"FROM empleados {where_sql} ORDER BY nombre LIMIT :limit"
        )
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql, params).mappings().all()
                if not rows and only_active:
                    rows = conn.execute(
                        text(
                            "SELECT id, COALESCE(ci, '') AS marker_code, nombre, COALESCE(estado, '') AS estado "
                            "FROM empleados ORDER BY nombre LIMIT :limit"
                        ),
                        {"limit": limit},
                    ).mappings().all()
                    if rows:
                        self.last_error = "No habia empleados con estado Vigente; se listaron todos"
        except SQLAlchemyError as exc:
            self.last_error = str(exc)
            return []
        return [
            RrhhEmployee(
                id=int(row["id"]),
                marker_code=str(row["marker_code"]),
                name=str(row["nombre"]),
                status=str(row["estado"]),
            )
            for row in rows
        ]

    def _list_employees_generic_db(self, query: str, limit: int) -> list[RrhhEmployee]:
        params = {
            "query": query.strip(),
            "query_like": f"%{query.strip()}%",
            "limit": limit,
        }
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text(self.config.generic_db.employee_query), params).mappings().all()
        except SQLAlchemyError as exc:
            self.last_error = str(exc)
            return []
        return [
            RrhhEmployee(
                id=int(row["id"]),
                marker_code=str(row["marker_code"]),
                name=str(row["name"]),
                status=str(row.get("status", "")),
            )
            for row in rows
        ]

    def _record_attendance_generic_db(
        self,
        marker_code: str | int,
        attendance_type: AttendanceType,
        at: datetime | None = None,
        confidence: float | None = None,
    ) -> tuple[bool, str]:
        timestamp = at or datetime.now()
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(self.config.generic_db.attendance_insert_sql),
                    {
                        "marker_code": str(marker_code),
                        "timestamp": timestamp,
                        "type": attendance_type.value,
                        "device_id": self.config.device_id,
                        "confidence": confidence,
                    },
                )
            return True, "Marcacion generica DB registrada"
        except SQLAlchemyError as exc:
            self.last_error = str(exc)
            return False, self.last_error

    def _list_linked_biometric_faces_generic_db(self, limit: int = 500) -> list[LinkedBiometricFace]:
        table = self._identifier(self.config.generic_db.biometric_faces_table)
        try:
            with self._engine.begin() as conn:
                self._ensure_biometric_faces_table(conn)
                rows = conn.execute(
                    text(
                        f"SELECT local_face_embedding_id, employee_marker_code, rrhh_employee_id, person_name "
                        f"FROM {table} "
                        "WHERE device_id = :device_id "
                        "AND status = 'LINKED' "
                        "AND employee_marker_code IS NOT NULL "
                        "AND local_face_embedding_id IS NOT NULL "
                        "ORDER BY linked_at DESC, updated_at DESC, created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"device_id": self.config.device_id, "limit": limit},
                ).mappings().all()
        except (ValueError, SQLAlchemyError) as exc:
            self.last_error = str(exc)
            return []
        return [
            LinkedBiometricFace(
                local_face_embedding_id=int(row["local_face_embedding_id"]),
                marker_code=str(row["employee_marker_code"]),
                rrhh_employee_id=int(row["rrhh_employee_id"] or 0),
                employee_name=str(row["person_name"] or row["employee_marker_code"]),
            )
            for row in rows
        ]

    def _request_json(self, method: str, path: str, payload: dict | None = None, query: dict | None = None):
        base_url = self.config.generic_rest.base_url.rstrip("/") + "/"
        url = urljoin(base_url, path.lstrip("/"))
        if query:
            url += "?" + urlencode(query)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.config.generic_rest.token:
            headers["Authorization"] = f"Bearer {self.config.generic_rest.token}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.generic_rest.timeout_seconds) as response:
                data = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            self.last_error = str(exc)
            return None
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self.last_error = str(exc)
            return None

    def _list_employees_rest(self, query: str, limit: int) -> list[RrhhEmployee]:
        data = self._request_json(
            "GET",
            self.config.generic_rest.employees_path,
            query={"q": query.strip(), "limit": limit},
        )
        if data is None:
            return []
        rows = data.get("employees", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            self.last_error = "Respuesta REST invalida para empleados"
            return []
        employees: list[RrhhEmployee] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            employees.append(
                RrhhEmployee(
                    id=int(row.get("id", 0)),
                    marker_code=str(row.get("marker_code") or row.get("code") or ""),
                    name=str(row.get("name") or row.get("nombre") or ""),
                    status=str(row.get("status") or ""),
                )
            )
        return employees

    def _upload_biometric_face_rest(
        self,
        *,
        local_employee_id: int,
        local_face_embedding_id: int,
        person_name: str,
        marker_code: str | None,
        embedding: bytes,
        dimension: int,
        provider: str,
        image_snapshot_path: str,
    ) -> bool:
        try:
            image_bytes = Path(image_snapshot_path).read_bytes()
        except OSError as exc:
            self.last_error = str(exc)
            return False
        payload = {
            "local_employee_id": local_employee_id,
            "local_face_embedding_id": local_face_embedding_id,
            "device_id": self.config.device_id,
            "person_name": person_name,
            "employee_marker_code": marker_code,
            "embedding_base64": base64.b64encode(embedding).decode("ascii"),
            "dimension": dimension,
            "provider": provider,
            "image_snapshot_base64": base64.b64encode(image_bytes).decode("ascii"),
            "status": "LINKED" if marker_code else "PENDING",
        }
        return self._request_json("POST", self.config.generic_rest.biometric_faces_path, payload=payload) is not None

    def _record_attendance_rest(
        self,
        marker_code: str | int,
        attendance_type: AttendanceType,
        at: datetime | None = None,
        confidence: float | None = None,
    ) -> tuple[bool, str]:
        timestamp = at or datetime.now()
        payload = {
            "employee_marker_code": str(marker_code),
            "timestamp": timestamp.isoformat(),
            "type": attendance_type.value,
            "device_id": self.config.device_id,
            "confidence": confidence,
        }
        ok = self._request_json("POST", self.config.generic_rest.attendance_path, payload=payload) is not None
        return (True, "Marcacion REST registrada") if ok else (False, self.last_error)

    def _list_linked_biometric_faces_rest(self, limit: int) -> list[LinkedBiometricFace]:
        data = self._request_json(
            "GET",
            self.config.generic_rest.biometric_faces_path,
            query={"device_id": self.config.device_id, "status": "LINKED", "limit": limit},
        )
        if data is None:
            return []
        rows = data.get("faces", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        linked: list[LinkedBiometricFace] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("local_face_embedding_id"):
                continue
            linked.append(
                LinkedBiometricFace(
                    local_face_embedding_id=int(row["local_face_embedding_id"]),
                    marker_code=str(row.get("employee_marker_code") or row.get("marker_code") or ""),
                    rrhh_employee_id=int(row.get("employee_id") or row.get("rrhh_employee_id") or 0),
                    employee_name=str(row.get("employee_name") or row.get("person_name") or ""),
                )
            )
        return linked

    def _list_unlinked_biometric_faces_rest(self, limit: int) -> list[UnlinkedBiometricFace]:
        data = self._request_json(
            "GET",
            self.config.generic_rest.biometric_faces_path,
            query={"device_id": self.config.device_id, "status": "PENDING", "limit": limit},
        )
        if data is None:
            return []
        rows = data.get("faces", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [
            UnlinkedBiometricFace(local_face_embedding_id=int(row["local_face_embedding_id"]))
            for row in rows
            if isinstance(row, dict) and row.get("local_face_embedding_id")
        ]

    def _identifier(self, value: str) -> str:
        if not value or not all(char.isalnum() or char == "_" for char in value):
            raise ValueError(f"Identificador SQL invalido: {value}")
        return value

    def _ensure_biometric_faces_table(self, conn) -> None:
        table = "biometric_faces" if self.mode == "sct" else self._identifier(self.config.generic_db.biometric_faces_table)
        if conn.engine.dialect.name == "sqlite":
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {table} ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "local_employee_id INTEGER NULL, "
                    "local_face_embedding_id INTEGER NULL, "
                    "device_id TEXT NOT NULL, "
                    "person_name TEXT NULL, "
                    "rrhh_employee_id INTEGER NULL, "
                    "employee_marker_code INTEGER NULL, "
                    "embedding BLOB NOT NULL, "
                    "dimension INTEGER NOT NULL DEFAULT 512, "
                    "provider TEXT NOT NULL DEFAULT 'insightface', "
                    "image_snapshot BLOB NOT NULL, "
                    "local_snapshot_path TEXT NULL, "
                    "status TEXT NOT NULL DEFAULT 'PENDING', "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NULL, "
                    "linked_at DATETIME NULL, "
                    "linked_by INTEGER NULL"
                    ", UNIQUE (device_id, local_face_embedding_id)"
                    ")"
                )
            )
            return
        marker_type = "INT" if self.mode == "sct" else "VARCHAR(80)"
        constraints = (
            ", CONSTRAINT fk_biometric_faces_empleado "
            "FOREIGN KEY (employee_marker_code) REFERENCES empleados(ci) ON UPDATE CASCADE, "
            "CONSTRAINT fk_biometric_faces_usuario "
            "FOREIGN KEY (linked_by) REFERENCES usuarios(id)"
            if self.mode == "sct"
            else ""
        )
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, "
                "local_employee_id INT NULL, "
                "local_face_embedding_id INT NULL, "
                "device_id VARCHAR(80) NOT NULL, "
                "person_name VARCHAR(160) NULL, "
                "rrhh_employee_id INT NULL, "
                f"employee_marker_code {marker_type} NULL, "
                "embedding LONGBLOB NOT NULL, "
                "dimension INT NOT NULL DEFAULT 512, "
                "provider VARCHAR(40) NOT NULL DEFAULT 'insightface', "
                "image_snapshot LONGBLOB NOT NULL, "
                "local_snapshot_path TEXT NULL, "
                "status ENUM('PENDING','LINKED','IGNORED') NOT NULL DEFAULT 'PENDING', "
                "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP, "
                "linked_at DATETIME NULL, "
                "linked_by INT NULL, "
                "UNIQUE KEY uk_biometric_faces_device_embedding (device_id, local_face_embedding_id), "
                "INDEX ix_biometric_faces_status (status), "
                "INDEX ix_biometric_faces_marker_code (employee_marker_code)"
                f"{constraints}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
        )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
