"""
Pybit schedules a threading.Timer to send application-level pings. After the socket is
closed (e.g. intentional shutdown), that timer can still fire and call ws.send(), producing
uncaught WebSocketConnectionClosedException tracebacks in background threads.

This module wraps pybit's _send_custom_ping so those expected post-close sends are ignored.
Does not change connection or reconnect behavior; only prevents noisy timer-thread crashes.
"""

from __future__ import annotations

_installed = False


def install_pybit_ws_ping_guard() -> None:
    """Idempotent: patch pybit _WebSocketManager._send_custom_ping once per process."""
    global _installed
    if _installed:
        return
    try:
        from pybit._websocket_stream import _WebSocketManager
    except Exception:
        return

    _orig = _WebSocketManager._send_custom_ping

    def _safe_send_custom_ping(self) -> None:
        try:
            _orig(self)
        except Exception as e:
            name = type(e).__name__
            if name == "WebSocketConnectionClosedException":
                return
            if name in ("BrokenPipeError", "ConnectionResetError", "OSError"):
                return
            msg = str(e).lower()
            if "already closed" in msg or "connection is already closed" in msg:
                return
            raise

    _WebSocketManager._send_custom_ping = _safe_send_custom_ping  # type: ignore[assignment]
    _installed = True
