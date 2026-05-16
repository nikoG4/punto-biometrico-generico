from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RecognitionConfig:
    provider: str = "insightface"
    model_name: str = "buffalo_l"
    det_size: tuple[int, int] = (640, 640)
    min_face_size: int = 40
    process_every_n_frames: int = 3
    anti_spoofing_required: bool = False
    blink_required: bool = False
    movement_required: bool = False


@dataclass(slots=True)
class CameraSourceConfig:
    id: str = "CAM_0"
    name: str = "Camara 0"
    source: int | str = 0
    enabled: bool = True
    width: int = 1280
    height: int = 720
    fps_limit: int = 30
    primary: bool = False


@dataclass(slots=True)
class GenericDbConfig:
    url: str = ""
    employee_query: str = (
        "SELECT id, code AS marker_code, name, COALESCE(status, '') AS status "
        "FROM employees "
        "WHERE (:query = '' OR name LIKE :query_like OR code LIKE :query_like) "
        "ORDER BY name LIMIT :limit"
    )
    attendance_insert_sql: str = (
        "INSERT INTO attendance_events "
        "(employee_code, timestamp, type, device_id, confidence) "
        "VALUES (:marker_code, :timestamp, :type, :device_id, :confidence)"
    )
    biometric_faces_table: str = "biometric_faces"


@dataclass(slots=True)
class GenericRestConfig:
    base_url: str = ""
    token: str = ""
    employees_path: str = "/employees"
    biometric_faces_path: str = "/biometric-faces"
    attendance_path: str = "/attendance"
    timeout_seconds: int = 8


@dataclass(slots=True)
class VideoDiscoveryConfig:
    enabled: bool = True
    subnet: str = "auto"
    ports: list[int] = field(default_factory=lambda: [554, 8554])
    timeout_ms: int = 350
    max_workers: int = 64


@dataclass(slots=True)
class AppConfig:
    device_id: str = "DEVICE_001"
    device_location: str = "Recepcion"
    threshold: float = 0.65
    cooldown_seconds: int = 60
    min_mark_interval_seconds: int = 3600
    camera_index: int = 0
    camera_sources: list[CameraSourceConfig] = field(default_factory=list)
    offline_mode: bool = False
    mysql_url: str = "mysql+pymysql://biometric_user:biometric_pass@127.0.0.1:3306/biometric_attendance"
    rrhh_mysql_url: str = ""
    rrhh_only_active: bool = True
    rrhh_attendance_user_id: int | None = None
    integration_mode: str = "sct"
    generic_db: GenericDbConfig = field(default_factory=GenericDbConfig)
    generic_rest: GenericRestConfig = field(default_factory=GenericRestConfig)
    sqlite_url: str = "sqlite:///local_cache.db"
    snapshot_dir: str = "snapshots"
    snapshot_retention_days: int = 30
    snapshot_max_mb: int = 1024
    admin_pin: str = "1234"
    recognition: RecognitionConfig = field(default_factory=RecognitionConfig)
    video_discovery: VideoDiscoveryConfig = field(default_factory=VideoDiscoveryConfig)
    sound_enabled: bool = True

    @property
    def primary_db_url(self) -> str:
        return self.sqlite_url if self.offline_mode else self.mysql_url

    @property
    def active_camera_sources(self) -> list[CameraSourceConfig]:
        sources = [source for source in self.camera_sources if source.enabled]
        if not sources:
            return [
                CameraSourceConfig(
                    id=f"CAM_{self.camera_index}",
                    name=f"Camara {self.camera_index}",
                    source=self.camera_index,
                    primary=True,
                )
            ]
        if not any(source.primary for source in sources):
            sources[0].primary = True
        return sources

    @property
    def primary_camera_source(self) -> CameraSourceConfig:
        for source in self.active_camera_sources:
            if source.primary:
                return source
        return self.active_camera_sources[0]


def parse_camera_sources(raw_sources: Any) -> list[CameraSourceConfig]:
    if not isinstance(raw_sources, list):
        return []
    fields = set(CameraSourceConfig.__dataclass_fields__)
    parsed: list[CameraSourceConfig] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_sources):
        if not isinstance(item, dict):
            continue
        values = {key: value for key, value in item.items() if key in fields}
        source_id = str(values.get("id") or f"CAM_{index}").strip() or f"CAM_{index}"
        if source_id in used_ids:
            source_id = f"{source_id}_{index}"
        used_ids.add(source_id)
        values["id"] = source_id
        values["name"] = str(values.get("name") or source_id)
        if "source" not in values:
            values["source"] = index
        if isinstance(values["source"], str):
            values["source"] = values["source"].strip()
        parsed.append(CameraSourceConfig(**values))
    return parsed


def _parse_dataclass(raw: Any, cls):
    if not isinstance(raw, dict):
        return cls()
    fields = set(cls.__dataclass_fields__)
    values = {key: value for key, value in raw.items() if key in fields}
    return cls(**values)


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(path or os.getenv("BIOMETRIC_CONFIG", "config.json"))
    if not config_path.exists():
        example = Path("config.example.json")
        if example.exists():
            config_path.write_text(example.read_text(encoding="utf-8-sig"), encoding="utf-8")
        else:
            return AppConfig()

    try:
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        bad_path = config_path.with_suffix(f".bad-{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        config_path.replace(bad_path)
        example = Path("config.example.json")
        if example.exists():
            config_path.write_text(example.read_text(encoding="utf-8-sig"), encoding="utf-8")
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        else:
            raw = {}
    recognition_raw = raw.pop("recognition", {})
    if "det_size" in recognition_raw:
        recognition_raw["det_size"] = tuple(recognition_raw["det_size"])
    recognition_fields = set(RecognitionConfig.__dataclass_fields__)
    recognition_raw = {key: value for key, value in recognition_raw.items() if key in recognition_fields}
    generic_db_raw = raw.pop("generic_db", {})
    generic_rest_raw = raw.pop("generic_rest", {})
    video_discovery_raw = raw.pop("video_discovery", {})
    raw["camera_sources"] = parse_camera_sources(raw.get("camera_sources", []))
    config_fields = set(AppConfig.__dataclass_fields__) - {"recognition", "generic_db", "generic_rest", "video_discovery"}
    raw = {key: value for key, value in raw.items() if key in config_fields}
    return AppConfig(
        **raw,
        recognition=RecognitionConfig(**recognition_raw),
        generic_db=_parse_dataclass(generic_db_raw, GenericDbConfig),
        generic_rest=_parse_dataclass(generic_rest_raw, GenericRestConfig),
        video_discovery=_parse_dataclass(video_discovery_raw, VideoDiscoveryConfig),
    )
