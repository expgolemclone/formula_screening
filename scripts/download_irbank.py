#!/usr/bin/env python3
"""Download IR BANK JSON files via qutebrowser.

Usage:
    python scripts/download_irbank.py [--years N] [--dest DIR]

Opens qutebrowser and sends :download commands for each year/file combination.
Files are saved to data/irbank/<year_code>/ by default.

After download completes, import into DB with:
    uv run python -m formula_screening import-data --dir data/irbank --all
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BASE_URL = "https://f.irbank.net/files"
_JSON_FILES = [
    "fy-profit-and-loss.json",
    "fy-balance-sheet.json",
    "fy-cash-flow-statement.json",
    "fy-stock-dividend.json",
]


def _find_qutebrowser() -> str:
    path = shutil.which("qutebrowser")
    if path:
        return path
    msg = "qutebrowser not found in PATH"
    raise FileNotFoundError(msg)


def _year_codes(years: int) -> list[str]:
    latest = datetime.now(timezone.utc).year - 1
    return [f"{y % 100:04d}" for y in range(latest - years + 1, latest + 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Download IR BANK JSON via qutebrowser")
    parser.add_argument("--years", type=int, default=10, help="Number of fiscal years (default: 10)")
    parser.add_argument("--dest", type=str, default=None, help="Destination directory (default: data/irbank)")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between downloads (default: 1.0)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    dest = Path(args.dest) if args.dest else project_root / "data" / "irbank"

    qb = _find_qutebrowser()
    codes = _year_codes(args.years)
    total = len(codes) * len(_JSON_FILES)

    print(f"Downloading {total} files for years: {', '.join(codes)}")
    print(f"Destination: {dest}")

    count = 0
    for code in codes:
        out_dir = dest / code
        out_dir.mkdir(parents=True, exist_ok=True)

        for filename in _JSON_FILES:
            url = f"{_BASE_URL}/{code}/{filename}"
            target = out_dir / filename
            count += 1

            print(f"[{count}/{total}] {url}")
            # qutebrowser :download --dest <path> <url>
            cmd = f":download --dest {target} {url}"
            subprocess.run([qb, cmd], check=False)

            if count < total:
                time.sleep(args.interval)

    print(f"\nDone. Import with:\n  uv run python -m formula_screening import-data --dir {dest} --all")


if __name__ == "__main__":
    main()
