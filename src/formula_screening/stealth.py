"""Anti-detection infrastructure: proxy rotation, validation, and request throttling.

Page fetching is delegated to the Node.js browser service
(puppeteer-real-browser) via ``formula_screening.browser.BrowserService``.
This module retains proxy pool management and the proxy validation pipeline.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import random
import re
import socket
import threading
import time
from pathlib import Path
from collections import Counter

import requests

logger: logging.Logger = logging.getLogger("formula_screening.stealth")

from formula_screening.config import (
    MAGIC,
    NOT_A_PROXY_LIST,
    PROXY_FAILURE_CACHE,
    PROXY_SOURCES_FILE,
    VALIDATION_SITES_FILE,
)

_HOST_PORT_RE = re.compile(
    r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$",
)

# UA strings used only for proxy validation requests (not for scraping)
_VALIDATION_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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

_FAILURE_TTL_HOURS: dict[str, float] = {
    "legacy": 1.0,
    "tcp_unreachable": 1.0,
    "anon_unreachable": 1.0,
    "quality_failed": 1.0,
    "anon_leak": float(MAGIC["proxy"]["failure_cache_ttl_hours"]),
}

_LAST_PROXY_FAILURE_SUMMARY: str = "no diagnostics recorded"


from stock_db.stealth import ProxyPool as _BaseProxyPool
from stock_db.stealth import ProxyUnavailableError  # noqa: F401
from stock_db.stealth import random_delay  # noqa: F401 — re-export; overrides local def below

# --- Quality check sites (loaded from config/validation_sites.txt) ---------------


def _load_validation_sites() -> list[str]:
    """Load proxy validation domains from the sites list file."""
    text: str = VALIDATION_SITES_FILE.read_text()
    return [line.strip() for line in text.splitlines() if line.strip()]


_VALIDATION_SITES: list[str] = _load_validation_sites()


_SOCKS5_TAG: str = "socks5 "


def _load_proxy_sources() -> list[tuple[str, str]]:
    """Load proxy source URLs from the sources list file.

    Lines prefixed with ``socks5 `` yield SOCKS5 candidates; all others are HTTP.
    """
    text: str = PROXY_SOURCES_FILE.read_text()
    result: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line: str = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_SOCKS5_TAG):
            result.append((line[len(_SOCKS5_TAG):].strip(), "socks5"))
        else:
            result.append((line, "http"))
    return result


_PROXY_SOURCES: list[tuple[str, str]] = _load_proxy_sources()


def random_ua() -> str:
    """Return a randomly chosen User-Agent for proxy validation requests."""
    return random.choice(_VALIDATION_USER_AGENTS)



# random_delay is re-exported from stock_db.stealth (see import above)


# --- Proxy fetching & validation ----------------------------------------------

def _fetch_single_source(url: str) -> list[str]:
    """Fetch a single proxy source and return parsed host:port addresses."""
    proxies: list[str] = []
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": random_ua()},
            timeout=MAGIC["proxy"]["anon_timeout"],
        )
        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/json" in content_type:
            payload = resp.json()
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        ip = item.get("ip")
                        port = item.get("port")
                        if isinstance(ip, str) and isinstance(port, str):
                            addr = f"{ip}:{port}"
                            if _HOST_PORT_RE.match(addr):
                                proxies.append(addr)
            return proxies
        for line in resp.text.strip().splitlines():
            addr = line.strip()
            if not addr or addr.startswith("<"):
                continue
            for prefix in ("http://", "https://"):
                if addr.startswith(prefix):
                    addr = addr[len(prefix):]
                    break
            if _HOST_PORT_RE.match(addr):
                proxies.append(addr)
    except requests.RequestException:
        logger.warning("Failed to fetch proxy source: %s", url, exc_info=True)
    return proxies


def _source_label(url: str) -> str:
    """Extract a short label from a proxy source URL."""
    parts: list[str] = url.split("/")
    # api.proxyscrape.com → proxyscrape_api
    if "api.proxyscrape.com" in url:
        return "proxyscrape_api"
    # proxy-list.download API → proxy_list_download
    if "proxy-list.download" in url:
        return "proxy_list_download"
    # proxylist.geonode.com API → geonode_api
    if "proxylist.geonode.com" in url:
        return "geonode_api"
    # databay.com API → databay_api
    if "databay.com" in url:
        return "databay_api"
    # raw.githubusercontent.com/{user}/... → user
    try:
        return parts[parts.index("raw.githubusercontent.com") + 1]
    except (ValueError, IndexError):
        pass
    # cdn.jsdelivr.net/gh/{user}/... → user
    try:
        gh_idx: int = parts.index("gh")
        if "cdn.jsdelivr.net" in parts:
            return parts[gh_idx + 1]
    except (ValueError, IndexError):
        pass
    # {user}.github.io/... → user
    for part in parts:
        if part.endswith(".github.io"):
            return part.removesuffix(".github.io")
    return url


def _fetch_proxy_candidates() -> tuple[list[str], dict[str, int], dict[str, str], dict[str, str]]:
    """Fetch raw proxy lists from all sources in parallel, deduplicated.

    Returns:
        Tuple of (shuffled proxy list, per-source counts, first source by addr,
        protocol by addr).
    """
    t0: float = time.monotonic()
    seen: set[str] = set()
    per_source: dict[str, int] = {}
    source_by_addr: dict[str, str] = {}
    proto_by_addr: dict[str, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_PROXY_SOURCES)) as executor:
        future_to_meta: dict[concurrent.futures.Future[list[str]], tuple[str, str]] = {
            executor.submit(_fetch_single_source, url): (_source_label(url), proto)
            for url, proto in _PROXY_SOURCES
        }
        for future in concurrent.futures.as_completed(future_to_meta):
            label: str
            proto: str
            label, proto = future_to_meta[future]
            result: list[str] = future.result()
            per_source[label] = len(result)
            before: int = len(seen)
            for addr in result:
                if addr not in seen:
                    seen.add(addr)
                    source_by_addr[addr] = label
                    proto_by_addr[addr] = proto
            logger.debug("Source %s: %d proxies (%d new)", label, len(result), len(seen) - before)

    elapsed: float = time.monotonic() - t0
    source_summary: str = ", ".join(f"{k}: {v}" for k, v in sorted(per_source.items()))
    logger.info("Fetched %d unique candidates from %d sources in %.1fs [%s]",
                len(seen), len(_PROXY_SOURCES), elapsed, source_summary)

    proxies: list[str] = list(seen)
    random.shuffle(proxies)
    return proxies, per_source, source_by_addr, proto_by_addr


def _classify_request_exception(exc: requests.RequestException) -> str:
    """Map request exceptions to cacheable proxy failure reasons."""
    if isinstance(exc, requests.exceptions.ProxyError):
        msg = str(exc).lower()
        if (
            "tunnel connection failed" in msg
            or "unable to connect to proxy" in msg
            or "wrong version number" in msg
            or ("proxy" in msg and "bad request" in msg)
        ):
            return "not_a_proxy"
    return "anon_unreachable"


def _request_via_proxy(
    url: str,
    proxies: dict[str, str],
    headers: dict[str, str],
    timeout: int,
) -> tuple[requests.Response | None, str | None]:
    """Issue a single proxied GET request, returning either a response or a reason."""
    try:
        return requests.get(url, proxies=proxies, headers=headers, timeout=timeout), None
    except requests.RequestException as exc:
        return None, _classify_request_exception(exc)


def _hit_anon_detailed(
    url: str,
    proxies: dict[str, str],
    headers: dict[str, str],
    timeout: int,
) -> str:
    """Return a detailed status for a single anonymity endpoint."""
    resp, error_reason = _request_via_proxy(url, proxies, headers, timeout)
    if error_reason is not None:
        return error_reason
    if resp is None or resp.status_code != 200:
        return "anon_unreachable"
    try:
        payload = resp.json()
    except ValueError:
        return "anon_unreachable"
    if "headers" not in payload:
        return "anon_unreachable"
    echoed: dict[str, str] = payload["headers"]
    if any(echoed.get(h) for h in _PROXY_LEAK_HEADERS):
        return "anon_leak"
    return "ok"


def _hit_anon(
    url: str,
    proxies: dict[str, str],
    headers: dict[str, str],
    timeout: int,
) -> bool | None:
    """Check a single header-echo endpoint for proxy anonymity.

    Returns:
        True  — endpoint responded and no leak headers detected (anonymous).
        False — endpoint responded but leak headers found (not anonymous).
        None  — endpoint unreachable or returned non-200.
    """
    result: str = _hit_anon_detailed(url, proxies, headers, timeout)
    if result == "ok":
        return True
    if result == "anon_leak":
        return False
    return None


def _hit_quality_detailed(
    domain: str,
    proxies: dict[str, str],
    headers: dict[str, str],
    timeout: int,
) -> str:
    """Return a detailed status for a single quality-check domain."""
    resp, error_reason = _request_via_proxy(
        f"https://{domain}/",
        proxies,
        headers,
        timeout,
    )
    if error_reason is not None:
        return error_reason
    if resp is None or resp.status_code != 200:
        return "quality_failed"
    return "ok"


def _hit_quality(
    domain: str,
    proxies: dict[str, str],
    headers: dict[str, str],
    timeout: int,
) -> bool:
    """Return True if the proxy can reach *domain* with HTTP 200."""
    return _hit_quality_detailed(domain, proxies, headers, timeout) == "ok"


def _proxy_url(addr: str, proto: str) -> str:
    """Construct a proxy URL from an address and protocol tag."""
    if proto == "socks5":
        return f"socks5h://{addr}"
    return f"http://{addr}"


def _prefilter_proxy(
    addr: str,
    *,
    proto: str = "http",
    tcp_timeout: float = MAGIC["proxy"]["tcp_timeout"],
    anon_timeout: int = MAGIC["proxy"]["anon_timeout"],
) -> str:
    """Fast proxy pre-filter using TCP reachability plus anonymous proxy checks."""
    if not _tcp_reachable(addr, timeout=tcp_timeout):
        return "tcp_unreachable"
    return _check_proxy(addr, proto=proto, anon_timeout=anon_timeout, quality_check_count=0)


def _check_proxy(
    addr: str,
    *,
    proto: str = "http",
    timeout: int = MAGIC["proxy"]["check_timeout"],
    anon_timeout: int = MAGIC["proxy"]["anon_timeout"],
    quality_check_count: int = MAGIC["proxy"]["quality_check_count"],
) -> str:
    """Return a detailed status for a proxy candidate.

    All checks (anonymity + quality) run concurrently in a single executor.
    Anonymity results are evaluated first; on failure everything is cancelled
    immediately so no time is wasted on quality checks for a leaky proxy.
    """
    proxy_url: str = _proxy_url(addr, proto)
    ua: str = random_ua()
    proxies: dict[str, str] = {"http": proxy_url, "https": proxy_url}
    headers: dict[str, str] = {"User-Agent": ua}
    check_domains: list[str] = random.sample(_VALIDATION_SITES, quality_check_count)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(_ANON_CHECK_URLS) + quality_check_count,
    ) as ex:
        anon_futures: set[concurrent.futures.Future[str]] = {
            ex.submit(_hit_anon_detailed, url, proxies, headers, anon_timeout)
            for url in _ANON_CHECK_URLS
        }
        quality_futures: set[concurrent.futures.Future[str]] = {
            ex.submit(_hit_quality_detailed, d, proxies, headers, timeout)
            for d in check_domains
        }

        # Evaluate anonymity first — one pass is enough, one leak is fatal
        anon_passed: bool = False
        anon_failure: str = "anon_unreachable"
        for future in concurrent.futures.as_completed(anon_futures):
            result: str = future.result()
            if result == "ok":
                anon_passed = True
                break
            if result == "anon_leak":
                logger.debug("FAIL %s (anon leak detected)", addr)
                ex.shutdown(wait=False, cancel_futures=True)
                return "anon_leak"
            if result == "not_a_proxy":
                anon_failure = "not_a_proxy"

        if not anon_passed:
            logger.debug("FAIL %s (%s)", addr, anon_failure)
            ex.shutdown(wait=False, cancel_futures=True)
            return anon_failure

        # All quality sites must return 200
        for future in concurrent.futures.as_completed(quality_futures):
            result = future.result()
            if result != "ok":
                logger.debug("FAIL %s (%s)", addr, result)
                ex.shutdown(wait=False, cancel_futures=True)
                return result

    logger.debug("PASS %s (anon ok, %d/%d quality sites)", addr, quality_check_count, quality_check_count)
    return "ok"


def _tcp_reachable(addr: str, timeout: float = MAGIC["proxy"]["tcp_timeout"]) -> bool:
    """Quick TCP connect test — True if the port is open."""
    try:
        host: str
        port_str: str
        host, port_str = addr.rsplit(":", 1)
        with socket.create_connection((host, int(port_str)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _format_reason_counts(counts: Counter[str]) -> str:
    """Render reason counters in a stable, human-readable order."""
    if not counts:
        return "none"
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{reason}={count}" for reason, count in items)


def _validation_failure_rate(validation_failures: Counter[str], checked: int) -> float:
    """Return the failure rate for fully validated proxy candidates."""
    if checked <= 0:
        return 0.0
    return sum(validation_failures.values()) / checked


def _build_proxy_failure_summary(
    *,
    alive_count: int,
    checked: int,
    cache_skipped: int,
    cache_skip_reasons: Counter[str],
    prefilter_failures: Counter[str],
    validation_failures: Counter[str],
    validation_failure_rate: float | None = None,
    validation_failure_threshold: float | None = None,
    min_validation_checks: int | None = None,
) -> str:
    """Build a stable diagnostic summary for proxy acquisition."""
    summary_parts: list[str] = [f"{alive_count}/{checked} passed"]
    if (
        validation_failure_rate is not None
        and validation_failure_threshold is not None
        and min_validation_checks is not None
    ):
        summary_parts.append(
            "validation_fail_rate="
            f"{validation_failure_rate * 100:.1f}% "
            f"(>{validation_failure_threshold * 100:.1f}%, min_checked={min_validation_checks})",
        )
    if cache_skip_reasons:
        summary_parts.append(
            f"cache_skipped={cache_skipped} [{_format_reason_counts(cache_skip_reasons)}]",
        )
    if prefilter_failures:
        summary_parts.append(f"prefilter [{_format_reason_counts(prefilter_failures)}]")
    if validation_failures:
        summary_parts.append(f"validation [{_format_reason_counts(validation_failures)}]")
    return "; ".join(summary_parts)


def _failure_ttl_seconds(reason: str) -> float:
    """Return the TTL for a cached failure reason."""
    return _FAILURE_TTL_HOURS[reason] * 3600


def _normalize_failure_cache_entry(value: object) -> dict[str, float | str] | None:
    """Convert legacy and current cache entries into a normalized form."""
    if isinstance(value, (int, float)):
        return {"reason": "legacy", "ts": float(value)}
    if not isinstance(value, dict):
        return None
    ts = value.get("ts")
    reason = value.get("reason")
    if not isinstance(ts, (int, float)) or not isinstance(reason, str):
        return None
    return {"reason": reason, "ts": float(ts)}


def _make_failure_cache_entry(reason: str, ts: float | None = None) -> dict[str, float | str]:
    """Build a failure-cache entry."""
    return {"reason": reason, "ts": float(time.time() if ts is None else ts)}


def _load_failure_cache() -> dict[str, dict[str, float | str]]:
    """Load the failure cache, discarding entries after their reason-specific TTL."""
    if not PROXY_FAILURE_CACHE.exists():
        return {}
    try:
        raw = json.loads(PROXY_FAILURE_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt or unreadable failure cache: %s", PROXY_FAILURE_CACHE, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    now: float = time.time()
    valid: dict[str, dict[str, float | str]] = {}
    loaded_by_reason: Counter[str] = Counter()
    expired_by_reason: Counter[str] = Counter()
    for addr, value in raw.items():
        if not isinstance(addr, str):
            continue
        entry = _normalize_failure_cache_entry(value)
        if entry is None:
            continue
        reason = str(entry["reason"])
        ts = float(entry["ts"])
        if now - ts < _failure_ttl_seconds(reason):
            valid[addr] = {"reason": reason, "ts": ts}
            loaded_by_reason[reason] += 1
        else:
            expired_by_reason[reason] += 1
    logger.info(
        "Failure cache: %d entries loaded, %d expired (%s)",
        len(valid),
        sum(expired_by_reason.values()),
        _format_reason_counts(loaded_by_reason),
    )
    return valid


def _save_failure_cache(cache: dict[str, dict[str, float | str]]) -> None:
    """Persist the failure cache to disk."""
    PROXY_FAILURE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PROXY_FAILURE_CACHE.write_text(json.dumps(cache, sort_keys=True))


def _load_not_a_proxy_set() -> set[str]:
    """Load the git-tracked not-a-proxy blacklist."""
    if not NOT_A_PROXY_LIST.exists():
        return set()
    text: str = NOT_A_PROXY_LIST.read_text()
    return {line.strip() for line in text.splitlines() if line.strip()}


def _save_not_a_proxy_set(addrs: set[str]) -> None:
    """Persist the not-a-proxy blacklist sorted for stable git diffs."""
    NOT_A_PROXY_LIST.parent.mkdir(parents=True, exist_ok=True)
    NOT_A_PROXY_LIST.write_text("\n".join(sorted(addrs)) + "\n")


def failure_cache_reason_counts() -> Counter[str]:
    """Return the active failure-cache distribution by reason, including the not-a-proxy list."""
    cache: dict[str, dict[str, float | str]] = _load_failure_cache()
    counts: Counter[str] = Counter()
    for entry in cache.values():
        counts[str(entry["reason"])] += 1
    not_a_proxy_count: int = len(_load_not_a_proxy_set())
    if not_a_proxy_count > 0:
        counts["not_a_proxy"] = not_a_proxy_count
    return counts


def failure_cache_reasons() -> list[str]:
    """Return the known failure reasons accepted by cache-management CLI."""
    return sorted({*_FAILURE_TTL_HOURS, "not_a_proxy"})


def clear_failure_cache(*, reasons: set[str] | None = None) -> tuple[int, int]:
    """Delete cached proxy failures, optionally filtered by reason.

    Returns:
        Tuple of (removed_count, remaining_count).
    """
    cache: dict[str, dict[str, float | str]] = _load_failure_cache()
    not_a_proxy_set: set[str] = _load_not_a_proxy_set()

    if reasons is None:
        removed: int = len(cache) + len(not_a_proxy_set)
        _save_failure_cache({})
        _save_not_a_proxy_set(set())
        return removed, 0

    kept: dict[str, dict[str, float | str]] = {}
    removed = 0
    for addr, entry in cache.items():
        reason = str(entry["reason"])
        if reason in reasons:
            removed += 1
        else:
            kept[addr] = entry
    if "not_a_proxy" in reasons:
        removed += len(not_a_proxy_set)
        _save_not_a_proxy_set(set())
    _save_failure_cache(kept)
    remaining: int = len(kept) + (len(not_a_proxy_set) if "not_a_proxy" not in reasons else 0)
    return removed, remaining


def fetch_live_proxies(
    *,
    target_count: int = MAGIC["proxy"]["target_count"],
    check_workers: int = MAGIC["proxy"]["check_workers"],
    quality_check_count: int = MAGIC["proxy"]["quality_check_count"],
) -> list[tuple[str, str]]:
    """Fetch proxy lists, validate anonymity + quality, return working proxies.

    Previously-failed proxies are skipped via a TTL-based on-disk cache.
    A proxy pre-filter first rejects dead ports and endpoints that cannot
    complete anonymous proxy requests. Surviving candidates are fed to a
    single executor in a producer-consumer style: at most *check_workers*
    futures are outstanding at any time, and each completed future is
    immediately replaced with the next candidate.

    Returns:
        List of ``(host:port, proto)`` tuples for elite-anonymous live proxies
        (shuffled).  *proto* is ``"http"`` or ``"socks5"``.

    Raises:
        ProxyUnavailableError: If proxy validation fails too often after enough checks.
    """
    global _LAST_PROXY_FAILURE_SUMMARY

    overall_t0: float = time.monotonic()
    _LAST_PROXY_FAILURE_SUMMARY = "no diagnostics recorded"
    failure_cache: dict[str, dict[str, float | str]] = _load_failure_cache()
    not_a_proxy_set: set[str] = _load_not_a_proxy_set()

    all_candidates: list[str]
    proto_by_addr: dict[str, str]
    all_candidates, per_source, source_by_addr, proto_by_addr = _fetch_proxy_candidates()
    source_stats: dict[str, Counter[str]] = {
        source: Counter({"fetched": count}) for source, count in per_source.items()
    }

    def bump_source(addr: str, key: str) -> None:
        source = source_by_addr[addr]
        source_stats.setdefault(source, Counter())
        source_stats[source][key] += 1

    cache_skip_reasons: Counter[str] = Counter()
    candidates: list[str] = []
    for addr in all_candidates:
        if addr in not_a_proxy_set:
            cache_skip_reasons["not_a_proxy"] += 1
            bump_source(addr, "cache_skipped")
            continue
        entry = failure_cache.get(addr)
        if entry is None:
            candidates.append(addr)
            continue
        reason = str(entry["reason"])
        cache_skip_reasons[reason] += 1
        bump_source(addr, "cache_skipped")

    cache_skipped: int = sum(cache_skip_reasons.values())
    logger.info(
        "%d candidates total, %d skipped (failure cache: %s)",
        len(all_candidates),
        cache_skipped,
        _format_reason_counts(cache_skip_reasons),
    )

    # Proxy pre-filter: require both an open port and a successful anonymous proxy probe.
    prefilter_t0: float = time.monotonic()
    prefilter_workers: int = MAGIC["proxy"]["tcp_workers"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=prefilter_workers) as prefilter_ex:
        prefilter_results: list[str] = list(prefilter_ex.map(
            lambda addr: _prefilter_proxy(addr, proto=proto_by_addr[addr]),
            candidates,
        ))
    prefilter_passed: list[str] = []
    prefilter_failures: Counter[str] = Counter()
    now: float = time.time()
    for addr, result in zip(candidates, prefilter_results):
        if result == "ok":
            prefilter_passed.append(addr)
            bump_source(addr, "prefilter_pass")
            continue
        prefilter_failures[result] += 1
        if result == "not_a_proxy":
            not_a_proxy_set.add(addr)
        else:
            failure_cache[addr] = _make_failure_cache_entry(result, now)
        bump_source(addr, f"prefilter_{result}")
    prefilter_elapsed: float = time.monotonic() - prefilter_t0
    logger.info(
        "Proxy pre-filter: %d/%d usable in %.1fs (workers=%d; reasons: %s)",
        len(prefilter_passed),
        len(candidates),
        prefilter_elapsed,
        prefilter_workers,
        _format_reason_counts(prefilter_failures),
    )
    candidates = prefilter_passed

    logger.info("Validating %d candidates (anonymity + %d quality sites, workers=%d)",
                len(candidates), quality_check_count, check_workers)

    alive: list[tuple[str, str]] = []
    checked: int = 0
    validation_failures: Counter[str] = Counter()
    max_validation_failure_rate: float = float(MAGIC["proxy"]["max_validation_failure_rate"])
    min_validation_checks_before_abort: int = int(MAGIC["proxy"]["min_validation_checks_before_abort"])
    validate_t0: float = time.monotonic()
    future_to_addr: dict[concurrent.futures.Future[str], str] = {}
    executor: concurrent.futures.ThreadPoolExecutor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=check_workers)
    )
    pending: set[concurrent.futures.Future[str]] = set()
    idx: int = 0

    while idx < len(candidates) and len(pending) < check_workers:
        f: concurrent.futures.Future[str] = executor.submit(
            _check_proxy, candidates[idx],
            proto=proto_by_addr[candidates[idx]],
            quality_check_count=quality_check_count,
        )
        future_to_addr[f] = candidates[idx]
        pending.add(f)
        idx += 1

    try:
        while pending:
            done, pending = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                result: str = future.result()
                addr: str = future_to_addr.pop(future)
                checked += 1
                if result == "ok":
                    alive.append((addr, proto_by_addr[addr]))
                    bump_source(addr, "validated_ok")
                    if len(alive) % 10 == 0:
                        elapsed: float = time.monotonic() - validate_t0
                        rate: float = checked / elapsed if elapsed > 0 else 0
                        logger.info("%d elite proxies found (%d checked, %.1f/s, %.1fs)",
                                    len(alive), checked, rate, elapsed)
                else:
                    validation_failures[result] += 1
                    if result == "not_a_proxy":
                        not_a_proxy_set.add(addr)
                    else:
                        failure_cache[addr] = _make_failure_cache_entry(result, now)
                    bump_source(addr, f"validated_{result}")

                failure_rate: float = _validation_failure_rate(validation_failures, checked)
                if (
                    checked >= min_validation_checks_before_abort
                    and failure_rate > max_validation_failure_rate
                ):
                    _LAST_PROXY_FAILURE_SUMMARY = _build_proxy_failure_summary(
                        alive_count=len(alive),
                        checked=checked,
                        cache_skipped=cache_skipped,
                        cache_skip_reasons=cache_skip_reasons,
                        prefilter_failures=prefilter_failures,
                        validation_failures=validation_failures,
                        validation_failure_rate=failure_rate,
                        validation_failure_threshold=max_validation_failure_rate,
                        min_validation_checks=min_validation_checks_before_abort,
                    )
                    _save_failure_cache(failure_cache)
                    _save_not_a_proxy_set(not_a_proxy_set)
                    logger.error("Aborting proxy validation: %s", _LAST_PROXY_FAILURE_SUMMARY)
                    raise ProxyUnavailableError(_LAST_PROXY_FAILURE_SUMMARY)

                if idx < len(candidates) and len(alive) < target_count:
                    f = executor.submit(
                        _check_proxy, candidates[idx],
                        proto=proto_by_addr[candidates[idx]],
                        quality_check_count=quality_check_count,
                    )
                    future_to_addr[f] = candidates[idx]
                    pending.add(f)
                    idx += 1

            if len(alive) >= target_count:
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    _save_failure_cache(failure_cache)
    _save_not_a_proxy_set(not_a_proxy_set)
    random.shuffle(alive)

    total_elapsed: float = time.monotonic() - overall_t0
    pass_rate: float = (len(alive) / checked * 100) if checked > 0 else 0
    logger.info("Validation failures: %s", _format_reason_counts(validation_failures))
    logger.info("%d elite-anonymous proxies ready (%d/%d passed, %.1f%%, %.1fs total)",
                len(alive), len(alive), checked, pass_rate, total_elapsed)
    for source, stats in sorted(source_stats.items()):
        fetched = stats.get("fetched", 0)
        if fetched == 0:
            continue
        ok = stats.get("validated_ok", 0)
        logger.info(
            "Source %s: fetched=%d cache_skipped=%d prefilter_pass=%d ok=%d",
            source,
            fetched,
            stats.get("cache_skipped", 0),
            stats.get("prefilter_pass", 0),
            ok,
        )
        if fetched >= 100 and ok == 0:
            logger.warning("Source %s yielded 0 live proxies out of %d fetched candidates", source, fetched)
    _LAST_PROXY_FAILURE_SUMMARY = _build_proxy_failure_summary(
        alive_count=len(alive),
        checked=checked,
        cache_skipped=cache_skipped,
        cache_skip_reasons=cache_skip_reasons,
        prefilter_failures=prefilter_failures,
        validation_failures=validation_failures,
    )
    return alive


class ProxyPool(_BaseProxyPool):
    """Extends stock_db.stealth.ProxyPool with auto-fetch from public sources."""

    def __init__(
        self,
        proxies: list[tuple[str, str]] | list[tuple[str, str, str]],
        *,
        direct: bool = False,
    ) -> None:
        super().__init__(
            proxies,
            direct=direct,
            max_failures=int(MAGIC["proxy"]["max_failures"]),
        )

    @classmethod
    def from_auto(
        cls,
        *,
        target_count: int = MAGIC["proxy"]["target_count"],
        quality_check_count: int = MAGIC["proxy"]["quality_check_count"],
    ) -> ProxyPool:
        logger.info("Fetching and validating proxies (target=%d, quality_sites=%d)...",
                    target_count, quality_check_count)
        proxies = fetch_live_proxies(target_count=target_count, quality_check_count=quality_check_count)
        if not proxies:
            raise ProxyUnavailableError(f"No live proxies found ({_LAST_PROXY_FAILURE_SUMMARY})")
        return cls(proxies)
