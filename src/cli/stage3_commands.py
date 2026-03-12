"""Stage 3 CLI: config, evaluate, optimize, shadow, promote, rollback."""

import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict

import typer

from src.automation.orchestrator import get_automation_status, run_demo_automation_cycle
from src.config.config import load_config
from src.config.versioning import (
    list_config_versions,
    get_config_version,
    activate_config_version,
    rollback_to_previous_config,
    diff_config_versions,
    import_candidate_to_live,
)
from src.evaluation.evaluator import Evaluator
from src.optimizer.search import run_optimization
from src.shadow.shadow_runner import ShadowRunner
from src.shadow.comparison import compare_baseline_shadow
from src.promotion.promoter import promote_candidate
from src.storage.db import Database
from src.cli.validate_env import validate_environment, ValidationResult
from src.storage.artifacts import pipeline_dir
from src.validation.readiness import (
    compute_readiness,
    READINESS_READY_TESTNET,
    READINESS_READY_SMALL_LIVE,
    ReadinessResult,
)


def _db_path(config_path: Optional[Path] = None) -> str:
    config, _ = load_config(config_path)
    return config.database_path


def _load_config_env(config_path: Optional[Path] = None) -> tuple[Any, Any]:
    """Load config and env for CLI commands that need artifact paths."""
    return load_config(config_path)


def _parse_date_range(from_date: Optional[str], to_date: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Parse YYYY-MM-DD date strings into epoch ms range (inclusive end-of-day for to_date)."""
    from_ts = None
    to_ts = None
    if from_date:
        from_ts = int(time.mktime(time.strptime(from_date, "%Y-%m-%d"))) * 1000
    if to_date:
        to_ts = int(time.mktime(time.strptime(to_date, "%Y-%m-%d"))) * 1000 + 86400 * 1000 - 1
    return from_ts, to_ts


def run_post_burnin_pipeline(
    *,
    config_path: Optional[Path] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    config_id: Optional[str] = None,
    n_samples: int = 20,
    window_hours: float = 24.0,
    start_shadow: bool = False,
    shadow_report: bool = False,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run a conservative, manual-first post-burn-in pipeline:
    1) Readiness
    2) Evaluation
    3) Optimization
    4) Candidate listing
    5) Optional shadow start/report.
    Returns a summary dict; no promotion or environment switch is performed.
    """
    summary: Dict[str, Any] = {}

    config, env = load_config(config_path)
    db_path = config.database_path
    from_ts, to_ts = _parse_date_range(from_date, to_date)

    # Resolve active / selected config id
    from src.config.versioning import get_active_config_id

    active_config_id = get_active_config_id(db_path)
    selected_config_id = config_id or active_config_id
    summary["timestamp_ms"] = int(time.time() * 1000)
    summary["config_id_active"] = active_config_id
    summary["config_id_selected"] = selected_config_id
    summary["from_date"] = from_date
    summary["to_date"] = to_date
    summary["from_ts"] = from_ts
    summary["to_ts"] = to_ts

    db = Database(db_path)
    art_root = Path(config.artifacts_root)
    hb_path = art_root / "heartbeat.json"
    burnin_out = art_root / "burnin"
    try:
        burn = getattr(config, "burn_in", None)
        phase = getattr(burn, "burn_in_phase", "demo") if burn else "demo"
        readiness: ReadinessResult = compute_readiness(
            db,
            heartbeat_path=hb_path,
            config_id=selected_config_id,
            window_hours=window_hours,
            burn_in_phase=phase,
        )
        summary["readiness"] = {
            "classification": readiness.classification,
            "message": readiness.message,
            "details": readiness.details,
        }
        acceptable = readiness.classification in (READINESS_READY_TESTNET, READINESS_READY_SMALL_LIVE)
        summary["readiness_acceptable"] = acceptable
        next_cmds: list[str] = []

        if not acceptable:
            # Stop early; tell operator what to review.
            next_cmds.append("python run_bot.py burnin report --window %.1f" % window_hours)
            next_cmds.append("python run_bot.py burnin readiness --window %.1f --output %s" % (window_hours, burnin_out))
            summary["evaluation"] = None
            summary["optimizer"] = None
            summary["candidates"] = None
            summary["shadow"] = None
            summary["next_commands"] = next_cmds
            return summary

        # 2) Evaluation
        ev_summary: Dict[str, Any] = {}
        try:
            ev = Evaluator(db_path)
            eval_result = ev.run(from_ts=from_ts, to_ts=to_ts, config_id=selected_config_id, symbol=None)
            ev_summary = {
                "run_id": eval_result.get("run_id"),
                "report_path": eval_result.get("report_path"),
                "trade_count": eval_result.get("trade_count", 0),
            }
        except Exception as e:
            ev_summary = {"error": str(e)}
        summary["evaluation"] = ev_summary

        trade_count = ev_summary.get("trade_count") or 0
        if trade_count <= 0:
            next_cmds.append("python run_bot.py evaluate --from-date YYYY-MM-DD --to-date YYYY-MM-DD")
            summary["optimizer"] = None
            summary["candidates"] = None
            summary["shadow"] = None
            summary["next_commands"] = next_cmds
            return summary

        # 3) Optimization
        opt_result: Dict[str, Any] = {}
        try:
            opt_out = run_optimization(
                db_path=db_path,
                config_id=selected_config_id,
                from_ts=from_ts,
                to_ts=to_ts,
                n_samples=n_samples,
            )
            opt_result = {
                "run_id": opt_out.get("run_id"),
                "best_candidate_config_id": opt_out.get("best_candidate_config_id"),
            }
        except Exception as e:
            opt_result = {"error": str(e)}
        summary["optimizer"] = opt_result

        run_id = opt_result.get("run_id")
        best_cand = opt_result.get("best_candidate_config_id")

        # 4) Candidate listing (for this optimizer run if possible)
        cand_info: Dict[str, Any] = {}
        try:
            conn = db._get_conn()
            rows = []
            if run_id:
                rows = conn.execute(
                    "SELECT config_id, optimizer_run_id, created_at FROM candidate_configs WHERE optimizer_run_id = ? ORDER BY created_at DESC",
                    (run_id,),
                ).fetchall()
            cand_info["count"] = len(rows)
            if best_cand:
                cand_info["top_candidate_id"] = best_cand
            elif rows:
                cand_info["top_candidate_id"] = rows[0][0]
            else:
                cand_info["top_candidate_id"] = None
            cand_info["optimizer_run_id"] = run_id
        except Exception as e:
            cand_info = {"error": str(e), "count": 0, "top_candidate_id": None, "optimizer_run_id": run_id}
        summary["candidates"] = cand_info

        top_candidate_id = cand_info.get("top_candidate_id")

        # 5) Optional shadow
        shadow_summary: Dict[str, Any] = {
            "start_requested": start_shadow,
            "report_requested": shadow_report,
            "started": False,
            "shadow_run_id": None,
            "report_path": None,
            "agreement_rate": None,
        }

        if start_shadow and top_candidate_id:
            runner = ShadowRunner(db_path)
            started = runner.start(top_candidate_id)
            shadow_summary["started"] = started
            if started:
                # Find latest shadow_run_id for this candidate
                conn = db._get_conn()
                row = conn.execute(
                    "SELECT id FROM shadow_runs WHERE candidate_config_id = ? ORDER BY started_at DESC LIMIT 1",
                    (top_candidate_id,),
                ).fetchone()
                if row:
                    shadow_summary["shadow_run_id"] = row[0]

        if shadow_report and top_candidate_id:
            # Use latest shadow run for this candidate
            conn = db._get_conn()
            row = conn.execute(
                "SELECT id FROM shadow_runs WHERE candidate_config_id = ? ORDER BY started_at DESC LIMIT 1",
                (top_candidate_id,),
            ).fetchone()
            if row:
                out = compare_baseline_shadow(row[0], db_path)
                shadow_summary["shadow_run_id"] = row[0]
                shadow_summary["agreement_rate"] = out.get("agreement_rate")
                shadow_summary["report_path"] = out.get("report_path")

        summary["shadow"] = shadow_summary

        # 6) Recommended next commands (manual)
        if top_candidate_id:
            if not start_shadow:
                next_cmds.append(f"python run_bot.py shadow start --candidate-config-id {top_candidate_id}")
            if not shadow_report:
                next_cmds.append(f"python run_bot.py shadow report --candidate-config-id {top_candidate_id}")
            next_cmds.append(f"python run_bot.py promote --config-id {top_candidate_id}")
        else:
            next_cmds.append("python run_bot.py candidates list")

        next_cmds.append("python run_bot.py promote status")
        summary["next_commands"] = next_cmds
        return summary
    finally:
        try:
            db.close()
        except Exception:
            pass

# --- Config subcommand ---
config_app = typer.Typer(help="Config versioning")

@config_app.command("list")
def config_list(
    status: Optional[str] = typer.Option(None, "--status"),
    limit: int = typer.Option(100, "--limit"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List config versions."""
    for r in list_config_versions(status=status, limit=limit, db_path=_db_path(config_path)):
        typer.echo(f"{r.get('config_id')}  {r.get('version')}  {r.get('status')}  {r.get('created_at')}")

@config_app.command("show")
def config_show(
    config_id: Optional[str] = typer.Argument(None, help="Config version ID"),
    config_id_opt: Optional[str] = typer.Option(None, "--config-id", help="Config version ID"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show a config version. Use: config show <config_id> or config show --config-id <id>."""
    cid = config_id_opt or config_id
    if not cid:
        typer.echo("Error: provide config_id as argument or --config-id"); raise typer.Exit(1)
    r = get_config_version(cid, _db_path(config_path))
    if not r:
        typer.echo("Not found"); raise typer.Exit(1)
    for k, v in r.items():
        typer.echo(f"{k}: {v}")

@config_app.command("activate")
def config_activate(
    config_id: str = typer.Argument(...),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Activate a config version."""
    ok = activate_config_version(config_id, _db_path(config_path))
    typer.echo("Activated" if ok else "Failed"); raise typer.Exit(0 if ok else 1)

@config_app.command("rollback")
def config_rollback(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    reason: str = typer.Option("manual rollback", "--reason"),
) -> None:
    """Rollback to previous active config."""
    new_id = rollback_to_previous_config(_db_path(config_path), reason=reason)
    if new_id:
        typer.echo(f"Rolled back to {new_id}")
    else:
        typer.echo("No previous config to roll back to"); raise typer.Exit(1)

@config_app.command("diff")
def config_diff(
    from_id: str = typer.Option(..., "--from"),
    to_id: str = typer.Option(..., "--to"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Diff two config versions."""
    d = diff_config_versions(from_id, to_id, _db_path(config_path))
    typer.echo(str(d))


# --- Optimize subcommand ---
optimize_app = typer.Typer(help="Walk-forward optimization")

@optimize_app.command("run")
def optimize_run(
    config_id: Optional[str] = typer.Option(None, "--config-id"),
    from_date: Optional[str] = typer.Option(None, "--from-date"),
    to_date: Optional[str] = typer.Option(None, "--to-date"),
    n_samples: int = typer.Option(20, "--n-samples"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Run walk-forward optimization. Writes artifacts to artifacts/optimizations/."""
    db_path = _db_path(config_path)
    from_ts = None
    to_ts = None
    if from_date:
        from_ts = int(time.mktime(time.strptime(from_date, "%Y-%m-%d"))) * 1000
    if to_date:
        to_ts = int(time.mktime(time.strptime(to_date, "%Y-%m-%d"))) * 1000
    out = run_optimization(db_path=db_path, config_id=config_id, from_ts=from_ts, to_ts=to_ts, n_samples=n_samples)
    typer.echo(f"Run ID: {out.get('run_id')}  Best candidate: {out.get('best_candidate_config_id')}")

@optimize_app.command("report")
def optimize_report(
    run_id: Optional[str] = typer.Argument(None, help="Optimization run ID"),
    run_id_opt: Optional[str] = typer.Option(None, "--run-id", help="Optimization run ID"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show optimization run summary. Use: optimize report <run_id> or optimize report --run-id <id>."""
    rid = run_id_opt or run_id
    if not rid:
        typer.echo("Error: provide run_id as argument or --run-id"); raise typer.Exit(1)
    db = Database(_db_path(config_path))
    row = db._get_conn().execute("SELECT * FROM optimization_runs WHERE run_id = ?", (rid,)).fetchone()
    db.close()
    if not row:
        typer.echo("Run not found"); raise typer.Exit(1)
    typer.echo(dict(row))


# --- Shadow subcommand ---
shadow_app = typer.Typer(help="Shadow comparison")

@shadow_app.command("start")
def shadow_start(
    candidate_config_id: Optional[str] = typer.Argument(None),
    candidate_config_id_opt: Optional[str] = typer.Option(None, "--candidate-config-id", help="Candidate config ID"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Start shadow run for candidate. Use: shadow start <id> or shadow start --candidate-config-id <id>. (Post-hoc: decisions recorded when bot records them; no live parallel scoring in this mode.)"""
    cid = candidate_config_id_opt or candidate_config_id
    if not cid:
        typer.echo("Error: provide candidate_config_id as argument or --candidate-config-id"); raise typer.Exit(1)
    runner = ShadowRunner(_db_path(config_path))
    ok = runner.start(cid)
    typer.echo("Shadow started" if ok else "Failed"); raise typer.Exit(0 if ok else 1)

@shadow_app.command("stop")
def shadow_stop(
    candidate_config_id: Optional[str] = typer.Option(None, "--candidate-config-id", help="Close this candidate's latest open run"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Stop shadow run. If --candidate-config-id given, closes that candidate's latest open run in DB; else no-op (in-process runner only)."""
    db_path = _db_path(config_path)
    if candidate_config_id:
        db = Database(db_path)
        conn = db._get_conn()
        now_ms = int(time.time() * 1000)
        conn.execute(
            "UPDATE shadow_runs SET stopped_at = ? WHERE id = (SELECT id FROM shadow_runs WHERE candidate_config_id = ? AND stopped_at IS NULL ORDER BY started_at DESC LIMIT 1)",
            (now_ms, candidate_config_id),
        )
        conn.commit()
        db.close()
    else:
        runner = ShadowRunner(db_path)
        runner.stop()
    typer.echo("Shadow stopped")

@shadow_app.command("report")
def shadow_report(
    candidate_config_id: Optional[str] = typer.Argument(None),
    candidate_config_id_opt: Optional[str] = typer.Option(None, "--candidate-config-id"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Comparison report for shadow run (post-hoc: uses stored baseline vs shadow decisions). Use: shadow report <id> or --candidate-config-id <id>."""
    cid = candidate_config_id_opt or candidate_config_id
    if not cid:
        typer.echo("Error: provide candidate_config_id as argument or --candidate-config-id"); raise typer.Exit(1)
    db = Database(_db_path(config_path))
    row = db._get_conn().execute(
        "SELECT id FROM shadow_runs WHERE candidate_config_id = ? ORDER BY started_at DESC LIMIT 1",
        (cid,),
    ).fetchone()
    db.close()
    if not row:
        typer.echo("No shadow run found"); raise typer.Exit(1)
    out = compare_baseline_shadow(row[0], _db_path(config_path))
    typer.echo(f"Agreement rate: {out.get('agreement_rate')}  Report: {out.get('report_path', 'N/A')}")


def register_stage3_cli(app: typer.Typer) -> None:
    """Register Stage 3 commands on the main app."""
    @app.command()
    def validate(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
        require_api_keys: bool = typer.Option(True, "--require-api-keys/--no-require-api-keys"),
    ) -> None:
        """Validate environment and config: config, .env, dirs, mode/env, strategy."""
        cfg = config_path or Path("config/config.yaml")
        result: ValidationResult = validate_environment(config_path=cfg, require_api_keys_for_live=require_api_keys)
        for e in result.errors:
            typer.echo(f"ERROR: {e}")
        for w in result.warnings:
            typer.echo(f"WARN: {w}")
        if result.ok:
            typer.echo("Validation OK.")
            mode = getattr(result, "operating_mode", None) or "live_guarded"
            typer.echo(f"operating_mode: {mode}")
            if mode == "demo_research":
                typer.echo("Ready for autonomous Demo research (demo_research). Start with: ./scripts/start_testnet_burnin.sh")
            else:
                typer.echo("Ready for guarded live trading (live_guarded). Manual approval required for config promotion and Demo→Live. Start with: ./scripts/check_small_live_ready.sh then ./scripts/start_small_live.sh")
            raise typer.Exit(0)
        typer.echo("Validation failed.")
        raise typer.Exit(1)

    app.add_typer(config_app, name="config")
    app.add_typer(optimize_app, name="optimize")
    app.add_typer(shadow_app, name="shadow")

    candidates_app = typer.Typer(help="Candidate configs")
    @candidates_app.command("list")
    def candidates_list_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        db = Database(_db_path(config_path))
        rows = db._get_conn().execute("SELECT config_id, optimizer_run_id, created_at FROM candidate_configs ORDER BY created_at DESC LIMIT 50").fetchall()
        db.close()
        for r in rows:
            typer.echo(f"{r[0]}  run={r[1]}  ts={r[2]}")
    app.add_typer(candidates_app, name="candidates")

    @app.command()
    def evaluate(
        from_date: Optional[str] = typer.Option(None, "--from-date"),
        to_date: Optional[str] = typer.Option(None, "--to-date"),
        config_id: Optional[str] = typer.Option(None, "--config-id"),
        symbol: Optional[str] = typer.Option(None, "--symbol"),
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Run evaluation report. Writes artifacts to config-scoped artifacts/evaluations/."""
        config, _ = load_config(config_path)
        db_path = config.database_path
        from_ts = None
        to_ts = None
        if from_date:
            from_ts = int(time.mktime(time.strptime(from_date, "%Y-%m-%d"))) * 1000
        if to_date:
            to_ts = int(time.mktime(time.strptime(to_date, "%Y-%m-%d"))) * 1000 + 86400 * 1000 - 1
        artifact_dir = Path(config.artifacts_root) / "evaluations"
        ev = Evaluator(db_path)
        summary = ev.run(from_ts=from_ts, to_ts=to_ts, config_id=config_id, symbol=symbol, artifact_dir=artifact_dir)
        typer.echo(f"Run ID: {summary.get('run_id')}  Report: {summary.get('report_path')}")
        if summary.get("trade_count", 0) == 0:
            typer.echo("No trades in window; report reflects empty metrics.")

    promote_app = typer.Typer(help="Promote candidate or show status")
    @promote_app.command("status")
    def promote_status_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Show current active config ID."""
        from src.config.versioning import get_active_config_id
        aid = get_active_config_id(_db_path(config_path))
        typer.echo(f"Active config: {aid or 'none'}")

    @promote_app.callback(invoke_without_command=True)
    def promote_callback(
        ctx: typer.Context,
        config_id: Optional[str] = typer.Option(None, "--config-id", help="Candidate config to promote"),
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        if config_id:
            ok, msg = promote_candidate(config_id, db_path=_db_path(config_path))
            typer.echo(msg); raise typer.Exit(0 if ok else 1)
        typer.echo("Use: promote --config-id <id> to promote, or promote status to show active config.")

    app.add_typer(promote_app, name="promote")

    @app.command()
    def rollback(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        reason: str = typer.Option("manual rollback", "--reason"),
    ) -> None:
        """Rollback to previous active config."""
        new_id = rollback_to_previous_config(_db_path(config_path), reason=reason)
        if new_id:
            typer.echo(f"Rolled back to {new_id}")
        else:
            typer.echo("No previous config"); raise typer.Exit(1)

    # --- Stage 5: health, status, report ---
    STALE_LOOP_SEC = 300.0
    # degradation_monitor runs on a slower cadence; do not fail health when it is older than 300s but still within 900s
    STALE_SEC_OVERRIDE_SLOW_LOOPS: dict[str, float] = {"degradation_monitor": 900.0}

    @app.command()
    def health(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat", help="Path to heartbeat file"),
        stale_sec: float = typer.Option(300.0, "--stale-sec", help="Loop considered stale after this many seconds"),
    ) -> None:
        """Health check: read heartbeat; report loop freshness; exit 1 if any loop stale or heartbeat missing."""
        config, env = load_config(config_path)
        db_path = Path(config.database_path)
        art = Path(config.artifacts_root)
        issues = []
        if not db_path.parent.exists():
            issues.append("DB directory missing")
        if not art.exists():
            art.mkdir(parents=True, exist_ok=True)
        hb_path = heartbeat_path or art / "heartbeat.json"
        if hb_path.exists():
            try:
                from src.monitoring.heartbeat import read_heartbeat
                data = read_heartbeat(hb_path)
                if data:
                    ts = data.get("ts", 0)
                    age = time.time() - ts
                    typer.echo(f"Heartbeat age: {age:.0f}s")
                    loops = data.get("loops") or {}
                    stale_loops = []
                    for name, loop in loops.items():
                        status = loop.get("status", "unknown")
                        last_ok = loop.get("last_ok_ts", 0)
                        loop_age = time.time() - last_ok if last_ok else 999999
                        effective_stale_sec = STALE_SEC_OVERRIDE_SLOW_LOOPS.get(name, stale_sec)
                        typer.echo(f"  {name}: {status} (last_ok {loop_age:.0f}s ago) {loop.get('message') or ''}")
                        if status == "fail":
                            stale_loops.append(name)
                        elif loop_age > effective_stale_sec:
                            stale_loops.append(name)
                    if age > stale_sec:
                        issues.append("Heartbeat file stale >{}s".format(int(stale_sec)))
                    if stale_loops:
                        issues.append("Stale/fail loops: " + ", ".join(stale_loops))
                else:
                    typer.echo("Heartbeat empty")
                    issues.append("Heartbeat empty")
            except Exception as e:
                typer.echo(f"Heartbeat read error: {e}")
                issues.append(str(e))
        else:
            typer.echo("No heartbeat file (bot may not be running)")
            issues.append("No heartbeat file")
        if issues:
            typer.echo("Issues: " + "; ".join(issues))
            raise typer.Exit(1)

    @app.command("show-runtime-mode")
    def show_runtime_mode(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Show operating mode, environment, automation, and manual approval gates."""
        from src.config.config import resolve_bybit_credentials, get_bybit_env, get_effective_operating_mode, get_demo_research_runtime_info
        from src.cli.validate_env import _has_dual_key_demo, _has_dual_key_live, _has_legacy_keys
        config, env = load_config(config_path)
        env_type = get_bybit_env(env)
        operating_mode = get_effective_operating_mode(config, env)
        typer.echo(f"operating_mode: {operating_mode}")
        if getattr(config, "instance_name", None):
            typer.echo(f"instance_name: {config.instance_name}")
        typer.echo(f"selected_environment: {env_type.upper()}")
        if operating_mode == "demo_research":
            info = get_demo_research_runtime_info(config, env)
            typer.echo(f"fixed_equity_enabled: {info['fixed_equity_enabled']}")
            typer.echo(f"effective_equity_source: {info['effective_equity_source']}")
            if info.get("effective_strategy_equity_usdt") is not None:
                typer.echo(f"effective_strategy_equity_usdt: {info['effective_strategy_equity_usdt']}")
            typer.echo(f"relaxed_kill_switch_enabled: {info['relaxed_kill_switch_enabled']}")
        auto = getattr(config, "automation", None)
        automation_active = bool(auto and getattr(auto, "enabled", False) and getattr(auto, "demo_orchestration_enabled", False))
        typer.echo(f"automation_active: {automation_active}")
        typer.echo(f"manual_approval_required: {operating_mode == 'live_guarded'}")
        api_key, api_secret, is_legacy, _ = resolve_bybit_credentials(env, env_type)
        typer.echo(f"credential_mode: {'legacy' if is_legacy else 'dual_key'}")
        typer.echo(f"selected_key_pair: {'present' if (api_key and api_secret) else 'missing'}")
        dual_demo = _has_dual_key_demo(env)
        dual_live = _has_dual_key_live(env)
        typer.echo(f"dual_key_configured: demo={dual_demo} live={dual_live}")
        typer.echo(f"mode: {config.mode}")
        typer.echo(f"dry_run: {config.dry_run}")
        typer.echo(f"exchange.testnet: {config.exchange.testnet}")
        typer.echo(f"BYBIT_ENV: {getattr(env, 'bybit_env', '') or 'N/A'}")
        burn_in = getattr(config, "burn_in", None)
        if burn_in:
            typer.echo(f"burn_in_enabled: {getattr(burn_in, 'burn_in_enabled', False)}")
            typer.echo(f"burn_in_phase: {getattr(burn_in, 'burn_in_phase', 'demo')}")
        else:
            typer.echo("burn_in_enabled: false")
        from src.config.versioning import get_active_config_id
        aid = get_active_config_id(config.database_path)
        typer.echo(f"active_config_id: {aid or 'none'}")
        typer.echo(f"active_strategy: {getattr(config, 'active_strategy', 'flow_impulse')}")
        if operating_mode == "demo_research":
            from src.warm_start import get_warm_start_status
            ws = get_warm_start_status(config.database_path, config_path)
            typer.echo(f"warm_start_enabled: {ws.get('warm_start_enabled', False)}")
            typer.echo(f"warm_start_needed: {ws.get('warm_start_needed', False)}")
            if ws.get("last_warm_start_report"):
                r = ws["last_warm_start_report"]
                typer.echo(f"warm_start_last_seed: {r.get('seed_config_id') or 'none'}")
                typer.echo(f"warm_start_fallback_used: {r.get('fallback_used', False)}")
        if is_legacy:
            typer.echo("WARN: Using legacy single-key. Set BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET for dual-key.")
        if operating_mode == "live_guarded" and env_type != "live":
            typer.echo("WARN: operating_mode is live_guarded but BYBIT_ENV is not live. Set BYBIT_ENV=live for guarded live.")
        if config.dry_run:
            typer.echo("execution: simulated only (dry_run=true; no orders will be placed)")
        else:
            typer.echo("execution: real orders will be placed on {}".format(env_type.upper()))

    @app.command("promote-env")
    def promote_env(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
        env_path: Optional[Path] = typer.Option(None, "--env", "-e", help="Path to .env"),
        confirm_live: bool = typer.Option(False, "--confirm-live", help="Confirm switch from Demo to Live (required to apply changes)"),
        start_live: bool = typer.Option(False, "--start-live", help="After switching, start guarded live service (only with --confirm-live)"),
        reason: Optional[str] = typer.Option(None, "--reason", help="Reason for promotion (e.g. 'demo burn-in passed')"),
        no_backup: bool = typer.Option(False, "--no-backup", help="Do not backup .env and config before changing"),
        window_hours: float = typer.Option(24.0, "--window", help="Readiness window hours"),
    ) -> None:
        """Safe promote environment: Demo -> guarded Live. Preview by default; use --confirm-live to apply."""
        from src.cli.promote_env import (
            run_promote_env_prechecks,
            apply_promote_env,
            write_promotion_artifact,
        )
        cfg = config_path or Path("config/config.yaml")
        env_file = env_path or Path(".env")

        precheck = run_promote_env_prechecks(
            config_path=cfg,
            env_path=env_file,
            window_hours=window_hours,
        )

        typer.echo("=== Promote environment (Demo -> Live) ===")
        typer.echo("current_environment: %s" % precheck.current_env.upper())
        typer.echo("live_credentials_present: %s" % precheck.live_credentials_present)
        if precheck.readiness:
            typer.echo("readiness: %s" % precheck.readiness.classification)
            typer.echo("readiness_message: %s" % (precheck.readiness.message or ""))
        for w in precheck.warnings:
            typer.echo("WARN: %s" % w)
        for e in precheck.errors:
            typer.echo("ERROR: %s" % e)

        if not precheck.ok:
            typer.echo("")
            typer.echo("Prechecks failed. Fix errors above before promoting. No files were changed.")
            raise typer.Exit(1)

        typer.echo("")
        typer.echo("Files that would be changed: .env (BYBIT_ENV=live), %s (burn_in_phase=live_small)" % cfg)
        if not confirm_live:
            typer.echo("")
            typer.echo("Preview only. To apply the switch, run:")
            typer.echo("  python run_bot.py promote-env --confirm-live")
            if reason:
                typer.echo("  python run_bot.py promote-env --confirm-live --reason \"%s\"" % reason)
            typer.echo("To also start guarded live after switching:")
            typer.echo("  python run_bot.py promote-env --confirm-live --start-live")
            raise typer.Exit(0)

        ok, report = apply_promote_env(
            config_path=cfg,
            env_path=env_file,
            backup=not no_backup,
            reason=reason,
        )
        report["start_live_requested"] = start_live
        if precheck.readiness:
            report["readiness_used"] = precheck.readiness.classification
        if not ok:
            typer.echo("Promotion failed: %s" % report.get("error", "unknown"))
            raise typer.Exit(1)

        _config, _ = load_config(cfg)
        art_path = write_promotion_artifact(report, Path(_config.artifacts_root))
        typer.echo("")
        typer.echo("Promotion applied.")
        typer.echo("  Files changed: %s" % ", ".join(report.get("files_changed", [])))
        typer.echo("  Backups: %s" % ", ".join(report.get("backups_created", [])) or "none")
        typer.echo("  Artifact: %s" % art_path)
        if start_live:
            typer.echo("")
            typer.echo("Starting guarded live...")
            import subprocess
            import sys
            r = subprocess.run(
                [sys.executable, "run_bot.py", "validate", "--config", str(cfg)],
                capture_output=True,
                text=True,
                cwd=Path.cwd(),
            )
            if r.returncode != 0:
                typer.echo("Validation failed after switch. Run: python run_bot.py validate")
                raise typer.Exit(1)
            typer.echo("Run guarded live with: ./scripts/start_small_live.sh (or python run_bot.py run)")
            typer.echo("Monitor: ./scripts/check_burnin.sh  python run_bot.py health")
        else:
            typer.echo("")
            typer.echo("Next: start guarded live with:")
            typer.echo("  ./scripts/start_small_live.sh")
            typer.echo("Or in foreground: ./scripts/start_small_live.sh --foreground")
            typer.echo("Then: ./scripts/check_burnin.sh  python run_bot.py health")

    @app.command("promote-to-live")
    def promote_to_live(
        candidate_config_id: str = typer.Option(..., "--candidate-config-id", help="Demo candidate config_id to import"),
        demo_config: Optional[Path] = typer.Option(None, "--demo-config", help="Demo config path (default: config/config.demo.yaml)"),
        live_config: Optional[Path] = typer.Option(None, "--live-config", help="Live config path (default: config/config.live.yaml)"),
        activate: bool = typer.Option(False, "--activate", help="Activate the imported config in Live (default: import only)"),
        reason: Optional[str] = typer.Option(None, "--reason", help="Reason for promotion (e.g. 'approved after demo review')"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; do not write to Live"),
    ) -> None:
        """Import a Demo candidate into the Live instance. Default: import only; use --activate to make it active in Live."""
        from pathlib import Path
        demo_cfg = demo_config or Path("config/config.demo.yaml")
        live_cfg = live_config or Path("config/config.live.yaml")
        if not demo_cfg.exists():
            typer.echo("ERROR: Demo config not found: %s" % demo_cfg)
            raise typer.Exit(1)
        if not live_cfg.exists():
            typer.echo("ERROR: Live config not found: %s" % live_cfg)
            raise typer.Exit(1)
        _demo, _ = load_config(demo_cfg)
        _live, _ = load_config(live_cfg)
        demo_db = _demo.database_path
        live_db = _live.database_path
        live_artifact_dir = Path(_live.artifacts_root) / "configs"
        result = import_candidate_to_live(
            candidate_config_id=candidate_config_id,
            demo_db_path=demo_db,
            live_db_path=live_db,
            live_artifact_dir=live_artifact_dir,
            description="Imported from Demo candidate %s" % candidate_config_id,
            reason=reason or ("approved after demo review" if activate else ""),
            activate=activate,
            dry_run=dry_run,
        )
        typer.echo("=== Promote to Live (Demo candidate -> Live instance) ===")
        typer.echo("candidate_config_id: %s" % result["candidate_config_id"])
        typer.echo("demo_db: %s" % demo_db)
        typer.echo("live_db: %s" % live_db)
        if not result["ok"]:
            typer.echo("ERROR: %s" % result["error"])
            raise typer.Exit(1)
        typer.echo("live_config_id: %s" % result["live_config_id"])
        typer.echo("imported: %s" % result["imported"])
        typer.echo("already_present: %s" % result["already_present"])
        typer.echo("activated: %s" % result["activated"])
        if result.get("dry_run"):
            typer.echo("(dry-run: no changes written)")
        typer.echo("")
        if result["activated"]:
            typer.echo("Config is now active in Live. Restart Live instance to use it:")
            typer.echo("  sudo systemctl restart money-flow-momentum-live")
            typer.echo("  (or ./scripts/start_live_guarded.sh)")
        else:
            typer.echo("Next: inspect the imported config in Live:")
            typer.echo("  python run_bot.py config show --config-id %s --config %s" % (result["live_config_id"], live_cfg))
            typer.echo("To activate it in Live (either):")
            typer.echo("  python run_bot.py promote-to-live --candidate-config-id %s --demo-config %s --live-config %s --activate" % (candidate_config_id, demo_cfg, live_cfg))
            typer.echo("  python run_bot.py promote --config-id %s --config %s" % (result["live_config_id"], live_cfg))
            if reason:
                typer.echo("  (add --reason \"...\" to promote-to-live if desired)")

    @app.command()
    def status(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat"),
    ) -> None:
        """Diagnostics: operating mode, active config, DB path, Stage 5, strategy, artifact dirs, last heartbeat freshness."""
        config, env = load_config(config_path)
        from src.config.config import get_bybit_env, get_effective_operating_mode
        operating_mode = get_effective_operating_mode(config, env)
        env_type = get_bybit_env(env)
        typer.echo(f"operating_mode: {operating_mode}")
        typer.echo(f"selected_environment: {env_type.upper()}")
        if operating_mode == "demo_research":
            from src.config.config import get_demo_research_runtime_info
            info = get_demo_research_runtime_info(config, env)
            typer.echo(f"fixed_equity_enabled: {info['fixed_equity_enabled']}")
            typer.echo(f"effective_equity_source: {info['effective_equity_source']}")
            if info.get("effective_strategy_equity_usdt") is not None:
                typer.echo(f"effective_strategy_equity_usdt: {info['effective_strategy_equity_usdt']}")
            typer.echo(f"relaxed_kill_switch_enabled: {info['relaxed_kill_switch_enabled']}")
        auto = getattr(config, "automation", None)
        automation_active = bool(auto and getattr(auto, "enabled", False) and getattr(auto, "demo_orchestration_enabled", False))
        typer.echo(f"automation_active: {automation_active}")
        from src.config.versioning import get_active_config_id
        aid = get_active_config_id(config.database_path)
        typer.echo(f"Active config: {aid or 'none'}")
        typer.echo(f"Database: {config.database_path}")
        if operating_mode == "demo_research":
            from src.warm_start import get_warm_start_status
            ws = get_warm_start_status(config.database_path, config_path)
            typer.echo(f"warm_start_enabled: {ws.get('warm_start_enabled', False)}")
            typer.echo(f"warm_start_needed: {ws.get('warm_start_needed', False)}")
            if ws.get("last_warm_start_report"):
                r = ws["last_warm_start_report"]
                typer.echo(f"warm_start_seed_config_id: {r.get('seed_config_id') or 'none'}")
                typer.echo(f"warm_start_fallback_used: {r.get('fallback_used', False)}")
        typer.echo(f"Stage 5: {getattr(config, 'stage5_enabled', False)}")
        typer.echo(f"Strategy: {getattr(config, 'active_strategy', 'flow_impulse')}")
        art_root = Path(config.artifacts_root)
        for name in ("artifacts", "artifacts/evaluations", "artifacts/optimizations"):
            p = art_root if name == "artifacts" else (art_root / "evaluations" if "evaluations" in name else art_root / "optimizations")
            typer.echo(f"  {p}: {'exists' if p.exists() else 'missing'}")
        hb_path = heartbeat_path or art_root / "heartbeat.json"
        if hb_path.exists():
            try:
                from src.monitoring.heartbeat import read_heartbeat
                data = read_heartbeat(hb_path)
                if data:
                    meta = data.get("meta") or {}
                    typer.echo(f"Heartbeat: {hb_path} (age {time.time() - data.get('ts', 0):.0f}s)")
                    if meta:
                        typer.echo(f"  config_id: {meta.get('config_id', 'N/A')}  strategy: {meta.get('strategy', 'N/A')}")
                    for name, loop in (data.get("loops") or {}).items():
                        last_ok = loop.get("last_ok_ts", 0)
                        age = time.time() - last_ok if last_ok else None
                        typer.echo(f"  loop {name}: {loop.get('status')} (last_ok {age:.0f}s ago)" if age is not None else f"  loop {name}: {loop.get('status')}")
                else:
                    typer.echo("Heartbeat file empty")
            except Exception as e:
                typer.echo(f"Heartbeat read error: {e}")
        else:
            typer.echo("No heartbeat file (runtime state unknown)")
        burn_in = getattr(config, "burn_in", None)
        if burn_in and getattr(burn_in, "burn_in_enabled", False):
            typer.echo(f"Burn-in: enabled  phase={getattr(burn_in, 'burn_in_phase', 'demo')}")

    @app.command()
    def report(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat"),
    ) -> None:
        """Summary: active config, degradation events, promotions, loop health (from heartbeat if present)."""
        config, env = load_config(config_path)
        from src.config.config import get_bybit_env
        env_type = get_bybit_env(env)
        typer.echo(f"selected_environment: {env_type.upper()}")
        db = Database(config.database_path)
        conn = db._get_conn()
        from src.config.versioning import get_active_config_id
        aid = get_active_config_id(config.database_path)
        typer.echo(f"Active config: {aid or 'none'}")
        deg = conn.execute("SELECT COUNT(*) FROM degradation_events WHERE ts > ?", (int(time.time() * 1000) - 86400 * 1000,)).fetchone()[0]
        prom = conn.execute("SELECT promoted_config_id, promoted_at FROM promotion_events ORDER BY promoted_at DESC LIMIT 3").fetchall()
        typer.echo(f"Degradation events (24h): {deg}")
        for r in prom:
            typer.echo(f"  Promotion: {r[0]} at {r[1]}")
        art_root = Path(config.artifacts_root)
        hb_path = heartbeat_path or art_root / "heartbeat.json"
        if hb_path.exists():
            try:
                from src.monitoring.heartbeat import read_heartbeat
                data = read_heartbeat(hb_path)
                if data and data.get("loops"):
                    typer.echo("Loop health:")
                    for k, v in data["loops"].items():
                        last_ok = v.get("last_ok_ts", 0)
                        age = time.time() - last_ok if last_ok else None
                        effective_stale = STALE_SEC_OVERRIDE_SLOW_LOOPS.get(k, 300.0)
                        stale = " (stale)" if (age is not None and age > effective_stale) else ""
                        typer.echo(f"  {k}: {v.get('status')}{stale}")
                else:
                    typer.echo("No loop data in heartbeat")
            except Exception as e:
                typer.echo(f"Heartbeat read error: {e}")
        else:
            typer.echo("No heartbeat file; runtime loop state unknown.")
        burn_in = getattr(config, "burn_in", None)
        if burn_in and getattr(burn_in, "burn_in_enabled", False):
            typer.echo(f"Burn-in: enabled  phase={getattr(burn_in, 'burn_in_phase', 'demo')}")
        db.close()

    # --- Burn-in validation ---
    burnin_app = typer.Typer(help="Burn-in / validation mode status and readiness")
    @burnin_app.command("status")
    def burnin_status_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Show burn-in config and recent gate breaches (if any). Works with missing data."""
        config, _ = load_config(config_path)
        burn = getattr(config, "burn_in", None)
        if not burn:
            typer.echo("Burn-in config not found (use defaults)")
            return
        typer.echo(f"Burn-in enabled: {getattr(burn, 'burn_in_enabled', False)}")
        typer.echo(f"Phase: {getattr(burn, 'burn_in_phase', 'demo')}")
        typer.echo(f"Max trades/day: {getattr(burn, 'burn_in_max_trades_per_day', 20)}")
        typer.echo(f"Max notional/day: {getattr(burn, 'burn_in_max_notional_usdt', 5000)}")
        db = Database(config.database_path)
        try:
            from_ts = int(time.time() * 1000) - 86400 * 1000
            breaches = db.get_burnin_gate_breaches(since_ts=from_ts)
            typer.echo(f"Gate breaches (24h): {len(breaches)}")
            for b in breaches[-5:]:
                typer.echo(f"  {b.get('gate_name')}: {b.get('message')}")
        except Exception as e:
            typer.echo(f"Could not read breaches: {e}")
        db.close()

    @burnin_app.command("report")
    def burnin_report_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        window_hours: float = typer.Option(24.0, "--window"),
    ) -> None:
        """Summary of execution audit, protection audit, and gate breaches in window."""
        config, _ = load_config(config_path)
        db = Database(config.database_path)
        from_ts = int(time.time() * 1000) - int(window_hours * 3600 * 1000)
        to_ts = int(time.time() * 1000)
        try:
            exec_audit = db.get_execution_audit(since_ts=from_ts, to_ts=to_ts)
            prot = db.get_protection_audit(since_ts=from_ts, to_ts=to_ts)
            breaches = db.get_burnin_gate_breaches(since_ts=from_ts, to_ts=to_ts)
            typer.echo(f"Window: {window_hours}h")
            typer.echo(f"Execution audit records: {len(exec_audit)}")
            drift = sum(1 for e in exec_audit if e.get("mismatch_reason"))
            typer.echo(f"Execution drift count: {drift}")
            typer.echo(f"Protection audit records: {len(prot)}")
            typer.echo(f"Gate breaches: {len(breaches)}")
            kill_events = db.get_kill_switch_events(since_ts=from_ts, to_ts=to_ts)
            typer.echo(f"Kill switch events: {len(kill_events)}")
            for ev in kill_events:
                ts_val = ev.get("ts")
                if isinstance(ts_val, int):
                    ts_str = datetime.utcfromtimestamp(ts_val / 1000.0).strftime("%Y-%m-%d %H:%M:%S UTC")
                else:
                    ts_str = str(ts_val)
                typer.echo(f"  {ts_str}  {ev.get('reason', '')}")
        except Exception as e:
            typer.echo(f"Error: {e}")
        db.close()

    @burnin_app.command("readiness")
    def burnin_readiness_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        window_hours: float = typer.Option(24.0, "--window"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat"),
        output_dir: Optional[Path] = typer.Option(None, "--output", help="Write JSON and MD to this dir (default artifacts/burnin)"),
    ) -> None:
        """Compute burn-in readiness; print classification and write artifacts if --output given."""
        config, _ = load_config(config_path)
        db = Database(config.database_path)
        art_root = Path(config.artifacts_root)
        hb_path = heartbeat_path or art_root / "heartbeat.json"
        burn = getattr(config, "burn_in", None)
        phase = getattr(burn, "burn_in_phase", "demo") if burn else "demo"
        from src.validation.readiness import compute_readiness
        from src.config.versioning import get_active_config_id
        config_id = get_active_config_id(config.database_path)
        result = compute_readiness(db, heartbeat_path=hb_path, config_id=config_id, window_hours=window_hours, burn_in_phase=phase)
        typer.echo(f"Readiness: {result.classification}")
        typer.echo(f"Message: {result.message}")
        for k, v in result.details.items():
            typer.echo(f"  {k}: {v}")
        out_dir = output_dir or art_root / "burnin"
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        import json
        ts = int(time.time() * 1000)
        path_json = out_dir / f"readiness_{ts}.json"
        with open(path_json, "w") as f:
            json.dump({"classification": result.classification, "message": result.message, "details": result.details, "ts": ts}, f, default=str)
        path_md = out_dir / f"readiness_{ts}.md"
        with open(path_md, "w") as f:
            f.write(f"# Burn-in Readiness\n\n**{result.classification}**\n\n{result.message}\n\n## Details\n\n")
            for k, v in result.details.items():
                f.write(f"- {k}: {v}\n")
        typer.echo(f"Wrote {path_json} and {path_md}")
        db.close()

    app.add_typer(burnin_app, name="burnin")

    # --- Warm-start (Demo-only: historical candle calibration before first trading) ---
    warm_start_app = typer.Typer(help="Demo warm-start: calibrate from historical candles before first trading")
    @warm_start_app.command("run")
    def warm_start_run_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Run warm-start calibration (fetch candles, optimize, seed Demo). Demo-only; no Live impact."""
        from src.warm_start import run_warm_start_calibration
        config, _ = load_config(config_path)
        result = run_warm_start_calibration(
            demo_db_path=config.database_path,
            config_path=config_path,
            artifact_dir=Path(config.artifacts_root),
        )
        if result.get("skipped"):
            typer.echo(f"Warm-start skipped: {result.get('reason')}")
            raise typer.Exit(0)
        if result.get("error"):
            typer.echo(f"Warm-start error: {result.get('error')}")
        typer.echo(f"success: {result.get('success')}  reason: {result.get('reason')}")
        typer.echo(f"seed_config_id: {result.get('seed_config_id') or 'none'}")
        typer.echo(f"fallback_used: {result.get('fallback_used', False)}")
        typer.echo(f"warm_start_used: {result.get('warm_start_used', False)}")
        raise typer.Exit(0 if result.get("success") else 1)
    @warm_start_app.command("status")
    def warm_start_status_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Show warm-start status: enabled, needed, last seed, fallback."""
        from src.warm_start import get_warm_start_status
        config, _ = load_config(config_path)
        ws = get_warm_start_status(config.database_path, config_path)
        typer.echo(f"operating_mode: {ws.get('operating_mode')}")
        typer.echo(f"warm_start_enabled: {ws.get('warm_start_enabled')}")
        typer.echo(f"warm_start_applies: {ws.get('warm_start_applies')}")
        typer.echo(f"warm_start_needed: {ws.get('warm_start_needed')}")
        typer.echo(f"reason: {ws.get('reason')}")
        typer.echo(f"active_config_id: {ws.get('active_config_id') or 'none'}")
        if ws.get("last_warm_start_report"):
            r = ws["last_warm_start_report"]
            typer.echo(f"last_seed_config_id: {r.get('seed_config_id') or 'none'}")
            typer.echo(f"last_fallback_used: {r.get('fallback_used', False)}")
    app.add_typer(warm_start_app, name="warm-start")

    # --- Automation / orchestration ---
    automation_app = typer.Typer(help="Demo automation / orchestration status and control")

    @automation_app.command("cycle")
    def automation_cycle_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Run a single automation cycle (safe to schedule periodically)."""
        out = run_demo_automation_cycle(config_path=config_path)
        snap = out.get("snapshot", {})
        typer.echo(f"state: {snap.get('state')}")
        typer.echo(f"last_recommendation_status: {snap.get('last_recommendation_status')}")

    @automation_app.command("status")
    def automation_status_cmd(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    ) -> None:
        """Show current automation snapshot and latest recommendation artifact."""
        out = get_automation_status(config_path=config_path)
        typer.echo(f"automation_enabled: {out.get('automation_enabled')}")
        typer.echo(f"env: {out.get('env')}")
        snap = out.get("snapshot", {}) or {}
        typer.echo(f"state: {snap.get('state')}")
        typer.echo(f"last_recommendation_status: {snap.get('last_recommendation_status')}")
        typer.echo(f"best_candidate_config_id: {snap.get('best_candidate_config_id')}")
        typer.echo(f"shadow_candidate_config_id: {snap.get('shadow_candidate_config_id')}")
        artifact = out.get("artifact") or {}
        if artifact:
            cfg, _ = load_config(config_path)
            typer.echo("latest_artifact: %s" % (Path(cfg.artifacts_root) / "automation" / "automation_status.json"))
        else:
            typer.echo("latest_artifact: none")

    app.add_typer(automation_app, name="automation")

    @app.command("post-burnin")
    def post_burnin(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
        from_date: Optional[str] = typer.Option(None, "--from-date", help="Evaluation/optimizer from-date (YYYY-MM-DD)"),
        to_date: Optional[str] = typer.Option(None, "--to-date", help="Evaluation/optimizer to-date (YYYY-MM-DD)"),
        config_id: Optional[str] = typer.Option(None, "--config-id", help="Config version to evaluate/optimize (defaults to active)"),
        n_samples: int = typer.Option(20, "--n-samples", help="Optimizer sample count"),
        window_hours: float = typer.Option(24.0, "--window", help="Readiness window hours"),
        start_shadow: bool = typer.Option(False, "--start-shadow", help="Start shadow run for top candidate (does not promote)"),
        shadow_report: bool = typer.Option(False, "--shadow-report", help="Generate shadow report for latest run of top candidate"),
        output_dir: Optional[Path] = typer.Option(None, "--output", help="Directory for pipeline summary artifacts (default artifacts/pipeline)"),
    ) -> None:
        """
        Post-burn-in helper: runs readiness, evaluation, optimizer, candidate listing,
        and optional shadow start/report. Does NOT auto-promote or switch environments.
        """
        config, _ = load_config(config_path)
        default_output = Path(config.artifacts_root) / "pipeline"
        output_dir = output_dir or default_output
        summary = run_post_burnin_pipeline(
            config_path=config_path,
            from_date=from_date,
            to_date=to_date,
            config_id=config_id,
            n_samples=n_samples,
            window_hours=window_hours,
            start_shadow=start_shadow,
            shadow_report=shadow_report,
            output_dir=output_dir,
        )

        # Write summary artifact
        out_base = Path(output_dir)
        out_base = Path(out_base)
        out_base.mkdir(parents=True, exist_ok=True)
        ts = summary.get("timestamp_ms") or int(time.time() * 1000)
        json_path = out_base / f"post_burnin_{ts}.json"
        md_path = out_base / f"post_burnin_{ts}.md"

        import json as _json

        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(summary, f, indent=2, default=str)

        # Human-readable summary
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Post-burn-in Pipeline Summary\n\n")
            f.write(f"- timestamp_ms: {summary.get('timestamp_ms')}\n")
            f.write(f"- active_config_id: {summary.get('config_id_active')}\n")
            f.write(f"- selected_config_id: {summary.get('config_id_selected')}\n")
            ready = summary.get("readiness", {})
            f.write(f"- readiness: {ready.get('classification')} — {ready.get('message')}\n")
            eval_s = summary.get("evaluation") or {}
            if eval_s:
                f.write(f"- evaluation: run_id={eval_s.get('run_id')} report={eval_s.get('report_path')}\n")
            opt_s = summary.get("optimizer") or {}
            if opt_s:
                f.write(f"- optimizer: run_id={opt_s.get('run_id')} best_candidate={opt_s.get('best_candidate_config_id')}\n")
            cand_s = summary.get("candidates") or {}
            if cand_s:
                f.write(f"- candidates: count={cand_s.get('count')} top_candidate_id={cand_s.get('top_candidate_id')}\n")
            sh_s = summary.get("shadow") or {}
            if sh_s:
                f.write(f"- shadow_started: {sh_s.get('started')} shadow_run_id={sh_s.get('shadow_run_id')} agreement_rate={sh_s.get('agreement_rate')}\n")
            f.write("\n## Next recommended commands\n\n")
            for cmd in summary.get("next_commands", []):
                f.write(f"- {cmd}\n")

        # CLI summary
        typer.echo("=== Post-burn-in pipeline summary ===")
        typer.echo(f"Readiness: {ready.get('classification')} — {ready.get('message')}")
        if not summary.get("readiness_acceptable", False):
            typer.echo("Readiness not acceptable; see burn-in artifacts and summary for details.")
        else:
            typer.echo("Readiness acceptable for post-burn-in evaluation/optimization.")
        if eval_s:
            typer.echo(f"Evaluation: run_id={eval_s.get('run_id')} report={eval_s.get('report_path')}")
        if opt_s:
            typer.echo(f"Optimizer: run_id={opt_s.get('run_id')} best_candidate={opt_s.get('best_candidate_config_id')}")
        if cand_s:
            typer.echo(f"Candidates: count={cand_s.get('count')} top_candidate_id={cand_s.get('top_candidate_id')}")
        if sh_s:
            typer.echo(f"Shadow: started={sh_s.get('started')} run_id={sh_s.get('shadow_run_id')} agreement_rate={sh_s.get('agreement_rate')}")
        typer.echo("Summary artifacts:")
        typer.echo(f"  JSON: {json_path}")
        typer.echo(f"  Markdown: {md_path}")
        typer.echo("Next recommended commands:")
        for cmd in summary.get("next_commands", []):
            typer.echo(f"  {cmd}")
