"""Tests for BrowserService helpers and challenge-aware navigation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

import formula_screening.browser as browser_module
from formula_screening.browser import BrowserService, BrowserServiceError, _build_proxy_fields

_CHALLENGE_REDIRECT_MS = 1200


class _BrowserTestHandler(BaseHTTPRequestHandler):
    server: "_BrowserTestHTTPServer"

    def do_GET(self) -> None:
        if self.path == "/challenge-fetch":
            self._write_html(self._challenge_page("/resolved-fetch"))
            return
        if self.path == "/resolved-fetch":
            self._write_html(
                """
                <html>
                  <head><title>Resolved Fetch</title></head>
                  <body><h1>Fetch Ready</h1><p>resolved fetch body</p></body>
                </html>
                """
            )
            return
        if self.path == "/challenge-stuck":
            self._write_html(self._challenge_page(None))
            return
        if self.path == "/challenge-download":
            self._write_html(self._challenge_page("/download-page"))
            return
        if self.path == "/download-page":
            self._write_html(
                """
                <html>
                  <head><title>Download Ready</title></head>
                  <body>
                    <a id="download-link" href="/files/selector.txt" download>Download</a>
                  </body>
                </html>
                """
            )
            return
        if self.path == "/files/selector.txt":
            self._write_attachment("selector download body\n", "selector.txt")
            return
        if self.path == "/files/direct.txt":
            self._write_attachment("direct download body\n", "direct.txt")
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _challenge_page(self, redirect_path: str | None) -> str:
        redirect_script: str = ""
        if redirect_path is not None:
            redirect_script = (
                "<script>"
                f"window.setTimeout(() => window.location.replace('{redirect_path}'), {_CHALLENGE_REDIRECT_MS});"
                "</script>"
            )
        return (
            "<html>"
            "<head><title>Just a moment...</title></head>"
            "<body>"
            "<h1>Verification</h1>"
            "<p>Performing security verification</p>"
            f"{redirect_script}"
            "</body>"
            "</html>"
        )

    def _write_attachment(self, body: str, filename: str) -> None:
        payload: bytes = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_html(self, html: str) -> None:
        payload: bytes = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _BrowserTestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


@dataclass(frozen=True, slots=True)
class _LocalHTTPServer:
    base_url: str
    server: _BrowserTestHTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def local_http_server() -> _LocalHTTPServer:
    server = _BrowserTestHTTPServer(("127.0.0.1", 0), _BrowserTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local_server = _LocalHTTPServer(
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        server=server,
        thread=thread,
    )
    try:
        yield local_server
    finally:
        local_server.close()


@pytest.fixture(scope="module")
def browser_service() -> BrowserService:
    browser_cfg = cast(dict[str, int | bool], browser_module.MAGIC["browser"])
    original_cfg: dict[str, int | bool] = dict(browser_cfg)
    browser_cfg["page_timeout"] = 8_000
    browser_cfg["challenge_poll_interval_ms"] = 100
    browser_cfg["challenge_clear_stable_ms"] = 300

    service = BrowserService()
    try:
        service.start()
    except BrowserServiceError as exc:
        browser_cfg.clear()
        browser_cfg.update(original_cfg)
        pytest.skip(f"BrowserService unavailable: {exc}")

    try:
        yield service
    finally:
        service.shutdown()
        browser_cfg.clear()
        browser_cfg.update(original_cfg)


class TestBuildProxyFields:

    def test_none_returns_empty_dict(self) -> None:
        # Arrange
        proxy: str | None = None

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result == {}

    def test_plain_http_proxy(self) -> None:
        # Arrange
        proxy = "http://1.2.3.4:8080"

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result == {"proxy": "1.2.3.4:8080"}

    def test_http_proxy_with_auth(self) -> None:
        # Arrange
        proxy = "http://alice:secret@1.2.3.4:8080"

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result == {
            "proxy": "1.2.3.4:8080",
            "proxyUsername": "alice",
            "proxyPassword": "secret",
        }

    def test_socks5_proxy(self) -> None:
        # Arrange
        proxy = "socks5h://1.2.3.4:1080"

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result == {"proxy": "1.2.3.4:1080", "proxyType": "socks5"}

    def test_socks5_proxy_with_auth(self) -> None:
        # Arrange
        proxy = "socks5://bob:pw@1.2.3.4:1080"

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result == {
            "proxy": "1.2.3.4:1080",
            "proxyType": "socks5",
            "proxyUsername": "bob",
            "proxyPassword": "pw",
        }

    def test_auth_with_special_chars_in_password(self) -> None:
        # Arrange
        proxy = "http://u:p%40ss@host:9000"

        # Act
        result = _build_proxy_fields(proxy)

        # Assert
        assert result["proxyUsername"] == "u"
        assert result["proxyPassword"] == "p@ss"
        assert result["proxy"] == "host:9000"


class TestBrowserServiceIntegration:

    def test_fetch_waits_for_challenge_redirect_before_returning(
        self,
        browser_service: BrowserService,
        local_http_server: _LocalHTTPServer,
    ) -> None:
        # Arrange
        url = f"{local_http_server.base_url}/challenge-fetch"

        # Act
        response = browser_service.fetch(url, timeout=8_000)

        # Assert
        assert response.error is None
        assert response.status == 200
        assert response.html is not None
        assert "Resolved Fetch" in response.html
        assert "resolved fetch body" in response.html
        assert "Just a moment..." not in response.html

    def test_fetch_returns_error_when_challenge_never_clears(
        self,
        browser_service: BrowserService,
        local_http_server: _LocalHTTPServer,
    ) -> None:
        # Arrange
        url = f"{local_http_server.base_url}/challenge-stuck"

        # Act
        response = browser_service.fetch(url, timeout=2_500)

        # Assert
        assert response.status == 502
        assert response.html is None
        assert response.error is not None
        assert "challenge" in response.error.lower()

    def test_download_waits_for_challenge_page_before_clicking_selector(
        self,
        browser_service: BrowserService,
        local_http_server: _LocalHTTPServer,
        tmp_path: Path,
    ) -> None:
        # Arrange
        download_dir = tmp_path / "selector-download"
        url = f"{local_http_server.base_url}/challenge-download"

        # Act
        file_path = browser_service.download(
            url,
            str(download_dir),
            selector="#download-link",
            timeout=8_000,
        )

        # Assert
        downloaded_file = Path(file_path)
        assert downloaded_file.exists()
        assert downloaded_file.read_text(encoding="utf-8") == "selector download body\n"

    def test_download_keeps_direct_download_path_working(
        self,
        browser_service: BrowserService,
        local_http_server: _LocalHTTPServer,
        tmp_path: Path,
    ) -> None:
        # Arrange
        download_dir = tmp_path / "direct-download"
        url = f"{local_http_server.base_url}/files/direct.txt"

        # Act
        file_path = browser_service.download(url, str(download_dir), timeout=8_000)

        # Assert
        downloaded_file = Path(file_path)
        assert downloaded_file.exists()
        assert downloaded_file.read_text(encoding="utf-8") == "direct download body\n"
