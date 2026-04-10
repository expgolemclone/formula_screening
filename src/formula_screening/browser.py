"""Client for the Node.js puppeteer-real-browser service.

Manages the lifecycle of the browser service subprocess and provides
a Python API for fetching pages through real browser instances.
"""

from __future__ import annotations

import logging
import os
import pty
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self, TypedDict, cast
from urllib.parse import unquote, urlsplit

import requests

from formula_screening.config import MAGIC

logger: logging.Logger = logging.getLogger("formula_screening.browser")

_BROWSER_SERVICE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "browser_service"
_NODE_EXECUTABLE: str = os.environ.get("NODE_PATH", "node")
_STARTUP_POLL_INTERVAL: float = 0.25


class ProxyFields(TypedDict, total=False):
    proxy: str
    proxyType: str
    proxyUsername: str
    proxyPassword: str


class BrowserConfig(TypedDict):
    pool_size: int
    page_timeout: int
    idle_timeout: int
    startup_timeout: int
    headless: bool
    disable_xvfb: bool
    challenge_poll_interval_ms: int
    challenge_clear_stable_ms: int


def _browser_config() -> BrowserConfig:
    return cast(BrowserConfig, MAGIC["browser"])


def _build_proxy_fields(proxy: str | None) -> ProxyFields:
    """Parse a proxy URL into the request body fields the browser service expects.

    Chromium's ``--proxy-server`` flag does not accept ``user:pass@host:port``,
    so auth must be forwarded out-of-band and applied via ``page.authenticate``.
    """
    if proxy is None:
        return {}
    parsed = urlsplit(proxy)
    fields: ProxyFields = {"proxy": f"{parsed.hostname}:{parsed.port}"}
    if parsed.scheme.startswith("socks5"):
        fields["proxyType"] = "socks5"
    if parsed.username is not None:
        fields["proxyUsername"] = unquote(parsed.username)
        fields["proxyPassword"] = unquote(parsed.password or "")
    return fields


@dataclass(frozen=True, slots=True)
class BrowserResponse:
    html: str | None
    status: int
    error: str | None


class BrowserServiceError(RuntimeError):
    """Raised when the browser service is unreachable or returns an error."""


class BrowserService:
    """Manages a Node.js browser service subprocess and proxies fetch requests."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._port: int | None = None
        self._base_url: str = ""
        self._pty_master_fd: int | None = None

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Launch the Node.js server and wait until it is ready."""
        if self.running:
            return

        browser_cfg: BrowserConfig = _browser_config()
        env: dict[str, str] = {
            **os.environ,
            "BROWSER_POOL_SIZE": str(browser_cfg["pool_size"]),
            "BROWSER_PAGE_TIMEOUT": str(browser_cfg["page_timeout"]),
            "BROWSER_IDLE_TIMEOUT": str(browser_cfg["idle_timeout"]),
            "BROWSER_HEADLESS": str(browser_cfg["headless"]).lower(),
            "BROWSER_DISABLE_XVFB": str(browser_cfg["disable_xvfb"]).lower(),
            "BROWSER_CHALLENGE_POLL_INTERVAL_MS": str(browser_cfg["challenge_poll_interval_ms"]),
            "BROWSER_CHALLENGE_CLEAR_STABLE_MS": str(browser_cfg["challenge_clear_stable_ms"]),
        }

        master_fd, slave_fd = pty.openpty()
        self._pty_master_fd = master_fd
        self._process = subprocess.Popen(
            [_NODE_EXECUTABLE, str(_BROWSER_SERVICE_DIR / "server.js")],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=True,
            env=env,
        )
        os.close(slave_fd)

        startup_timeout: int = browser_cfg["startup_timeout"]

        line_queue: queue.Queue[str] = queue.Queue()
        output_lines: list[str] = []

        def _reader() -> None:
            if self._pty_master_fd is None:
                return
            with os.fdopen(self._pty_master_fd, "r", encoding="utf-8", errors="replace") as stream:
                while True:
                    try:
                        raw_line = stream.readline()
                    except OSError:
                        return
                    if raw_line == "":
                        return
                    line = raw_line.strip()
                    output_lines.append(line)
                    line_queue.put(line)

        reader_thread: threading.Thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        deadline: float = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                stderr_output: str = "\n".join(output_lines)
                raise BrowserServiceError(
                    f"Browser service exited with code {self._process.returncode}: {stderr_output}"
                )

            try:
                line: str = line_queue.get(timeout=_STARTUP_POLL_INTERVAL)
            except queue.Empty:
                continue

            if line.startswith("BROWSER_SERVICE_PORT="):
                self._port = int(line.split("=", 1)[1])
                self._base_url = f"http://127.0.0.1:{self._port}"
                logger.info("Browser service started on port %d", self._port)
                return

        self._kill()
        raise BrowserServiceError(
            f"Browser service did not start within {startup_timeout}s"
        )

    def fetch(
        self,
        url: str,
        *,
        proxy: str | None = None,
        timeout: int = MAGIC["browser"]["page_timeout"],
    ) -> BrowserResponse:
        """Fetch a URL through the browser service.

        Args:
            url: The page URL to navigate to.
            proxy: Proxy address in ``host:port`` format.  Omit for direct connection.
            timeout: Page navigation timeout in milliseconds.

        Returns:
            A ``BrowserResponse`` with rendered HTML, HTTP status, and error.
        """
        if not self.running:
            raise BrowserServiceError("Browser service is not running")

        fetch_body: dict[str, str | int | None] = {
            "url": url, "timeout": timeout,
            **_build_proxy_fields(proxy),
        }

        try:
            resp: requests.Response = requests.post(
                f"{self._base_url}/fetch",
                json=fetch_body,
                timeout=timeout / 1000 + 10,
            )
            data: dict[str, str | int | None] = resp.json()
            return BrowserResponse(
                html=str(data.get("html")) if data.get("html") is not None else None,
                status=int(data.get("status", resp.status_code)),
                error=str(data["error"]) if data.get("error") is not None else None,
            )
        except requests.RequestException as exc:
            return BrowserResponse(html=None, status=502, error=str(exc))

    def download(
        self,
        url: str,
        download_dir: str,
        *,
        selector: str | None = None,
        proxy: str | None = None,
        timeout: int = MAGIC["browser"]["page_timeout"],
    ) -> str:
        """Download a file by navigating to *url* via the browser service.

        Args:
            url: The page URL that triggers a file download.
            download_dir: Local directory to save the downloaded file.
            selector: Optional CSS selector to click to start the download.
            proxy: Proxy address (``host:port``).  Omit for direct connection.
            timeout: Navigation/download timeout in milliseconds.

        Returns:
            The absolute path to the downloaded file.
        """
        if not self.running:
            raise BrowserServiceError("Browser service is not running")

        body: dict[str, str | int | None] = {
            "url": url,
            "downloadDir": download_dir,
            "timeout": timeout,
            **_build_proxy_fields(proxy),
        }
        if selector is not None:
            body["selector"] = selector

        try:
            resp: requests.Response = requests.post(
                f"{self._base_url}/download",
                json=body,
                timeout=timeout / 1000 + 10,
            )
            data: dict[str, str | int | None] = resp.json()
            if resp.status_code != 200 or data.get("error"):
                raise BrowserServiceError(
                    f"Download failed: {data.get('error', resp.status_code)}"
                )
            return str(data["filePath"])
        except requests.RequestException as exc:
            raise BrowserServiceError(f"Download request failed: {exc}") from exc

    def shutdown(self) -> None:
        """Gracefully shut down the browser service."""
        if not self.running:
            return

        try:
            requests.post(f"{self._base_url}/shutdown", timeout=5)
        except requests.RequestException:
            pass

        self._kill()
        logger.info("Browser service stopped")

    def _kill(self) -> None:
        """Force-kill the subprocess if still alive."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._process.kill()
            self._process = None
            self._port = None
            self._base_url = ""
        if self._pty_master_fd is not None:
            try:
                os.close(self._pty_master_fd)
            except OSError:
                pass
            self._pty_master_fd = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.shutdown()
