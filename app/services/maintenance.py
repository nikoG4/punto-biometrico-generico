from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from app.config import AppConfig


class MaintenanceService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)

    def cleanup_snapshots(self) -> int:
        folder = Path(self.config.snapshot_dir)
        if not folder.exists():
            return 0
        deleted = 0
        cutoff = datetime.now() - timedelta(days=max(1, self.config.snapshot_retention_days))
        files = [item for item in folder.rglob("*.jpg") if item.is_file()]
        for path in files:
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
                if modified < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError as exc:
                self.logger.warning("No se pudo limpiar snapshot %s: %s", path, exc)

        max_bytes = max(32, self.config.snapshot_max_mb) * 1024 * 1024
        remaining = [item for item in folder.rglob("*.jpg") if item.is_file()]
        total = sum(item.stat().st_size for item in remaining)
        if total <= max_bytes:
            return deleted
        for path in sorted(remaining, key=lambda item: item.stat().st_mtime):
            if total <= max_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
                deleted += 1
            except OSError as exc:
                self.logger.warning("No se pudo limpiar snapshot %s: %s", path, exc)
        if deleted:
            self.logger.info("Snapshots limpiados: %s", deleted)
        return deleted
