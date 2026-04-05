"""Application configuration and directory management."""

import tomllib
from pathlib import Path

# Project root: two levels up from this file (src/formula_screening/config.py -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


def _load_toml(name: str) -> dict:
    with (_CONFIG_DIR / name).open("rb") as f:
        return tomllib.load(f)


MAGIC: dict = _load_toml("magic_numbers.toml")
PATHS: dict = _load_toml("path.toml")
CLI_DEFAULTS: dict = _load_toml("cli_defaults.toml")

VALIDATION_SITES_FILE = _CONFIG_DIR / "validation_sites.txt"

DATA_DIR = _PROJECT_ROOT / PATHS["data"]["root"]
LOG_DIR = _PROJECT_ROOT / PATHS["data"]["log"]
DB_PATH = _PROJECT_ROOT / PATHS["data"]["db"]
IRBANK_DIR = _PROJECT_ROOT / PATHS["data"]["irbank"]
HASH_FILE = _PROJECT_ROOT / PATHS["data"]["hash_file"]
PROXY_FAILURE_CACHE = _PROJECT_ROOT / PATHS["data"]["proxy_failure_cache"]

def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
