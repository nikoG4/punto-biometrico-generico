from __future__ import annotations

import argparse
import sys
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app.config import load_config
from app.db.session import DatabaseManager
from app.logging_config import configure_logging
from app.services.bootstrap import bootstrap_runtime
from app.ui.main_window import MainWindow


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Punto Biometrico Facial")
    parser.add_argument("--diagnose", action="store_true", help="Ejecuta diagnostico operativo y sale")
    parser.add_argument("--no-fullscreen", action="store_true", help="Abre la UI en ventana para pruebas")
    parser.add_argument("--exit-after-ms", type=int, default=0, help="Cierra la UI automaticamente luego de N ms")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    configure_logging()
    logger = logging.getLogger(__name__)

    def log_uncaught(exc_type, exc, traceback) -> None:
        logger.critical("Excepcion no controlada", exc_info=(exc_type, exc, traceback))

    sys.excepthook = log_uncaught
    config = load_config()
    if args.diagnose:
        from app.diagnostics import run_diagnostics

        return run_diagnostics(config)

    logger.info("Iniciando punto biometrico device_id=%s offline=%s", config.device_id, config.offline_mode)
    db = DatabaseManager(config)
    runtime = bootstrap_runtime(config, db)

    qt_app = QApplication(sys.argv)
    window = MainWindow(config=config, runtime=runtime)
    if args.exit_after_ms > 0:
        QTimer.singleShot(args.exit_after_ms, qt_app.quit)
    if args.no_fullscreen:
        window.showMaximized()
    else:
        window.showFullScreen()
    try:
        return qt_app.exec()
    finally:
        db.dispose()
        logger.info("Aplicacion finalizada")


if __name__ == "__main__":
    raise SystemExit(main())
