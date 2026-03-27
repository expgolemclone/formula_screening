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
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
]

# --- User-Agent rotation pool ---------------------------------------------------

_USER_AGENTS = [
    # Chrome 134 — Windows / macOS / Linux
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    # Chrome 133 — Windows / macOS
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    # Firefox 135 — Windows / macOS / Linux
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
    # Safari 18.3
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.3 Safari/605.1.15"
    ),
    # Edge 134
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
    ),
    # Chrome 134 — Android
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36"
    ),
    # Safari — iPhone
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1"
    ),
]

# --- Anonymity check endpoints (header-echo services) ---------------------------

_ANON_CHECK_URLS = [
    "https://httpbin.io/headers",
    "https://httpbin.org/headers",
]

# --- Quality check URLs (moderately strict sites) --------------------------------

_CHECK_URLS = [
    # Financial
    "https://finance.yahoo.com/quote/AAPL/",
    "https://www.bloomberg.com/",
    "https://www.reuters.com/",
    "https://www.marketwatch.com/",
    "https://www.investing.com/",
    "https://finance.yahoo.co.jp/",
    "https://www.nikkei.com/",
    "https://www.wsj.com/",
    # Tech
    "https://www.google.com/",
    "https://www.amazon.com/",
    "https://www.microsoft.com/",
    "https://www.apple.com/",
    "https://github.com/",
    "https://www.cloudflare.com/",
    # Media
    "https://www.bbc.com/",
    "https://edition.cnn.com/",
    "https://www.nytimes.com/",
    "https://www.theguardian.com/",
    "https://www.forbes.com/",
    # Japanese
    "https://www.yahoo.co.jp/",
    "https://www.rakuten.co.jp/",
    "https://www.amazon.co.jp/",
    "https://zozo.jp/",
    "https://www.dmm.com/",
    # E-commerce / other
    "https://www.ebay.com/",
    "https://www.walmart.com/",
    "https://www.target.com/",
    "https://www.netflix.com/",
    "https://www.spotify.com/",
    "https://www.twitch.tv/",
]


def random_ua() -> str:
    """Return a randomly chosen browser User-Agent string."""
    return random.choice(_USER_AGENTS)


def _fetch_proxy_candidates() -> list[str]:
    """Fetch raw proxy lists from public sources."""
    proxies: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": random_ua()})

    for url in _PROXY_SOURCES:
        try:
            resp = session.get(url, timeout=10)
            for line in resp.text.strip().splitlines():
                addr = line.strip()
                if not addr or addr.startswith("<"):
                    continue
                # Strip protocol prefix (e.g. "http://1.2.3.4:8080" → "1.2.3.4:8080")
                for prefix in ("http://", "https://"):
                    if addr.startswith(prefix):
                        addr = addr[len(prefix):]
                        break
                if ":" in addr:
                    proxies.append(addr)
        except requests.RequestException:
            continue

    random.shuffle(proxies)
    return proxies


def _check_proxy(addr: str, *, timeout: int = 5) -> str | None:
    """Return *addr* if the proxy is elite-anonymous and can reach a tough site.

    Two-phase validation:
    1. Anonymity — header-echo service must NOT reveal X-Forwarded-For / Via.
    2. Quality   — random tough site must return HTTP 200.
    """
    proxy_url = f"http://{addr}"
    ua = random_ua()
    proxies = {"http": proxy_url, "https": proxy_url}
    headers = {"User-Agent": ua}

    # Phase 1: anonymity check
    anon_url = random.choice(_ANON_CHECK_URLS)
    try:
        resp = requests.get(
            anon_url, proxies=proxies, headers=headers, timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        echoed = resp.json().get("headers", {})
        if echoed.get("X-Forwarded-For") or echoed.get("Via"):
            return None
    except (requests.RequestException, ValueError):
        return None

    # Phase 2: quality check against a random tough site
    check_url = random.choice(_CHECK_URLS)
    try:
        resp = requests.get(
            check_url, proxies=proxies, headers=headers, timeout=timeout,
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
    """Fetch proxy lists, validate anonymity + quality, return working proxies.

    Args:
        target_count: Stop checking once this many live proxies are found.
        check_workers: Number of parallel validation threads.
        check_timeout: Per-proxy HTTP timeout in seconds.

    Returns:
        List of ``host:port`` strings for elite-anonymous live proxies (shuffled).
    """
    candidates = _fetch_proxy_candidates()
    print(f"  {len(candidates)} proxy candidates, validating (anonymity + quality)...")

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
                    print(f"  ... {len(alive)} elite proxies so far")
                if len(alive) >= target_count:
                    for f in futures:
                        f.cancel()
                    break

    random.shuffle(alive)
    print(f"  {len(alive)} elite-anonymous proxies ready")
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
