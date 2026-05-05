from __future__ import annotations

from collections.abc import Mapping

from stock_web_ui.config import ServerConfig

import formula_screening.web as web_mod


def test_serve_screening_passes_handbook_dir_to_stock_web_ui(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_serve(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(web_mod, "_serve", fake_serve)

    server_config = ServerConfig(host="127.0.0.1", port=8080)
    web_mod.serve_screening([], server_config=server_config)

    api_routes = captured["api_routes"]

    assert captured["static_root"] == web_mod._STATIC_ROOT
    assert isinstance(api_routes, Mapping)
    assert set(api_routes) == {"/api/screening"}
    assert captured["server_config"] == server_config
    assert captured["yazi_base_dir"] == web_mod._HANDBOOK_DATA_DIR
