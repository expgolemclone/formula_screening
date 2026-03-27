#!/usr/bin/env python3
"""Download IR BANK JSON files via HTTP.

Usage:
    uv run python scripts/download_irbank.py [--years N] [--dest DIR]

Downloads annual financial JSON files from https://f.irbank.net/files/
and saves them to data/irbank/<year_code>/ by default.

After download completes, import into DB with:
    uv run python -m formula_screening import-data --dir data/irbank --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

_BASE_URL = "https://f.irbank.net/files"
_JSON_FILES = [
    "fy-profit-and-loss.json",
    "fy-balance-sheet.json",
    "fy-cash-flow-statement.json",
    "fy-stock-dividend.json",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://irbank.net/download",
}
_MAX_RETRIES = 3
_RETRY_WAIT = 10.0  # seconds to wait on rate-limit before retry


def _year_codes(years: int) -> list[str]:
    latest = datetime.now(timezone.utc).year - 1
    return [f"{y % 100:04d}" for y in range(latest - years + 1, latest + 1)]


def _download_file(
    session: requests.Session, url: str, dest: Path, *, timeout: float = 30
) -> bool:
    """Download a single file with retry on rate-limit. Returns True on success."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()

            # Detect HTML error page (rate-limit response)
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type or resp.content.lstrip()[:1] == b"<":
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_WAIT * attempt
                    print(f"  Rate-limited, retrying in {wait:.0f}s (attempt {attempt}/{_MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                print("  ERROR: still rate-limited after retries", file=sys.stderr)
                return False

            # Validate JSON
            json.loads(resp.content)
            dest.write_bytes(resp.content)
            return True
        except requests.RequestException as e:
            if attempt < _MAX_RETRIES:
                print(f"  {e}, retrying in {_RETRY_WAIT:.0f}s...")
                time.sleep(_RETRY_WAIT)
                continue
            print(f"  ERROR: {e}", file=sys.stderr)
            return False
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Download IR BANK JSON files")
    parser.add_argument("--years", type=int, default=10, help="Number of fiscal years (default: 10)")
    parser.add_argument("--dest", type=str, default=None, help="Destination directory (default: data/irbank)")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between downloads (default: 3.0)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    dest = Path(args.dest) if args.dest else project_root / "data" / "irbank"

    codes = _year_codes(args.years)
    total = len(codes) * len(_JSON_FILES)
    ok = 0
    fail = 0

    print(f"Downloading {total} files for years: {', '.join(codes)}")
    print(f"Destination: {dest}")

    session = requests.Session()
    session.headers.update(_HEADERS)

    count = 0
    for code in codes:
        out_dir = dest / code
        out_dir.mkdir(parents=True, exist_ok=True)

        for filename in _JSON_FILES:
            url = f"{_BASE_URL}/{code}/{filename}"
            target = out_dir / filename
            count += 1

            print(f"[{count}/{total}] {url}")
            if _download_file(session, url, target):
                ok += 1
            else:
                fail += 1

            if count < total:
                time.sleep(args.interval)

    print(f"\nDone: {ok} succeeded, {fail} failed.")
    if ok > 0:
        print(f"Import with:\n  uv run python -m formula_screening import-data --dir {dest} --all")


if __name__ == "__main__":
    main()
