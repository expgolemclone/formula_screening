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
import concurrent.futures
import functools
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()
print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output

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
_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]
_MAX_PROXY_TRIES = 10
_RATE_LIMIT_WAIT = 30.0
_PROXY_CHECK_URL = "https://httpbin.org/ip"
_PROXY_CHECK_WORKERS = 200
_PROXY_CHECK_TIMEOUT = 2
_PROXY_TARGET_COUNT = 100  # stop checking once we have enough


def _year_codes(years: int) -> list[str]:
    latest = datetime.now(timezone.utc).year - 1
    return [f"{y % 100:04d}" for y in range(latest - years + 1, latest + 1)]


def _fetch_proxy_candidates() -> list[str]:
    """Fetch raw proxy lists from public sources."""
    proxies: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": _HEADERS["User-Agent"]})

    for url in _PROXY_SOURCES:
        try:
            resp = session.get(url, timeout=10)
            for line in resp.text.strip().splitlines():
                addr = line.strip()
                if addr and ":" in addr and not addr.startswith("<"):
                    proxies.append(addr)
        except requests.RequestException:
            continue

    random.shuffle(proxies)
    return proxies


def _check_proxy(addr: str) -> str | None:
    """Return addr if proxy is alive, else None."""
    proxy_url = f"http://{addr}"
    try:
        resp = requests.get(
            _PROXY_CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=_PROXY_CHECK_TIMEOUT,
        )
        if resp.status_code == 200:
            return addr
    except requests.RequestException:
        pass
    return None


def _fetch_live_proxies() -> list[str]:
    """Fetch proxy lists, then validate in parallel. Returns only working proxies."""
    candidates = _fetch_proxy_candidates()
    print(f"  {len(candidates)} candidates, checking liveness...")

    alive: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_PROXY_CHECK_WORKERS) as pool:
        futures = {pool.submit(_check_proxy, addr): addr for addr in candidates}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                alive.append(result)
                if len(alive) % 10 == 0:
                    print(f"  ... {len(alive)} alive so far")
                if len(alive) >= _PROXY_TARGET_COUNT:
                    # Cancel remaining checks
                    for f in futures:
                        f.cancel()
                    break

    random.shuffle(alive)
    print(f"  {len(alive)} live proxies ready")
    return alive


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
    tried = 0
    while tried < _MAX_PROXY_TRIES:
        addr = proxies.pop() if proxies else None
        label = addr or "direct"
        tried += 1
        try:
            content = _try_download(url, addr, timeout=timeout)
            if content is not None:
                dest.write_bytes(content)
                print(f"  OK via {label}")
                return True
            # Rate-limited: switch proxy and wait
            print(f"  Rate-limited ({label}), switching IP + waiting {_RATE_LIMIT_WAIT:.0f}s...")
            time.sleep(_RATE_LIMIT_WAIT)
        except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError):
            continue

    print("  FAILED", file=sys.stderr)
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
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    dest = Path(args.dest) if args.dest else project_root / "data" / "irbank"

    print("Fetching and validating proxies...")
    proxies = _fetch_live_proxies()
    if not proxies:
        print("WARNING: No live proxies found. Using direct connection.", file=sys.stderr)

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
                print(f"[{count}/{total}] SKIP {target.name}")
                skip += 1
                continue

            # Refresh proxy list if running low
            if len(proxies) < _MAX_PROXY_TRIES:
                print("  Refreshing proxies...")
                proxies = _fetch_live_proxies()

            print(f"[{count}/{total}] {url}")
            if _download_file(url, target, proxies):
                ok += 1
            else:
                fail += 1

            if count < total:
                time.sleep(args.interval)

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed.")
    if fail > 0:
        print("Re-run to retry failed files (already downloaded files are skipped).", file=sys.stderr)
    if ok + skip > 0:
        print(f"Import with:\n  uv run python -m formula_screening import-data --dir {dest} --all")
    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
