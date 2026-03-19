"""Shutdown / logging cleanup: pybit ping-after-close, Loguru placeholder style."""

from pathlib import Path
from unittest.mock import MagicMock

import websocket


def test_pybit_ws_ping_guard_swallows_connection_closed_on_send() -> None:
    from pybit._websocket_stream import _WebSocketManager

    from src.exchange.pybit_ws_ping_guard import install_pybit_ws_ping_guard

    install_pybit_ws_ping_guard()
    mgr = MagicMock()
    mgr.ws = MagicMock()
    mgr.ws.send.side_effect = websocket.WebSocketConnectionClosedException(
        "Connection is already closed."
    )
    mgr.custom_ping_message = "{}"
    _WebSocketManager._send_custom_ping(mgr)


def test_pybit_ws_ping_guard_idempotent() -> None:
    from src.exchange import pybit_ws_ping_guard as mod

    mod.install_pybit_ws_ping_guard()
    mod.install_pybit_ws_ping_guard()


def test_warm_start_skip_override_log_uses_loguru_placeholders_not_percent_s() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "warm_start" / "runner.py").read_text(encoding="utf-8")
    start = text.index("Warm-start skip overridden")
    snippet = text[start : start + 400]
    assert "%s" not in snippet
    assert "trade_count={}" in snippet or "source={}" in snippet
