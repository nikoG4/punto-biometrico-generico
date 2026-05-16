from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import threading

import cv2
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QSpinBox,
    QFrame,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, CameraSourceConfig, parse_camera_sources
from app.core.liveness import LivenessChecker
from app.db.models import AttendanceType
from app.services.bootstrap import RuntimeServices
from app.services.rrhh_service import RrhhService
from app.services.video_discovery import VideoSourceDiscovery
from app.ui.camera import CameraError, CameraFrame, CameraThread
from app.ui.styles import APP_STYLE
from app.ui.widgets import VideoLabel


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, runtime: RuntimeServices):
        super().__init__()
        self.config = config
        self.runtime = runtime
        self.logger = logging.getLogger(__name__)
        self.latest_frame = None
        self.latest_frames: dict[str, CameraFrame] = {}
        self.frame_count = 0
        self.frame_counts: dict[str, int] = {}
        self.active_source_id = config.primary_camera_source.id
        self.liveness_by_source = {source.id: LivenessChecker(config) for source in config.active_camera_sources}
        self.last_marked_at: dict[int, datetime] = {}
        self.syncing_faces = False

        self.setWindowTitle("Punto Biometrico")
        self.setStyleSheet(APP_STYLE)
        self.stack = QStackedWidget()
        self.kiosk = KioskPage(self)
        self.admin = AdminPage(self)
        self.settings = SettingsPage(self)
        self.stack.addWidget(self.kiosk)
        self.stack.addWidget(self.admin)
        self.stack.addWidget(self.settings)
        self.setCentralWidget(self.stack)

        self.cameras: list[CameraThread] = []
        self._start_cameras()
        self.kiosk.set_camera_label(config.primary_camera_source.name, len(self.cameras))

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.kiosk.reset_prompt)
        self.status_timer.start(7000)

        self.system_status_timer = QTimer(self)
        self.system_status_timer.timeout.connect(self._update_system_status)
        self.system_status_timer.start(30000)

        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self._sync_cached_faces)
        self.sync_timer.start(15000)
        QTimer.singleShot(3000, self._sync_cached_faces)
        QTimer.singleShot(1000, self._update_system_status)
        QTimer.singleShot(5000, self._run_maintenance)

    def _start_cameras(self) -> None:
        for source in self.config.active_camera_sources:
            camera = CameraThread(source)
            camera.frame_ready.connect(self.on_frame)
            camera.camera_error.connect(self.on_camera_error)
            self.cameras.append(camera)
            camera.start()

    def on_frame(self, camera_frame: CameraFrame) -> None:
        source_id = camera_frame.source_id
        frame = camera_frame.frame
        self.latest_frames[source_id] = camera_frame
        self.latest_frame = frame
        self.frame_count += 1
        self.frame_counts[source_id] = self.frame_counts.get(source_id, 0) + 1
        try:
            if self.stack.currentWidget() is self.kiosk:
                if self.active_source_id == source_id:
                    self.kiosk.video.set_frame(frame)
                    self.kiosk.set_camera_label(camera_frame.source_name, len(self.cameras))
                self._process_kiosk_frame(camera_frame)
            elif self.stack.currentWidget() is self.admin:
                self.admin.update_preview(camera_frame)
        except Exception as exc:
            self.logger.exception("Error procesando frame: %s", exc)
            self.kiosk.set_error("Error temporal de camara")

    def on_camera_error(self, error: CameraError) -> None:
        if not self.latest_frames or error.source_id == self.active_source_id:
            self.kiosk.set_error(error.message)
        self.logger.warning("Camara %s: %s", error.source_id, error.message)

    def _process_kiosk_frame(self, camera_frame: CameraFrame) -> None:
        frame = camera_frame.frame
        source_id = camera_frame.source_id
        n = max(1, self.config.recognition.process_every_n_frames)
        if self.frame_counts.get(source_id, 0) % n != 0:
            return
        observations = self.runtime.face_engine.extract(frame)
        observation = self._select_kiosk_face(observations)
        if observation is None and observations:
            if self.active_source_id == source_id:
                self.kiosk.set_error("Solo una persona frente a la camara")
            return
        liveness = self.liveness_by_source.setdefault(source_id, LivenessChecker(self.config))
        live, live_message = liveness.update(observation)
        if observation is None:
            return
        self.active_source_id = source_id
        self.latest_frame = frame
        self.kiosk.set_camera_label(camera_frame.source_name, len(self.cameras))
        self.kiosk.video.set_frame(frame, observation.bbox)
        if not self._face_fits_guide(frame, observation.bbox):
            self.kiosk.set_error("Encaje el rostro en la guia")
            return
        if not live:
            self.kiosk.set_error(live_message)
            return
        match = self.runtime.recognition.find_match(observation.embedding)
        if match is None:
            candidate = self.runtime.recognition.best_candidate(observation.embedding)
            if candidate is None:
                self.kiosk.set_error("No hay rostros registrados")
            else:
                self.kiosk.set_error(f"No reconocido ({candidate.score:.2f})")
            return
        if match.name.startswith("Pendiente de vincular"):
            self._sync_cached_faces()
            self.kiosk.set_ok("Rostro vinculado pendiente", "Actualizando datos desde RRHH")
            return
        if self.runtime.attendance.is_in_cooldown(match.employee_id):
            self.kiosk.set_ok(f"{match.name}", "Marcacion reciente")
            return
        attendance_type = self.runtime.attendance.next_type(match.employee_id)
        snapshot = self._save_snapshot(frame, match.employee_id, source_id)
        log = self.runtime.attendance.mark(match.employee_id, attendance_type, match.score, snapshot)
        self.logger.info(
            "Marcacion local employee_id=%s type=%s confidence=%.3f",
            match.employee_id,
            attendance_type.value,
            match.score,
        )
        rrhh_detail = self._record_rrhh_attendance(match.employee_id, attendance_type, log.timestamp, match.score)
        label = "Entrada" if attendance_type == AttendanceType.IN else "Salida"
        detail = f"{label} registrada ({match.score:.2f})"
        if rrhh_detail:
            detail += f" - {rrhh_detail}"
        self.kiosk.set_ok(match.name, detail)

    def _save_snapshot(self, frame, employee_id: int, source_id: str) -> str:
        folder = Path(self.config.snapshot_dir)
        folder.mkdir(parents=True, exist_ok=True)
        safe_source = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in source_id)
        path = folder / f"{employee_id}_{safe_source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(str(path), frame)
        return str(path)

    def _face_fits_guide(self, frame, bbox: tuple[int, int, int, int]) -> bool:
        x1, y1, x2, y2 = bbox
        gx1, gy1, gx2, gy2 = VideoLabel.guide_bbox(frame.shape)
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        face_width = max(1, x2 - x1)
        face_height = max(1, y2 - y1)
        guide_width = max(1, gx2 - gx1)
        guide_height = max(1, gy2 - gy1)
        center_inside = gx1 <= cx <= gx2 and gy1 <= cy <= gy2
        size_ok = face_width >= guide_width * 0.45 and face_width <= guide_width * 1.18
        height_ok = face_height >= guide_height * 0.42 and face_height <= guide_height * 1.18
        return center_inside and size_ok and height_ok

    def _select_kiosk_face(self, observations):
        if not observations:
            return None
        ranked = sorted(observations, key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]), reverse=True)
        primary = ranked[0]
        primary_area = (primary.bbox[2] - primary.bbox[0]) * (primary.bbox[3] - primary.bbox[1])
        significant = [
            item
            for item in ranked
            if (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]) >= primary_area * 0.35
        ]
        return primary if len(significant) == 1 else None

    def _sync_cached_faces(self) -> None:
        if self.syncing_faces:
            return
        self.syncing_faces = True
        def run_sync() -> None:
            try:
                changed = self.runtime.sync.sync_biometric_faces(self.runtime.rrhh)
                if changed:
                    self.runtime.recognition.refresh_index()
            finally:
                self.syncing_faces = False

        threading.Thread(
            target=run_sync,
            daemon=True,
        ).start()

    def _run_maintenance(self) -> None:
        threading.Thread(target=self.runtime.maintenance.cleanup_snapshots, daemon=True).start()

    def _update_system_status(self) -> None:
        provider = "ArcFace" if self.runtime.face_engine.provider == "insightface" else "Demo"
        sct = "SCT activo" if self.runtime.rrhh.enabled else "SCT sin configurar"
        mode = "Offline" if self.config.offline_mode else "Online"
        cameras = f"{len(self.cameras)} camara" if len(self.cameras) == 1 else f"{len(self.cameras)} camaras"
        self.kiosk.system_status.setText(
            f"{provider} | {self.runtime.recognition.registered_count} rostros | {cameras} | {sct} | {mode}"
        )

    def _record_rrhh_attendance(
        self,
        employee_id: int,
        attendance_type: AttendanceType,
        timestamp: datetime,
        confidence: float,
    ) -> str:
        employee = self.runtime.employees.get(employee_id)
        marker_code = employee.document_id if employee and employee.external_id and not employee.external_id.startswith("PENDING:") else None
        if not marker_code:
            return "sin vinculo externo"
        ok, message = self.runtime.rrhh.record_attendance(marker_code, attendance_type, timestamp, confidence)
        if ok:
            return "Integracion OK" if self.runtime.rrhh.mode != "sct" else "SCT OK"
        self.runtime.sync.queue_rrhh_attendance(marker_code, attendance_type, timestamp, confidence)
        return "Integracion en cola" if self.runtime.rrhh.mode != "sct" else "SCT en cola"

    def open_admin(self) -> None:
        dialog = PinDialog(self.config.admin_pin, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.stack.setCurrentWidget(self.admin)

    def show_kiosk(self) -> None:
        self._sync_cached_faces()
        self.stack.setCurrentWidget(self.kiosk)

    def show_admin(self) -> None:
        self.admin.refresh_camera_sources()
        self.stack.setCurrentWidget(self.admin)

    def show_settings(self) -> None:
        self.settings.load_values()
        self.stack.setCurrentWidget(self.settings)

    def closeEvent(self, event) -> None:
        for camera in self.cameras:
            camera.stop()
        event.accept()


class KioskPage(QWidget):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        header = QHBoxLayout()
        title = QLabel("Punto Biometrico")
        title.setObjectName("Title")
        self.system_status = QLabel("")
        self.system_status.setObjectName("Hint")
        self.camera_label = QLabel("")
        self.camera_label.setObjectName("Hint")
        admin_button = QPushButton("Admin")
        admin_button.clicked.connect(window.open_admin)
        header.addWidget(title)
        header.addWidget(self.system_status)
        header.addWidget(self.camera_label)
        header.addStretch()
        header.addWidget(admin_button)

        self.video = VideoLabel()
        self.video.setMinimumSize(900, 520)
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status = QLabel("Acerquese para marcar asistencia")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setObjectName("StatusOk")
        self.detail = QLabel("")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setObjectName("Hint")

        layout.addLayout(header)
        layout.addWidget(self.video, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.detail)

    def set_ok(self, title: str, detail: str) -> None:
        self.status.setObjectName("StatusOk")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(title)
        self.detail.setText(detail)

    def set_error(self, message: str) -> None:
        self.status.setObjectName("StatusError")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(message)
        self.detail.setText("Intente nuevamente")

    def set_camera_label(self, source_name: str, total_sources: int) -> None:
        if total_sources <= 1:
            self.camera_label.setText(source_name)
        else:
            self.camera_label.setText(f"Vista: {source_name}")

    def reset_prompt(self) -> None:
        self.status.setObjectName("StatusOk")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText("Acerquese para marcar asistencia")
        self.detail.setText("")


class AdminPage(QWidget):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.captured_embeddings = []
        self.last_observation = None
        self.last_detection_frame: dict[str, int] = {}
        self.rrhh_employees = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        title = QLabel("Administracion")
        title.setObjectName("Title")

        content = QHBoxLayout()
        camera_panel = QVBoxLayout()
        self.video = VideoLabel()
        self.video.setMinimumSize(720, 420)
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_preview = VideoLabel()
        self.face_preview.setMinimumSize(220, 220)
        self.face_preview.setMaximumSize(280, 280)
        self.face_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_preview.setFrameShape(QFrame.Shape.Box)
        self.preview_status = QLabel("Vista previa pendiente")
        self.preview_status.setObjectName("Hint")
        camera_panel.addWidget(self.video, 1)
        camera_panel.addWidget(self.preview_status)

        form_panel = QVBoxLayout()
        form = QFormLayout()
        self.camera_source = QComboBox()
        self.refresh_camera_sources()
        self.camera_source.currentIndexChanged.connect(self.on_camera_selected)
        form.addRow("Camara", self.camera_source)

        self.rrhh_employee = QComboBox()
        self.rrhh_employee.setEditable(True)
        self.rrhh_employee.currentIndexChanged.connect(self.on_rrhh_selected)
        refresh_rrhh = QPushButton("Actualizar RRHH")
        refresh_rrhh.clicked.connect(self.load_rrhh_employees)
        rrhh_row = QHBoxLayout()
        rrhh_row.addWidget(self.rrhh_employee, 1)
        rrhh_row.addWidget(refresh_rrhh)
        form.addRow("Empleado RRHH", rrhh_row)

        actions = QHBoxLayout()
        capture = QPushButton("Capturar muestra")
        capture.clicked.connect(self.capture_sample)
        save = QPushButton("Guardar rostro")
        save.clicked.connect(self.save_face)
        clear = QPushButton("Limpiar muestras")
        clear.clicked.connect(self.clear_samples)
        settings = QPushButton("Configuracion")
        settings.clicked.connect(window.show_settings)
        back = QPushButton("Volver")
        back.clicked.connect(window.show_kiosk)
        actions.addWidget(capture)
        actions.addWidget(save)
        actions.addWidget(clear)
        actions.addWidget(settings)
        actions.addStretch()
        actions.addWidget(back)

        self.counter = QLabel("Muestras capturadas: 0")
        self.counter.setObjectName("Hint")
        self.hint = QLabel("Capture entre 5 y 10 muestras con pequenos cambios de angulo.")
        self.hint.setObjectName("Hint")

        layout.addWidget(title)
        form_panel.addLayout(form)
        form_panel.addWidget(QLabel("Ultima muestra"))
        form_panel.addWidget(self.face_preview)
        form_panel.addWidget(self.counter)
        form_panel.addWidget(self.hint)
        form_panel.addStretch()
        content.addLayout(camera_panel, 3)
        content.addLayout(form_panel, 1)
        layout.addLayout(content, 1)
        layout.addLayout(actions)
        self.load_rrhh_employees()

    def refresh_camera_sources(self) -> None:
        current = self.selected_camera_id() if hasattr(self, "camera_source") else self.window.active_source_id
        self.camera_source.blockSignals(True)
        self.camera_source.clear()
        for source in self.window.config.active_camera_sources:
            self.camera_source.addItem(source.name, source.id)
        index = self.camera_source.findData(current)
        if index < 0:
            index = self.camera_source.findData(self.window.active_source_id)
        if index >= 0:
            self.camera_source.setCurrentIndex(index)
        self.camera_source.blockSignals(False)

    def selected_camera_id(self) -> str:
        if self.camera_source.count():
            source_id = self.camera_source.currentData()
            if source_id:
                return str(source_id)
        return self.window.active_source_id

    def on_camera_selected(self, *_args) -> None:
        source_id = self.selected_camera_id()
        self.window.active_source_id = source_id
        self.last_observation = None
        latest = self.window.latest_frames.get(source_id)
        if latest:
            self.update_preview(latest)

    def _selected_camera_frame(self) -> CameraFrame | None:
        return self.window.latest_frames.get(self.selected_camera_id())

    def update_preview(self, camera_frame: CameraFrame) -> None:
        source_id = camera_frame.source_id
        if source_id != self.selected_camera_id():
            return
        frame = camera_frame.frame
        n = max(1, self.window.config.recognition.process_every_n_frames)
        frame_count = self.window.frame_counts.get(source_id, 0)
        if frame_count - self.last_detection_frame.get(source_id, 0) >= n:
            observations = self.window.runtime.face_engine.extract(frame)
            self.last_observation, message = self._select_registration_face(frame, observations)
            self.last_detection_frame[source_id] = frame_count
            if message:
                self.preview_status.setText(message)

        bbox = self.last_observation.bbox if self.last_observation else None
        self.video.set_frame(frame, bbox)
        if self.last_observation:
            x1, y1, x2, y2 = self._safe_bbox(frame, self.last_observation.bbox)
            face = frame[y1:y2, x1:x2]
            if face.size:
                self.face_preview.set_frame(face, guide=False)
            self.preview_status.setText(f"Rostro detectado - calidad {self.last_observation.confidence:.2f}")
        elif not self.preview_status.text().startswith("Hay mas") and not self.preview_status.text().startswith("Acerque mas"):
            self.preview_status.setText("Acerque el rostro al centro de la camara")

    def capture_sample(self) -> None:
        camera_frame = self._selected_camera_frame()
        frame = camera_frame.frame if camera_frame else None
        if frame is None:
            QMessageBox.warning(self, "Camara", "No hay frame disponible")
            return
        observations = self.window.runtime.face_engine.extract(frame)
        observation, message = self._select_registration_face(frame, observations)
        if observation is None:
            QMessageBox.warning(self, "Rostro", message or "No se detecto un rostro")
            return
        x1, y1, x2, y2 = self._safe_bbox(frame, observation.bbox)
        quality_ok, quality_message, quality_score = self.window.runtime.face_engine.face_quality(frame, (x1, y1, x2, y2))
        if not quality_ok:
            QMessageBox.warning(self, "Calidad", quality_message)
            self.preview_status.setText(quality_message)
            return
        face = frame[y1:y2, x1:x2]
        if face.size:
            self.face_preview.set_frame(face, guide=False)
        self.captured_embeddings.append(observation.embedding)
        self.last_observation = observation
        remaining = max(0, 5 - len(self.captured_embeddings))
        self.counter.setText(f"Muestras capturadas: {len(self.captured_embeddings)}")
        self.preview_status.setText(
            f"Muestra agregada - calidad {quality_score:.2f}"
            if remaining == 0
            else f"Muestra agregada - faltan {remaining} - calidad {quality_score:.2f}"
        )

    def save_face(self) -> None:
        if len(self.captured_embeddings) < 5:
            QMessageBox.warning(self, "Muestras", "Capture al menos 5 muestras")
            return
        snapshot_path = self._save_enrollment_snapshot()
        consistency = self.window.runtime.face_engine.embedding_consistency(self.captured_embeddings)
        if consistency < 0.72 and self.window.runtime.face_engine.provider != "demo":
            QMessageBox.warning(
                self,
                "Muestras inconsistentes",
                "Las muestras no parecen pertenecer al mismo rostro. Limpie y capture nuevamente.",
            )
            return
        selected_rrhh = self._selected_rrhh_employee()
        embedding = self.window.runtime.face_engine.average_embeddings(self.captured_embeddings)
        duplicate = self.window.runtime.recognition.find_duplicate(embedding)
        if duplicate:
            QMessageBox.warning(
                self,
                "Rostro duplicado",
                f"Este rostro ya esta registrado como {duplicate.name} ({duplicate.score:.2f}).",
            )
            return
        if selected_rrhh:
            prefix = "SCT" if self.window.runtime.rrhh.mode == "sct" else "GENERIC"
            employee = self.window.runtime.employees.get_or_create_rrhh_employee(
                selected_rrhh.id,
                selected_rrhh.name,
                selected_rrhh.marker_code or None,
                prefix,
            )
        else:
            employee = self.window.runtime.employees.create_pending_employee()
        employee_id = employee.id
        try:
            face_row = self.window.runtime.recognition.register_embedding(employee_id, embedding, snapshot_path)
        except Exception as exc:
            logging.getLogger(__name__).warning("No se pudo registrar rostro employee_id=%s: %s", employee_id, exc)
            QMessageBox.warning(self, "Registro", f"No se pudo guardar el rostro: {exc}")
            return
        uploaded = False
        if snapshot_path:
            uploaded = self.window.runtime.rrhh.upload_biometric_face(
                local_employee_id=employee_id,
                local_face_embedding_id=face_row.id,
                person_name=employee.name,
                marker_code=(selected_rrhh.marker_code or None) if selected_rrhh else None,
                embedding=face_row.embedding,
                dimension=face_row.dimension,
                provider=face_row.provider,
                image_snapshot_path=snapshot_path,
            )
        self.clear_samples()
        message = "Rostro registrado correctamente"
        if uploaded:
            message += "\nFoto enviada al servidor SCT/RRHH"
        elif self.window.runtime.rrhh.enabled:
            detail = self.window.runtime.rrhh.last_error[:160] if self.window.runtime.rrhh.last_error else "sin detalle"
            message += f"\nQuedo en cache local; no se pudo enviar a SCT: {detail}"
        else:
            message += "\nQuedo en cache local; RRHH no esta configurado"
        QMessageBox.information(self, "Registro", message)

    def clear_samples(self) -> None:
        self.captured_embeddings.clear()
        self.counter.setText("Muestras capturadas: 0")
        self.preview_status.setText("Muestras limpiadas")

    def load_rrhh_employees(self) -> None:
        typed_query = self.rrhh_employee.currentText().strip() if self.rrhh_employee.count() else ""
        if typed_query == "Sin vincular / relacionar despues":
            typed_query = ""
        self.rrhh_employees = self.window.runtime.rrhh.list_employees(typed_query, limit=500)
        self.rrhh_employee.blockSignals(True)
        self.rrhh_employee.clear()
        self.rrhh_employee.addItem("Sin vincular / relacionar despues")
        for employee in self.rrhh_employees:
            self.rrhh_employee.addItem(employee.label)
        completer = QCompleter([employee.label for employee in self.rrhh_employees], self.rrhh_employee)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.rrhh_employee.setCompleter(completer)
        self.rrhh_employee.blockSignals(False)
        if self.rrhh_employees:
            self.preview_status.setText(f"RRHH cargado: {len(self.rrhh_employees)} empleados")
        elif self.window.runtime.rrhh.enabled:
            detail = self.window.runtime.rrhh.last_error[:180] if self.window.runtime.rrhh.last_error else "sin detalle"
            self.preview_status.setText(f"RRHH sin empleados: {detail}")
        else:
            self.preview_status.setText("RRHH no configurado: ingrese URL en Configuracion")

    def on_rrhh_selected(self, *_args) -> None:
        return

    def _selected_rrhh_employee(self):
        index = self.rrhh_employee.currentIndex()
        if index > 0:
            employee_index = index - 1
            if employee_index < len(self.rrhh_employees):
                return self.rrhh_employees[employee_index]
        text = self.rrhh_employee.currentText().strip()
        for employee in self.rrhh_employees:
            if employee.label == text:
                return employee
        return None

    def _select_registration_face(self, frame, observations):
        if not observations:
            return None, "No se detecto un rostro"
        height, width = frame.shape[:2]
        frame_area = width * height
        ranked = sorted(observations, key=lambda obs: self._face_area(obs.bbox), reverse=True)
        primary = ranked[0]
        primary_area = self._face_area(primary.bbox)
        significant_faces = [obs for obs in ranked if self._face_area(obs.bbox) >= primary_area * 0.35]
        if len(significant_faces) > 1:
            return None, "Hay mas de un rostro en primer plano"
        if primary_area < frame_area * 0.08:
            return None, "Acerque mas el rostro"
        x1, y1, x2, y2 = primary.bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        gx1, gy1, gx2, gy2 = VideoLabel.guide_bbox(frame.shape)
        guide_width = max(1, gx2 - gx1)
        guide_height = max(1, gy2 - gy1)
        face_width = max(1, x2 - x1)
        face_height = max(1, y2 - y1)
        if not (gx1 <= cx <= gx2 and gy1 <= cy <= gy2):
            return None, "Encaje el rostro en la guia"
        if face_width < guide_width * 0.45 or face_height < guide_height * 0.42:
            return None, "Acerque mas el rostro a la guia"
        if face_width > guide_width * 1.18 or face_height > guide_height * 1.18:
            return None, "Alejese un poco de la camara"
        return primary, ""

    def _face_area(self, bbox: tuple[int, int, int, int]) -> int:
        x1, y1, x2, y2 = bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    def _safe_bbox(self, frame, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        pad_x = max(12, int((x2 - x1) * 0.15))
        pad_y = max(12, int((y2 - y1) * 0.15))
        return (
            max(0, x1 - pad_x),
            max(0, y1 - pad_y),
            min(width, x2 + pad_x),
            min(height, y2 + pad_y),
        )

    def _save_enrollment_snapshot(self) -> str | None:
        camera_frame = self._selected_camera_frame()
        frame = camera_frame.frame if camera_frame else self.window.latest_frame
        observation = self.last_observation
        if frame is None or observation is None:
            return None
        x1, y1, x2, y2 = self._safe_bbox(frame, observation.bbox)
        face = frame[y1:y2, x1:x2]
        if not face.size:
            return None
        folder = Path(self.window.config.snapshot_dir) / "enrollments"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"face_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        cv2.imwrite(str(path), face)
        return str(path)


class SettingsPage(QWidget):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        title = QLabel("Configuracion")
        title.setObjectName("Title")

        form = QFormLayout()
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.1, 0.99)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 3600)
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 10)
        self.camera_sources_json = QTextEdit()
        self.camera_sources_json.setMinimumHeight(130)
        self.camera_sources_json.setMaximumHeight(180)
        self.camera_sources_json.setPlaceholderText(
            '[{"id":"FRONTAL","name":"Frontal","source":0,"enabled":true,"primary":true}]'
        )
        self.integration_mode = QComboBox()
        self.integration_mode.addItem("SCT/RRHH legado", "sct")
        self.integration_mode.addItem("Generico DB", "generic_db")
        self.integration_mode.addItem("Generico REST", "generic_rest")
        self.rrhh_url = QLineEdit()
        self.generic_db_url = QLineEdit()
        self.generic_rest_url = QLineEdit()
        self.generic_rest_token = QLineEdit()
        self.generic_rest_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.offline = QCheckBox("Usar SQLite local")
        self.anti_spoof = QCheckBox("Requerir prueba de vida")
        form.addRow("Threshold", self.threshold)
        form.addRow("Cooldown segundos", self.cooldown)
        form.addRow("Camara legacy", self.camera_index)
        form.addRow("Fuentes video", self.camera_sources_json)
        form.addRow("Modo integracion", self.integration_mode)
        form.addRow("URL RRHH", self.rrhh_url)
        form.addRow("URL DB generica", self.generic_db_url)
        form.addRow("URL REST generica", self.generic_rest_url)
        form.addRow("Token REST", self.generic_rest_token)
        form.addRow("Offline", self.offline)
        form.addRow("Anti-spoofing", self.anti_spoof)

        actions = QHBoxLayout()
        save = QPushButton("Guardar")
        save.clicked.connect(self.save)
        discover_rtsp = QPushButton("Descubrir RTSP")
        discover_rtsp.clicked.connect(self.discover_rtsp_sources)
        back = QPushButton("Volver")
        back.clicked.connect(window.show_admin)
        actions.addWidget(save)
        actions.addWidget(discover_rtsp)
        actions.addStretch()
        actions.addWidget(back)

        self.hint = QLabel("Algunos cambios, como camara o modo offline, requieren reiniciar la app.")
        self.hint.setObjectName("Hint")

        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.hint)
        layout.addStretch()
        self.load_values()

    def load_values(self) -> None:
        config = self.window.config
        self.threshold.setValue(config.threshold)
        self.cooldown.setValue(config.cooldown_seconds)
        self.camera_index.setValue(config.camera_index)
        self.camera_sources_json.setPlainText(
            json.dumps([self._camera_source_to_dict(source) for source in config.camera_sources], indent=2)
        )
        index = self.integration_mode.findData(config.integration_mode)
        self.integration_mode.setCurrentIndex(index if index >= 0 else 0)
        self.rrhh_url.setText(config.rrhh_mysql_url)
        self.generic_db_url.setText(config.generic_db.url)
        self.generic_rest_url.setText(config.generic_rest.base_url)
        self.generic_rest_token.setText(config.generic_rest.token)
        self.offline.setChecked(config.offline_mode)
        self.anti_spoof.setChecked(config.recognition.anti_spoofing_required)

    def save(self) -> None:
        config = self.window.config
        sources_text = self.camera_sources_json.toPlainText().strip()
        camera_sources: list[CameraSourceConfig] = []
        if sources_text:
            try:
                raw_sources = json.loads(sources_text)
                if not isinstance(raw_sources, list):
                    raise ValueError("Fuentes video debe ser una lista JSON")
                camera_sources = parse_camera_sources(raw_sources)
            except Exception as exc:
                QMessageBox.warning(self, "Configuracion", f"Fuentes video invalidas: {exc}")
                return
        config.threshold = self.threshold.value()
        config.cooldown_seconds = self.cooldown.value()
        config.camera_index = self.camera_index.value()
        config.camera_sources = camera_sources
        config.integration_mode = str(self.integration_mode.currentData() or "sct")
        config.rrhh_mysql_url = self.rrhh_url.text().strip()
        config.generic_db.url = self.generic_db_url.text().strip()
        config.generic_rest.base_url = self.generic_rest_url.text().strip()
        config.generic_rest.token = self.generic_rest_token.text().strip()
        config.offline_mode = self.offline.isChecked()
        config.recognition.anti_spoofing_required = self.anti_spoof.isChecked()
        path = Path("config.json")
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        data.update(
            {
                "threshold": config.threshold,
                "cooldown_seconds": config.cooldown_seconds,
                "camera_index": config.camera_index,
                "camera_sources": [self._camera_source_to_dict(source) for source in config.camera_sources],
                "integration_mode": config.integration_mode,
                "rrhh_mysql_url": config.rrhh_mysql_url,
                "generic_db": self._generic_db_to_dict(),
                "generic_rest": self._generic_rest_to_dict(),
                "offline_mode": config.offline_mode,
            }
        )
        recognition = data.setdefault("recognition", {})
        recognition["anti_spoofing_required"] = config.recognition.anti_spoofing_required
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.window.runtime.rrhh.dispose()
        self.window.runtime.rrhh = RrhhService(config)
        self.window.admin.load_rrhh_employees()
        QMessageBox.information(
            self,
            "Configuracion",
            "Configuracion guardada. Reinicie la app para aplicar cambios de camaras.",
        )

    def discover_rtsp_sources(self) -> None:
        self.hint.setText("Buscando camaras RTSP en la red local...")
        candidates = VideoSourceDiscovery(self.window.config).discover()
        if not candidates:
            self.hint.setText("No se encontraron camaras RTSP abiertas")
            QMessageBox.information(self, "RTSP", "No se encontraron fuentes RTSP abiertas en la red local.")
            return
        try:
            current_sources = json.loads(self.camera_sources_json.toPlainText() or "[]")
            if not isinstance(current_sources, list):
                current_sources = []
        except json.JSONDecodeError:
            current_sources = []
        known_sources = {str(item.get("source")) for item in current_sources if isinstance(item, dict)}
        added = 0
        for candidate in candidates:
            source_url = candidate.urls[0]
            if source_url in known_sources:
                continue
            current_sources.append(
                {
                    "id": f"RTSP_{candidate.host.replace('.', '_')}_{candidate.port}",
                    "name": f"RTSP {candidate.host}:{candidate.port}",
                    "source": source_url,
                    "enabled": False,
                    "width": 1280,
                    "height": 720,
                    "fps_limit": 15,
                    "primary": False,
                }
            )
            added += 1
        self.camera_sources_json.setPlainText(json.dumps(current_sources, indent=2))
        preview = "\n".join(url for candidate in candidates[:5] for url in candidate.urls[:2])
        self.hint.setText(f"RTSP encontrados: {len(candidates)} fuentes, agregadas {added} como deshabilitadas")
        QMessageBox.information(
            self,
            "RTSP",
            f"Se encontraron {len(candidates)} fuente(s). Se agregaron como deshabilitadas.\n\n{preview}",
        )

    def _camera_source_to_dict(self, source: CameraSourceConfig) -> dict:
        return {
            "id": source.id,
            "name": source.name,
            "source": source.source,
            "enabled": source.enabled,
            "width": source.width,
            "height": source.height,
            "fps_limit": source.fps_limit,
            "primary": source.primary,
        }

    def _generic_db_to_dict(self) -> dict:
        db_config = self.window.config.generic_db
        return {
            "url": db_config.url,
            "employee_query": db_config.employee_query,
            "attendance_insert_sql": db_config.attendance_insert_sql,
            "biometric_faces_table": db_config.biometric_faces_table,
        }

    def _generic_rest_to_dict(self) -> dict:
        rest_config = self.window.config.generic_rest
        return {
            "base_url": rest_config.base_url,
            "token": rest_config.token,
            "employees_path": rest_config.employees_path,
            "biometric_faces_path": rest_config.biometric_faces_path,
            "attendance_path": rest_config.attendance_path,
            "timeout_seconds": rest_config.timeout_seconds,
        }


class PinDialog(QDialog):
    def __init__(self, pin: str, parent=None):
        super().__init__(parent)
        self.pin = pin
        self.setWindowTitle("PIN Admin")
        layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        button = QPushButton("Ingresar")
        button.clicked.connect(self.validate)
        layout.addWidget(QLabel("PIN"))
        layout.addWidget(self.input)
        layout.addWidget(button)

    def validate(self) -> None:
        if self.input.text() == self.pin:
            self.accept()
        else:
            QMessageBox.warning(self, "PIN", "PIN incorrecto")
