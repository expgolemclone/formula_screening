"""Client for the Node.js puppeteer-real-browser service.

Manages the lifecycle of the browser service subprocess and provides
a Python API for fetching pages through real browser instances.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import requests

from formula_screening.config import MAGIC

logger: logging.Logger = logging.getLogger("formula_screening.browser")

_BROWSER_SERVICE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "browser_service"
_NODE_EXECUTABLE: str = os.environ.get("NODE_PATH", "node")
_STARTUP_POLL_INTERVAL: float = 0.25


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

        browser_cfg: dict[str, int] = MAGIC["browser"]
        env: dict[str, str] = {
            **os.environ,
            "BROWSER_POOL_SIZE": str(browser_cfg["pool_size"]),
            "BROWSER_PAGE_TIMEOUT": str(browser_cfg["page_timeout"]),
            "BROWSER_IDLE_TIMEOUT": str(browser_cfg["idle_timeout"]),
        }

        self._process = subprocess.Popen(
            [_NODE_EXECUTABLE, str(_BROWSER_SERVICE_DIR / "server.js")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        startup_timeout: int = browser_cfg["startup_timeout"]
        deadline: float = time.monotonic() + startup_timeout

        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                stderr_output: str = self._process.stderr.read() if self._process.stderr else ""
                raise BrowserServiceError(
                    f"Browser service exited with code {self._process.returncode}: {stderr_output}"
                )

            line: str = self._process.stdout.readline().strip() if self._process.stdout else ""
            if line.startswith("BROWSER_SERVICE_PORT="):
                self._port = int(line.split("=", 1)[1])
                self._base_url = f"http://127.0.0.1:{self._port}"
                logger.info("Browser service started on port %d", self._port)
                return

            time.sleep(_STARTUP_POLL_INTERVAL)

        self._kill()
        raise BrowserServiceError(
            f"Browser service did not start within {startup_timeout}s"
        )

    def fetch(
        self,
        url: str,
        *,
        proxy: str,
        timeout: int = MAGIC["browser"]["page_timeout"],
    ) -> BrowserResponse:
        """Fetch a URL through the browser service.

        Args:
            url: The page URL to navigate to.
            proxy: Proxy address in ``host:port`` format.
            timeout: Page navigation timeout in milliseconds.

        Returns:
            A ``BrowserResponse`` with rendered HTML, HTTP status, and error.
        """
        if not self.running:
            raise BrowserServiceError("Browser service is not running")

        # Strip http:// prefix if present (ProxyPool.get() returns "http://host:port")
        proxy_addr: str = proxy.removeprefix("http://").removeprefix("https://")

        try:
            resp: requests.Response = requests.post(
                f"{self._base_url}/fetch",
                json={"url": url, "proxy": proxy_addr, "timeout": timeout},
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
