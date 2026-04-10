#!/usr/bin/env python3
"""Download IR BANK JSON files via HTTP with proxy rotation.

Usage:
    uv run python scripts/download_irbank.py [--years N] [--dest DIR]

Fetches proxy lists, validates them in parallel, then downloads
IR BANK JSON files through working proxies. Falls back to direct
connection if needed. Already-downloaded files are skipped.

After download completes, import into DB with:
    uv run python -m formula_screening import-data --dir data/irbank --all
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import truststore

# Ensure the project package is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from formula_screening.config import CLI_DEFAULTS, IRBANK_DIR, MAGIC
from formula_screening.stealth import fetch_live_proxies

truststore.inject_into_ssl()

_BASE_URL = "https://f.irbank.net/files"
_FY_FILES = [
    "fy-profit-and-loss.json",
    "fy-balance-sheet.json",
    "fy-cash-flow-statement.json",
    "fy-stock-dividend.json",
]
_QY_FILES = [
    "qy-net-sales.json",
    "qy-operating-income.json",
    "qy-ordinary-income.json",
    "qy-profit-loss.json",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://irbank.net/download",
}
_MAX_PROXY_TRIES = MAGIC["scrape"]["max_proxy_tries"]
_RATE_LIMIT_WAIT = MAGIC["scrape"]["rate_limit_wait"]


def _year_codes(years: int) -> list[str]:
    latest = datetime.now(timezone.utc).year
    return [f"{y % 100:04d}" for y in range(latest - years + 1, latest + 1)]


def _is_rate_limited(resp: requests.Response) -> bool:
    content_type = resp.headers.get("Content-Type", "")
    return "html" in content_type or resp.content.lstrip()[:1] == b"<"


def _try_download(url: str, proxy_addr: str | None, *, timeout: float = 15) -> bytes | None:
    """Single download attempt. Returns content bytes or None."""
    kwargs: dict = {"headers": _HEADERS, "timeout": timeout}
    if proxy_addr:
        proxy_url = f"http://{proxy_addr}"
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    resp = requests.get(url, **kwargs)
    if resp.status_code != 200 or _is_rate_limited(resp):
        return None
    json.loads(resp.content)  # validate
    return resp.content


def _download_file(
    url: str,
    dest: Path,
    proxies: list[str],
    *,
    timeout: float = 15,
) -> bool:
    """Download with proxy rotation. On rate-limit, switch IP and wait 30s."""
    for _tried in range(_MAX_PROXY_TRIES):
        addr: str | None = random.choice(proxies) if proxies else None
        label: str = addr or "direct"
        try:
            content: bytes | None = _try_download(url, addr, timeout=timeout)
            if content is not None:
                dest.write_bytes(content)
                print(f"  OK via {label}", flush=True)
                return True
            print(f"  Rate-limited ({label}), switching IP + waiting {_RATE_LIMIT_WAIT:.0f}s...", flush=True)
            time.sleep(_RATE_LIMIT_WAIT)
        except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError):
            continue

    print("  FAILED", file=sys.stderr, flush=True)
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
    _dl = CLI_DEFAULTS["download_irbank"]
    parser.add_argument("--years", type=int, default=_dl["years"], help=f"Number of fiscal years (default: {_dl['years']})")
    parser.add_argument("--dest", type=str, default=None, help=f"Destination directory (default: {IRBANK_DIR})")
    parser.add_argument("--interval", type=float, default=_dl["interval"], help=f"Seconds between downloads (default: {_dl['interval']})")
    parser.add_argument("--force", action="store_true", help="Re-download existing files")
    args = parser.parse_args()

    dest = Path(args.dest) if args.dest else IRBANK_DIR

    print("Fetching and validating proxies...", flush=True)
    proxies: list[str] = fetch_live_proxies()
    if not proxies:
        print("ABORT: No live proxies found.", file=sys.stderr, flush=True)
        sys.exit(1)

    codes = _year_codes(args.years)

    # Build download jobs: (url, target_path) pairs
    jobs: list[tuple[str, Path]] = []
    for code in codes:
        out_dir = dest / code
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename in _FY_FILES:
            jobs.append((f"{_BASE_URL}/{code}/{filename}", out_dir / filename))

    # Quarterly (cumulative) data from the "0000" (all-years) endpoint
    qy_dir = dest / "quarterly"
    qy_dir.mkdir(parents=True, exist_ok=True)
    for filename in _QY_FILES:
        jobs.append((f"{_BASE_URL}/0000/{filename}", qy_dir / filename))

    ok: int = 0
    fail: int = 0

    if args.force:
        download_jobs = jobs
        skip: int = 0
    else:
        download_jobs = [(url, t) for url, t in jobs if not _is_valid_json_file(t)]
        skip = len(jobs) - len(download_jobs)

    total: int = len(download_jobs)

    print(f"Downloading {total} files for years: {', '.join(codes)} + quarterly", flush=True)
    if skip > 0:
        print(f"Skipping {skip} files (already downloaded)", flush=True)
    print(f"Destination: {dest}", flush=True)

    for count, (url, target) in enumerate(download_jobs, 1):
        if len(proxies) < _MAX_PROXY_TRIES:
            print("  Refreshing proxies...", flush=True)
            proxies = fetch_live_proxies()

        print(f"[{count}/{total}] {url}", flush=True)
        if _download_file(url, target, proxies):
            ok += 1
        else:
            fail += 1

        if count < total:
            time.sleep(args.interval)

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed.", flush=True)
    if fail > 0:
        print("Re-run to retry failed files (already downloaded files are skipped).", file=sys.stderr, flush=True)
    if ok + skip > 0:
        print(f"Import with:\n  uv run python -m formula_screening import-irbank --dir {dest}", flush=True)
    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
