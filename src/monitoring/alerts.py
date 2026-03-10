"""Alert routing: log, file, optional webhook."""

import json
import time
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


class AlertRouter:
    """Route alerts to log, file, and optional webhook."""

    def __init__(
        self,
        alert_file_path: Optional[Path] = None,
        webhook_url: Optional[str] = None,
        enabled: bool = True,
    ):
        self.alert_file_path = Path(alert_file_path) if alert_file_path else None
        self.webhook_url = webhook_url
        self.enabled = enabled

    def send(
        self,
        severity: str,
        title: str,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        payload = payload or {}
        rec = {
            "ts": int(time.time() * 1000),
            "severity": severity,
            "title": title,
            "message": message,
            **payload,
        }
        log.warning(f"ALERT [{severity}] {title}: {message}")
        if self.alert_file_path:
            try:
                self.alert_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.alert_file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
            except Exception as e:
                log.error(f"Alert file write failed: {e}")
        if self.webhook_url:
            self._send_webhook(rec)

    def _send_webhook(self, payload: dict) -> None:
        try:
            import urllib.request
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status >= 400:
                    log.error(f"Webhook returned {resp.status}")
        except Exception as e:
            log.debug(f"Webhook send failed: {e}")
