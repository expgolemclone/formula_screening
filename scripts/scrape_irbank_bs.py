#!/usr/bin/env python3
"""Scrape detailed BS data from IRBank individual stock pages.

Thin wrapper around: uv run python -m formula_screening scrape-bs

All arguments are forwarded to the CLI subcommand.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.cli import main

if __name__ == "__main__":
    sys.argv = ["formula_screening", "scrape-bs", *sys.argv[1:]]
    main()
