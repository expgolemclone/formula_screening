"""Application configuration and directory management."""

import os
from pathlib import Path

# Project root: two levels up from this file (src/formula_screening/config.py -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = _PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "screening.db"

EDINETDB_API_KEY = os.environ.get("EDINETDB_API_KEY", "")
EDINETDB_BASE_URL = "https://edinetdb.jp/v1"


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
