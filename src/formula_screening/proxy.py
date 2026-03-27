"""Shared proxy management: fetch, validate, and rotate HTTP proxies."""

from __future__ import annotations

import concurrent.futures
import functools
import random

import requests

print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output

_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]
_PROXY_CHECK_URL = "https://httpbin.org/ip"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)


def _fetch_proxy_candidates() -> list[str]:
    """Fetch raw proxy lists from public sources."""
    proxies: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": _DEFAULT_USER_AGENT})

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


def _check_proxy(addr: str, *, timeout: int = 2) -> str | None:
    """Return *addr* if the proxy responds, else ``None``."""
    proxy_url = f"http://{addr}"
    try:
        resp = requests.get(
            _PROXY_CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return addr
    except requests.RequestException:
        pass
    return None


def fetch_live_proxies(
    *,
    target_count: int = 100,
    check_workers: int = 200,
    check_timeout: int = 2,
) -> list[str]:
    """Fetch proxy lists, validate in parallel, return working proxies.

    Args:
        target_count: Stop checking once this many live proxies are found.
        check_workers: Number of parallel validation threads.
        check_timeout: Per-proxy HTTP timeout in seconds.

    Returns:
        List of ``host:port`` strings for live proxies (shuffled).
    """
    candidates = _fetch_proxy_candidates()
    print(f"  {len(candidates)} proxy candidates, checking liveness...")

    alive: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=check_workers) as pool:
        futures = {
            pool.submit(_check_proxy, addr, timeout=check_timeout): addr
            for addr in candidates
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                alive.append(result)
                if len(alive) % 10 == 0:
                    print(f"  ... {len(alive)} alive so far")
                if len(alive) >= target_count:
                    for f in futures:
                        f.cancel()
                    break

    random.shuffle(alive)
    print(f"  {len(alive)} live proxies ready")
    return alive
