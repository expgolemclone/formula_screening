"""Anti-detection HTTP infrastructure: proxy rotation, TLS fingerprint
mimicry, User-Agent rotation, and request throttling."""

from __future__ import annotations

import concurrent.futures
import functools
import random
import re
import threading
import time
from typing import Any

import requests

from formula_screening.config import MAGIC

_HOST_PORT_RE = re.compile(
    r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$",
)

print = functools.partial(print, flush=True)  # noqa: A001 — unbuffered output

_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
]

# --- Browser profiles (TLS fingerprint + UA + headers, always consistent) ------
#
# Each entry is a (impersonate, user_agent, extra_headers) tuple.
# impersonate controls the TLS handshake (JA3/JA4); the UA and headers
# MUST match the same browser to avoid trivial detection.

_CHROMIUM_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_SAFARI_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,ja-JP;q=0.8,ja;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}


def _chromium_headers(brand: str, version: str, platform: str) -> dict[str, str]:
    """Build Chromium headers with Client Hints matching the given identity."""
    return {
        **_CHROMIUM_BASE_HEADERS,
        "Sec-CH-UA": f'"{brand}";v="{version}", "Chromium";v="{version}", "Not-A.Brand";v="99"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": f'"{platform}"',
    }


_BROWSER_PROFILES: list[tuple[str, str, dict[str, str]]] = [
    # Chrome 124 — Windows / macOS / Linux
    (
        "chrome124",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        _chromium_headers("Google Chrome", "124", "Windows"),
    ),
    (
        "chrome124",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        _chromium_headers("Google Chrome", "124", "macOS"),
    ),
    (
        "chrome124",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        _chromium_headers("Google Chrome", "124", "Linux"),
    ),
    # Chrome 120 — Windows / macOS
    (
        "chrome120",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        _chromium_headers("Google Chrome", "120", "Windows"),
    ),
    (
        "chrome120",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        _chromium_headers("Google Chrome", "120", "macOS"),
    ),
    # Edge 101 — Windows
    (
        "edge101",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/101.0.4951.64 Safari/537.36 Edg/101.0.1210.47",
        _chromium_headers("Microsoft Edge", "101", "Windows"),
    ),
    # Safari 17.0 — macOS (no Client Hints — Safari does not send them)
    (
        "safari17_0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        _SAFARI_HEADERS,
    ),
    # Safari 15.5 — macOS
    (
        "safari15_5",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/15.5 Safari/605.1.15",
        _SAFARI_HEADERS,
    ),
]

# --- Anonymity check endpoints (header-echo services) ---------------------------

_ANON_CHECK_URLS = [
    "https://httpbin.io/headers",
    "https://httpbin.org/headers",
]

_PROXY_LEAK_HEADERS = (
    "X-Forwarded-For",
    "Via",
    "X-Real-IP",
    "Forwarded",
    "X-Proxy-ID",
)

# --- Quality check URLs (moderately strict sites) --------------------------------

_CHECK_URLS = [
    # Financial (low block-rate)
    "https://finance.yahoo.com/quote/AAPL/",
    "https://www.investing.com/",
    "https://finance.yahoo.co.jp/",
    # Tech
    "https://www.google.com/",
    "https://www.amazon.com/",
    "https://www.microsoft.com/",
    "https://www.apple.com/",
    "https://github.com/",
    # Media (low block-rate)
    "https://www.bbc.com/",
    "https://www.theguardian.com/",
    # Japanese
    "https://www.yahoo.co.jp/",
    "https://www.rakuten.co.jp/",
    "https://www.amazon.co.jp/",
    # E-commerce / other
    "https://www.ebay.com/",
    "https://www.walmart.com/",
    "https://www.spotify.com/",
]


def random_ua() -> str:
    """Return a randomly chosen browser User-Agent string."""
    return random.choice(_BROWSER_PROFILES)[1]


def create_session(
    pool: ProxyPool | None = None,
) -> Any:
    """Create a ``curl_cffi`` session with consistent browser identity.

    When *pool* is provided the browser profile is pinned to the
    current proxy so that the same IP always presents the same
    TLS fingerprint / UA / headers.  Without a pool a random
    profile is selected.

    Works with any HTTP target — yfinance, IR BANK, etc.
    """
    from curl_cffi import requests as cffi_requests

    if pool is not None:
        impersonate, ua, extra_headers = pool.profile
    else:
        impersonate, ua, extra_headers = random.choice(_BROWSER_PROFILES)

    session = cffi_requests.Session(impersonate=impersonate)
    session.headers["User-Agent"] = ua
    session.headers.update(extra_headers)

    if pool is not None:
        proxy_url = pool.get()
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}

    return session


def random_delay(min_s: float = 1.0, max_s: float = 5.0) -> None:
    """Sleep for a random duration to break request-timing correlation."""
    time.sleep(random.uniform(min_s, max_s))


# --- Proxy fetching & validation ----------------------------------------------

def _fetch_proxy_candidates() -> list[str]:
    """Fetch raw proxy lists from public sources."""
    proxies: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": random_ua()})

    for url in _PROXY_SOURCES:
        try:
            resp = session.get(url, timeout=MAGIC["proxy"]["anon_timeout"])
            for line in resp.text.strip().splitlines():
                addr = line.strip()
                if not addr or addr.startswith("<"):
                    continue
                # Strip protocol prefix (e.g. "http://1.2.3.4:8080" → "1.2.3.4:8080")
                for prefix in ("http://", "https://"):
                    if addr.startswith(prefix):
                        addr = addr[len(prefix):]
                        break
                if _HOST_PORT_RE.match(addr):
                    proxies.append(addr)
        except requests.RequestException:
            continue

    random.shuffle(proxies)
    return proxies


def _check_proxy(
    addr: str,
    *,
    timeout: int = MAGIC["proxy"]["check_timeout"],
    anon_timeout: int = MAGIC["proxy"]["anon_timeout"],
) -> str | None:
    """Return *addr* if the proxy is elite-anonymous and can reach a tough site.

    Two-phase validation:
    1. Anonymity — header-echo service must NOT reveal X-Forwarded-For / Via.
    2. Quality   — random tough site must return HTTP 200.
    """
    proxy_url = f"http://{addr}"
    ua = random_ua()
    proxies = {"http": proxy_url, "https": proxy_url}
    headers = {"User-Agent": ua}

    # Phase 1: anonymity check (try each endpoint until one succeeds)
    anon_passed = False
    for anon_url in random.sample(_ANON_CHECK_URLS, len(_ANON_CHECK_URLS)):
        try:
            resp = requests.get(
                anon_url, proxies=proxies, headers=headers, timeout=anon_timeout,
            )
            if resp.status_code != 200:
                continue
            echoed = resp.json().get("headers", {})
            if any(echoed.get(h) for h in _PROXY_LEAK_HEADERS):
                return None
            anon_passed = True
            break
        except (requests.RequestException, ValueError):
            continue
    if not anon_passed:
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
    target_count: int = MAGIC["proxy"]["target_count"],
    check_workers: int = MAGIC["proxy"]["check_workers"],
    batch_size: int = MAGIC["proxy"]["batch_size"],
) -> list[str]:
    """Fetch proxy lists, validate anonymity + quality, return working proxies.

    Candidates are processed in batches so that once *target_count* elite
    proxies are found the remaining batches are skipped entirely — avoiding
    thousands of wasted timeout waits.

    Returns:
        List of ``host:port`` strings for elite-anonymous live proxies (shuffled).
    """
    candidates = _fetch_proxy_candidates()
    print(f"  {len(candidates)} proxy candidates, validating (anonymity + quality)...")

    alive: list[str] = []
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start : batch_start + batch_size]
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=check_workers)
        futures = {
            executor.submit(_check_proxy, addr): addr
            for addr in batch
        }
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is not None:
                    alive.append(result)
                    if len(alive) % 10 == 0:
                        print(f"  ... {len(alive)} elite proxies so far")
                    if len(alive) >= target_count:
                        break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if len(alive) >= target_count:
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
        self._lock = threading.Lock()
        self._proxies = list(proxies)
        self._index = 0
        self._failures: dict[str, int] = {}
        self._max_failures = MAGIC["proxy"]["max_failures"]
        self._profile_idx = random.randrange(len(_BROWSER_PROFILES))

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
        with self._lock:
            if not self._proxies:
                return None
            return f"http://{self._proxies[self._index % len(self._proxies)]}"

    @property
    def profile(self) -> tuple[str, str, dict[str, str]]:
        """Return the browser profile pinned to the current proxy."""
        with self._lock:
            return _BROWSER_PROFILES[self._profile_idx % len(_BROWSER_PROFILES)]

    def _rotate_locked(self) -> None:
        """Advance to the next proxy (caller must hold ``_lock``)."""
        if self._proxies:
            self._index += 1
            self._profile_idx = random.randrange(len(_BROWSER_PROFILES))
            proxy_url = f"http://{self._proxies[self._index % len(self._proxies)]}"
            print(f"  Rotated to proxy: {proxy_url}")

    def rotate(self) -> None:
        """Move to the next proxy and browser profile in the pool."""
        with self._lock:
            self._rotate_locked()

    def report_failure(self) -> None:
        """Record a failure for the current proxy; rotate if too many."""
        with self._lock:
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
                self._rotate_locked()

    @property
    def exhausted(self) -> bool:
        """True if all proxies have been removed due to failures."""
        with self._lock:
            return len(self._proxies) == 0

    def split(self, n: int) -> list[ProxyPool]:
        """Split the proxy list into *n* sub-pools (round-robin distribution).

        Each sub-pool gets its own browser profile.  If there are fewer
        proxies than *n*, some sub-pools will be empty (direct connection).
        """
        if n <= 0:
            raise ValueError("n must be positive")
        with self._lock:
            buckets: list[list[str]] = [[] for _ in range(n)]
            for i, addr in enumerate(self._proxies):
                buckets[i % n].append(addr)
        return [ProxyPool(b) for b in buckets]
