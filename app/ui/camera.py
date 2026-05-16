from __future__ import annotations

from dataclasses import dataclass
import cv2
import logging

from PyQt6.QtCore import QThread, pyqtSignal

from app.config import CameraSourceConfig


@dataclass(slots=True)
class CameraFrame:
    source_id: str
    source_name: str
    frame: object


@dataclass(slots=True)
class CameraError:
    source_id: str
    source_name: str
    message: str


def normalize_video_source(source: int | str) -> int | str:
    if isinstance(source, str):
        text = source.strip()
        if text.isdigit():
            return int(text)
        return text
    return source


class CameraThread(QThread):
    frame_ready = pyqtSignal(object)
    camera_error = pyqtSignal(object)

    def __init__(self, source_config: CameraSourceConfig):
        super().__init__()
        self.source_config = source_config
        self._running = True
        self._cap = None

    def run(self) -> None:
        logger = logging.getLogger(__name__)
        source = normalize_video_source(self.source_config.source)
        logger.info(
            "Abriendo fuente de video id=%s name=%s source=%s",
            self.source_config.id,
            self.source_config.name,
            source,
        )
        while self._running:
            cap = self._open_capture(source)
            if cap is None:
                self._emit_error(f"No se pudo abrir la camara {self.source_config.name}")
                self.msleep(2000)
                continue

            failed_reads = 0
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    failed_reads += 1
                    if failed_reads >= 10:
                        logger.warning("Lectura fallida en camara %s, reintentando", self.source_config.id)
                        self._emit_error(f"Senal perdida en {self.source_config.name}; reconectando")
                        break
                    self.msleep(120)
                    continue

                failed_reads = 0
                self.frame_ready.emit(
                    CameraFrame(
                        source_id=self.source_config.id,
                        source_name=self.source_config.name,
                        frame=frame,
                    )
                )
                delay_ms = int(1000 / max(1, min(60, self.source_config.fps_limit)))
                self.msleep(delay_ms)

            cap.release()
            self._cap = None

        logger.info("Camara liberada id=%s", self.source_config.id)

    def _open_capture(self, source: int | str):
        cap = self._create_capture(source)
        self._cap = cap
        if not cap.isOpened():
            cap.release()
            self._cap = None
            return None
        if self.source_config.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.source_config.width)
        if self.source_config.height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.source_config.height)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def _create_capture(self, source: int | str):
        if isinstance(source, str) and "://" in source:
            params = []
            open_timeout = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
            read_timeout = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
            if open_timeout is not None:
                params.extend([open_timeout, 5000])
            if read_timeout is not None:
                params.extend([read_timeout, 5000])
            if params:
                try:
                    return cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
                except Exception:
                    logging.getLogger(__name__).debug("OpenCV no soporta parametros de timeout para VideoCapture")
        return cv2.VideoCapture(source)

    def _emit_error(self, message: str) -> None:
        logging.getLogger(__name__).error("%s (%s)", message, self.source_config.id)
        self.camera_error.emit(
            CameraError(
                source_id=self.source_config.id,
                source_name=self.source_config.name,
                message=message,
            )
        )

    def stop(self) -> None:
        self._running = False
        if self._cap is not None:
            self._cap.release()
        self.wait(1500)
