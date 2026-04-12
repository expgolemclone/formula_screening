#!/usr/bin/env python3
"""Fetch 発行済株式数 from kabutan into the screening DB.

Thin wrapper around: uv run python -m formula_screening fetch-shares

All arguments are forwarded to the CLI subcommand.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.cli import main

if __name__ == "__main__":
    sys.argv = ["formula_screening", "fetch-shares", *sys.argv[1:]]
    main()
