#!/usr/bin/env python3
"""Fetch and cache stock prices from Stooq into the screening DB.

Thin wrapper around: uv run python -m formula_screening fetch-prices

All arguments are forwarded to the CLI subcommand.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.cli import main

if __name__ == "__main__":
    sys.argv = ["formula_screening", "fetch-prices", *sys.argv[1:]]
    main()
