"""Stage 3 CLI: config, evaluate, optimize, shadow, promote, rollback."""

import time
from pathlib import Path
from typing import Optional

import typer

from src.config.config import load_config
from src.config.versioning import (
    list_config_versions,
    get_config_version,
    activate_config_version,
    rollback_to_previous_config,
    diff_config_versions,
)
from src.evaluation.evaluator import Evaluator
from src.optimizer.search import run_optimization
from src.shadow.shadow_runner import ShadowRunner
from src.shadow.comparison import compare_baseline_shadow
from src.promotion.promoter import promote_candidate
from src.storage.db import Database


def _db_path(config_path: Optional[Path] = None) -> str:
    config, _ = load_config(config_path)
    return config.database_path


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
        """Run evaluation report. Writes artifacts to artifacts/evaluations/."""
        db_path = _db_path(config_path)
        from_ts = None
        to_ts = None
        if from_date:
            from_ts = int(time.mktime(time.strptime(from_date, "%Y-%m-%d"))) * 1000
        if to_date:
            to_ts = int(time.mktime(time.strptime(to_date, "%Y-%m-%d"))) * 1000 + 86400 * 1000 - 1
        ev = Evaluator(db_path)
        summary = ev.run(from_ts=from_ts, to_ts=to_ts, config_id=config_id, symbol=symbol)
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

    @app.command()
    def health(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat", help="Path to heartbeat file"),
        stale_sec: float = typer.Option(300.0, "--stale-sec", help="Loop considered stale after this many seconds"),
    ) -> None:
        """Health check: read heartbeat; report loop freshness; exit 1 if any loop stale or heartbeat missing."""
        config, env = load_config(config_path)
        db_path = Path(config.database_path)
        art = Path("artifacts")
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
                        typer.echo(f"  {name}: {status} (last_ok {loop_age:.0f}s ago) {loop.get('message') or ''}")
                        if status == "fail" or loop_age > stale_sec:
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
        typer.echo("OK")

    @app.command()
    def status(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat"),
    ) -> None:
        """Diagnostics: active config, DB path, Stage 5, strategy, artifact dirs, last heartbeat freshness."""
        config, _ = load_config(config_path)
        from src.config.versioning import get_active_config_id
        aid = get_active_config_id(config.database_path)
        typer.echo(f"Active config: {aid or 'none'}")
        typer.echo(f"Database: {config.database_path}")
        typer.echo(f"Stage 5: {getattr(config, 'stage5_enabled', False)}")
        typer.echo(f"Strategy: {getattr(config, 'active_strategy', 'flow_impulse')}")
        for name in ("artifacts", "artifacts/evaluations", "artifacts/optimizations"):
            p = Path(name)
            typer.echo(f"  {name}: {'exists' if p.exists() else 'missing'}")
        hb_path = heartbeat_path or Path("artifacts/heartbeat.json")
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
            typer.echo(f"Burn-in: enabled  phase={getattr(burn_in, 'burn_in_phase', 'testnet')}")

    @app.command()
    def report(
        config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
        heartbeat_path: Optional[Path] = typer.Option(None, "--heartbeat"),
    ) -> None:
        """Summary: active config, degradation events, promotions, loop health (from heartbeat if present)."""
        config, _ = load_config(config_path)
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
        hb_path = heartbeat_path or Path("artifacts/heartbeat.json")
        if hb_path.exists():
            try:
                from src.monitoring.heartbeat import read_heartbeat
                data = read_heartbeat(hb_path)
                if data and data.get("loops"):
                    typer.echo("Loop health:")
                    for k, v in data["loops"].items():
                        last_ok = v.get("last_ok_ts", 0)
                        age = time.time() - last_ok if last_ok else None
                        stale = " (stale)" if (age is not None and age > 300) else ""
                        typer.echo(f"  {k}: {v.get('status')}{stale}")
                else:
                    typer.echo("No loop data in heartbeat")
            except Exception as e:
                typer.echo(f"Heartbeat read error: {e}")
        else:
            typer.echo("No heartbeat file; runtime loop state unknown.")
        burn_in = getattr(config, "burn_in", None)
        if burn_in and getattr(burn_in, "burn_in_enabled", False):
            typer.echo(f"Burn-in: enabled  phase={getattr(burn_in, 'burn_in_phase', 'testnet')}")
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
        typer.echo(f"Phase: {getattr(burn, 'burn_in_phase', 'testnet')}")
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
        hb_path = heartbeat_path or Path("artifacts/heartbeat.json")
        burn = getattr(config, "burn_in", None)
        phase = getattr(burn, "burn_in_phase", "testnet") if burn else "testnet"
        from src.validation.readiness import compute_readiness
        from src.config.versioning import get_active_config_id
        config_id = get_active_config_id(config.database_path)
        result = compute_readiness(db, heartbeat_path=hb_path, config_id=config_id, window_hours=window_hours, burn_in_phase=phase)
        typer.echo(f"Readiness: {result.classification}")
        typer.echo(f"Message: {result.message}")
        for k, v in result.details.items():
            typer.echo(f"  {k}: {v}")
        out_dir = output_dir or Path("artifacts/burnin")
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
