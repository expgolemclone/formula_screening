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
_PROXY_CHECK_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
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


def _check_proxy(addr: str, *, timeout: int = 5) -> str | None:
    """Return *addr* if the proxy can reach Yahoo Finance, else ``None``."""
    proxy_url = f"http://{addr}"
    try:
        resp = requests.get(
            _PROXY_CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            headers={"User-Agent": _DEFAULT_USER_AGENT},
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
    check_timeout: int = 5,
) -> list[str]:
    """Fetch proxy lists, validate against Yahoo Finance, return working proxies.

    Args:
        target_count: Stop checking once this many live proxies are found.
        check_workers: Number of parallel validation threads.
        check_timeout: Per-proxy HTTP timeout in seconds.

    Returns:
        List of ``host:port`` strings for live proxies (shuffled).
    """
    candidates = _fetch_proxy_candidates()
    print(f"  {len(candidates)} proxy candidates, checking against Yahoo Finance...")

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


class ProxyPool:
    """Rotating proxy pool with automatic failover.

    Usage::

        pool = ProxyPool.from_auto()
        proxy_url = pool.get()       # "http://host:port" or None
        pool.report_failure()        # mark current proxy bad, auto-rotate
        proxy_url = pool.get()       # next proxy
    """

    def __init__(self, proxies: list[str]) -> None:
        self._proxies = list(proxies)
        self._index = 0
        self._failures: dict[str, int] = {}
        self._max_failures = 2

    @classmethod
    def from_auto(cls) -> ProxyPool:
        """Create a pool by auto-fetching public proxies."""
        print("Fetching and validating proxies...")
        proxies = fetch_live_proxies()
        if not proxies:
            print("WARNING: No live proxies found. Using direct connection.")
        return cls(proxies)

    @classmethod
    def from_url(cls, url: str) -> ProxyPool:
        """Create a pool with a single user-specified proxy."""
        addr = url.removeprefix("http://").removeprefix("https://")
        return cls([addr])

    @classmethod
    def direct(cls) -> ProxyPool:
        """Create an empty pool (direct connection)."""
        return cls([])

    def get(self) -> str | None:
        """Return the current proxy URL, or None for direct connection."""
        if not self._proxies:
            return None
        return f"http://{self._proxies[self._index % len(self._proxies)]}"

    def rotate(self) -> None:
        """Move to the next proxy in the pool."""
        if self._proxies:
            self._index += 1
            proxy = self.get()
            print(f"  Rotated to proxy: {proxy}")

    def report_failure(self) -> None:
        """Record a failure for the current proxy; rotate if too many."""
        if not self._proxies:
            return
        addr = self._proxies[self._index % len(self._proxies)]
        self._failures[addr] = self._failures.get(addr, 0) + 1
        if self._failures[addr] >= self._max_failures:
            print(f"  Proxy {addr} failed {self._max_failures} times, removing")
            self._proxies = [p for p in self._proxies if p != addr]
            if self._proxies:
                self._index = self._index % len(self._proxies)
        else:
            self.rotate()

    @property
    def exhausted(self) -> bool:
        """True if all proxies have been removed due to failures."""
        return len(self._proxies) == 0
