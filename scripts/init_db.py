from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config
from app.db.session import DatabaseManager


def main() -> None:
    config = load_config()
    DatabaseManager(config)
    print("Schema inicializado.")


if __name__ == "__main__":
    main()
