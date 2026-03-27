#!/usr/bin/env python3
"""Download IR BANK JSON files via HTTP with Tor IP rotation.

Usage:
    uv run python scripts/download_irbank.py [--years N] [--dest DIR]

Requires a running Tor proxy (tor service on port 9050 or Tor Browser on 9150).
Each download uses a fresh Tor circuit (= different IP) via random SOCKS auth.
Already-downloaded valid files are skipped (use --force to re-download).

After download completes, import into DB with:
    uv run python -m formula_screening import-data --dir data/irbank --all
"""

from __future__ import annotations

import argparse
import json
import random
import string
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
_MAX_RETRIES = 5
_RETRY_WAIT = 10.0


def _year_codes(years: int) -> list[str]:
    latest = datetime.now(timezone.utc).year - 1
    return [f"{y % 100:04d}" for y in range(latest - years + 1, latest + 1)]


def _detect_tor_port() -> int | None:
    """Detect running Tor SOCKS port (service=9050, Browser=9150)."""
    import socket

    for port in (9050, 9150):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return port
        except OSError:
            continue
    return None


def _make_session(tor_port: int | None) -> requests.Session:
    """Create a requests session, optionally routed through Tor with a fresh circuit."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    if tor_port is not None:
        # Random SOCKS auth forces Tor to use a new circuit (IsolateSOCKSAuth)
        user = "".join(random.choices(string.ascii_lowercase, k=10))
        proxy = f"socks5h://{user}:{user}@127.0.0.1:{tor_port}"
        session.proxies = {"http": proxy, "https": proxy}

    return session


def _is_rate_limited(resp: requests.Response) -> bool:
    content_type = resp.headers.get("Content-Type", "")
    return "html" in content_type or resp.content.lstrip()[:1] == b"<"


def _download_file(
    url: str, dest: Path, *, tor_port: int | None, timeout: float = 30
) -> bool:
    """Download a single file. Each attempt uses a fresh Tor circuit."""
    for attempt in range(1, _MAX_RETRIES + 1):
        session = _make_session(tor_port)
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()

            if _is_rate_limited(resp):
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_WAIT * attempt
                    print(f"  Rate-limited, new circuit + retry in {wait:.0f}s ({attempt}/{_MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                print("  ERROR: still rate-limited after retries", file=sys.stderr)
                return False

            json.loads(resp.content)
            dest.write_bytes(resp.content)
            return True
        except requests.RequestException as e:
            if attempt < _MAX_RETRIES:
                print(f"  {e}, new circuit + retry in {_RETRY_WAIT:.0f}s...")
                time.sleep(_RETRY_WAIT)
                continue
            print(f"  ERROR: {e}", file=sys.stderr)
            return False
        finally:
            session.close()
    return False


def _is_valid_json_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_bytes())
        return isinstance(data, dict) and "item" in data
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Download IR BANK JSON files")
    parser.add_argument("--years", type=int, default=10, help="Number of fiscal years (default: 10)")
    parser.add_argument("--dest", type=str, default=None, help="Destination directory (default: data/irbank)")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between downloads (default: 3.0)")
    parser.add_argument("--force", action="store_true", help="Re-download existing files")
    parser.add_argument("--no-tor", action="store_true", help="Disable Tor proxy (direct connection)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    dest = Path(args.dest) if args.dest else project_root / "data" / "irbank"

    tor_port = None if args.no_tor else _detect_tor_port()
    if tor_port:
        print(f"Tor detected on port {tor_port} — IP rotation enabled")
    else:
        if not args.no_tor:
            print("WARNING: Tor not detected. Running without IP rotation (may be rate-limited).", file=sys.stderr)
            print("  Install Tor: https://www.torproject.org/", file=sys.stderr)

    codes = _year_codes(args.years)
    total = len(codes) * len(_JSON_FILES)
    ok = 0
    skip = 0
    fail = 0

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

            if not args.force and _is_valid_json_file(target):
                print(f"[{count}/{total}] SKIP (exists) {target.name}")
                skip += 1
                continue

            print(f"[{count}/{total}] {url}")
            if _download_file(url, target, tor_port=tor_port):
                ok += 1
            else:
                fail += 1

            if count < total:
                time.sleep(args.interval)

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed.")
    if fail > 0 and tor_port is None:
        print("Tip: Install Tor to enable IP rotation and avoid rate limits.", file=sys.stderr)
    if ok + skip > 0:
        print(f"Import with:\n  uv run python -m formula_screening import-data --dir {dest} --all")


if __name__ == "__main__":
    main()
