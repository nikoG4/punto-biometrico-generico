from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config
from app.services.video_discovery import VideoSourceDiscovery


def main() -> int:
    config = load_config()
    candidates = VideoSourceDiscovery(config).discover()
    if not candidates:
        print("No se encontraron fuentes RTSP abiertas.")
        return 1
    for candidate in candidates:
        print(f"{candidate.host}:{candidate.port}")
        for url in candidate.urls:
            print(f"  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
