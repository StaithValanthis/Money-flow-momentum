# Monitoring and Alerting

## Health Snapshot

- **HealthSnapshot**: Tracks per-loop status. **Runtime loops** in the bot call `report_ok(name)` / `report_fail(name, message)` so that heartbeat reflects real activity.
- **to_dict()**: Machine-readable snapshot with `ts`, `loops` (name → status, message, last_ok_ts, consecutive_failures), and optional `meta` (e.g. config_id, strategy).
- Loops that report: public_ws, private_ws, context_refresh, reconciliation, lifecycle, score_entry, degradation_monitor.

## Heartbeat

- **Written by the bot**: The main score/entry loop calls `write_heartbeat(health, Path("artifacts/heartbeat.json"))` about every 30 seconds. Before that, each loop (context refresh, WS monitor, reconciliation, lifecycle, degradation check, score_entry) calls `report_ok(...)` so the file contains real loop timestamps.
- **read_heartbeat(path)**: Returns last snapshot dict or None.
- **CLI `health`**: Reads the heartbeat; reports per-loop age; marks loops **stale** when last_ok_ts is older than `--stale-sec` (default 300); exits 1 if heartbeat missing or any loop stale/fail. Use `--heartbeat path` to override file location.
- **CLI `status`**: Shows heartbeat file age and per-loop freshness when the file exists; otherwise reports "No heartbeat file (runtime state unknown)".
- **CLI `report`**: Includes loop health / stale summary from heartbeat; if no file, states "No heartbeat file; runtime loop state unknown."

## Alert Router

- **AlertRouter(alert_file_path, webhook_url, enabled)**:
  - Every `send(severity, title, message, payload)` logs at WARNING and optionally appends to a file and/or POSTs to a webhook.
- Keep alerting **optional** and **config-driven**; no external services required for tests.
- Typical use: instantiate with config (e.g. `alerts_file: artifacts/alerts.jsonl`, `webhook_url: null` unless provided).

## What to Alert On

- Repeated WS reconnects / stale feeds
- Protection repair failures
- Kill-switch activation
- Degradation breaches
- Promotion/rollback events
- Artifact generation failures

Implementors can call `AlertRouter.send()` from the appropriate places (e.g. degradation monitor, promotion, repair failure).
