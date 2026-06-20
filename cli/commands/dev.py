"""Dev lifecycle — start, stop, status, logs.

Ports all functionality from dev.sh into structured Typer commands.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time

import typer

from cli.config import (
    BACKEND_DIR,
    FRONTEND_DIR,
    LOG_DIR,
    PORTS,
    PROJECT_ROOT,
    TMUX_SESSION,
)
from cli.output import header, status_line

app = typer.Typer(no_args_is_help=True)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _backend_python() -> tuple[str, str] | None:
    candidates = (
        ("backend/.venv", BACKEND_DIR / ".venv" / "bin" / "python"),
        ("backend/venv", BACKEND_DIR / "venv" / "bin" / "python"),
    )
    for label, path in candidates:
        if path.is_file():
            return label, str(path)
    return None


def _port_in_use(port: int) -> bool:
    """Check if a port has a listening process."""
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _kill_port(port: int) -> bool:
    """Kill whatever is listening on a port. Returns True if something was killed."""
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    pids = result.stdout.strip()
    if not pids:
        return False
    for pid in pids.split("\n"):
        subprocess.run(["kill", "-9", pid.strip()], capture_output=True)
    return True


def _gcloud_adc_valid() -> bool:
    """Check if gcloud application-default credentials are valid."""
    if not shutil.which("gcloud"):
        return False
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _tmux_session_exists() -> bool:
    """Check if the tmux session is running."""
    if not shutil.which("tmux"):
        return False
    result = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True,
    )
    return result.returncode == 0


def _has_command(name: str) -> bool:
    return shutil.which(name) is not None


def _preflight(*, local: bool) -> bool:
    """Run preflight checks. Returns True if all critical checks pass."""
    header("Preflight checks")
    fail = False

    if local:
        native_infra_ok = all(
            _has_command(cmd)
            for cmd in ("postgres", "initdb", "pg_ctl", "psql", "redis-server")
        )
        status_line("native PostgreSQL/Redis", native_infra_ok)
        if not native_infra_ok:
            typer.echo("     Install: brew install postgresql@15 redis")
            fail = True
    else:
        docker_ok = subprocess.run(
            ["docker", "info"], capture_output=True
        ).returncode == 0
        status_line("Docker", docker_ok)
        if not docker_ok:
            fail = True

    # Backend venv
    backend_runtime = _backend_python()
    status_line(backend_runtime[0] if backend_runtime else "backend/.venv", bool(backend_runtime))
    if backend_runtime is None:
        fail = True

    # Frontend node_modules
    node_modules = (FRONTEND_DIR / "node_modules").is_dir()
    status_line(
        "frontend/node_modules",
        node_modules,
        "" if node_modules else "(cd frontend && npm i)",
    )
    if not node_modules:
        fail = True

    # .env
    env_file = (PROJECT_ROOT / ".env").is_file()
    status_line(".env", env_file)
    if not env_file:
        fail = True

    # gcloud ADC (warning only)
    adc_ok = _gcloud_adc_valid()
    if not adc_ok:
        typer.echo(
            typer.style("  !  gcloud ADC", fg=typer.colors.YELLOW)
            + "  expired — AI brain won't work"
        )
        typer.echo("     Run: gcloud auth application-default login")

    if fail:
        typer.echo("\n  Fix the issues above.")
    else:
        typer.echo("\n  All checks passed.")

    return not fail


def _ensure_backend_path() -> None:
    """Add backend directory to sys.path so app.* imports work."""
    backend_str = str(BACKEND_DIR)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


async def _event_spine_status(redis_url: str) -> dict:
    """Fetch event spine divergence counters + publish_failures via the shared loader."""
    _ensure_backend_path()
    try:
        import redis.asyncio as aioredis
        from app.services.runtime_signals import load_event_spine_signals

        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            signals = await load_event_spine_signals(r)
            return {
                "publish_failures": signals.publish_failures,
                "divergences": signals.global_divergences,
                "status": signals.status,
                "error": signals.error,
            }
        finally:
            await r.aclose()
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)}


def _get_event_spine_status(redis_port: int) -> dict:
    """Synchronous wrapper for _event_spine_status."""
    redis_url = f"redis://localhost:{redis_port}/0"
    return asyncio.run(_event_spine_status(redis_url))


# ── Commands ─────────────────────────────────────────────────────────────────


@app.command()
def start(
    mock: bool = typer.Option(
        False, "--mock", help="Reserved for legacy mock mode; GramJS is the active Telegram adapter"
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Run PostgreSQL/Redis/backend/frontend/GramJS natively; no Docker.",
    ),
):
    """Start all OQIM services in a tmux session."""
    if not shutil.which("tmux"):
        typer.echo("  tmux is required. Install: brew install tmux")
        raise typer.Exit(1)

    # Preflight
    if not _preflight(local=local):
        raise typer.Exit(1)

    # Kill existing tmux session + stale port processes for a clean start
    if _tmux_session_exists():
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION], capture_output=True)
        typer.echo(f"  Killed previous '{TMUX_SESSION}' session.")
        time.sleep(1)

    for name in ("backend", "frontend", "gramjs", "postgres", "redis"):
        port = PORTS[name]
        if _kill_port(port):
            typer.echo(f"  Killed stale process on port {port} ({name})")

    # Clear old logs
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for log_name in ("infra", "docker", "postgres", "api", "gramjs", "web"):
        (LOG_DIR / f"{log_name}.log").write_text("")

    root = str(PROJECT_ROOT)

    # Create tmux session
    subprocess.run([
        "tmux", "new-session", "-d", "-s", TMUX_SESSION,
        "-n", "infra", "-x", "220", "-y", "50",
    ])

    # Window 1: Infra
    if local:
        subprocess.run([
            "tmux", "send-keys", "-t", f"{TMUX_SESSION}:infra",
            f"cd '{root}' && bash scripts/local-infra.sh 2>&1 | tee '{LOG_DIR}/infra.log'",
            "Enter",
        ])
    else:
        subprocess.run([
            "tmux", "send-keys", "-t", f"{TMUX_SESSION}:infra",
            f"cd '{root}' && docker compose up 2>&1 | tee '{LOG_DIR}/docker.log'",
            "Enter",
        ])

    # Window 2: Backend API
    backend_runtime = _backend_python()
    if backend_runtime is None:
        raise RuntimeError("Backend Python runtime not found")
    _, backend_python = backend_runtime
    subprocess.run([
        "tmux", "new-window", "-t", TMUX_SESSION, "-n", "api",
    ])
    subprocess.run([
        "tmux", "send-keys", "-t", f"{TMUX_SESSION}:api",
        f"cd '{root}/backend' && set -a && source ../.env && set +a && '{backend_python}' -m uvicorn app.main:app --host 0.0.0.0 --port 8001 2>&1 | tee '{LOG_DIR}/api.log'",
        "Enter",
    ])

    if local:
        subprocess.run([
            "tmux", "new-window", "-t", TMUX_SESSION, "-n", "gramjs",
        ])
        subprocess.run([
            "tmux", "send-keys", "-t", f"{TMUX_SESSION}:gramjs",
            (
                f"cd '{root}/gramjs-sidecar' && "
                "set -a && source ../.env && set +a && "
                "export DATABASE_URL='postgresql://postgres:postgres@localhost:5434/oqim_business' && "
                "export BACKEND_CALLBACK_URL='http://localhost:8001' && "
                "export SIDECAR_PORT='3100' && "
                "npm start 2>&1 | tee '../.dev-logs/gramjs.log'"
            ),
            "Enter",
        ])

    # Telegram transport is handled by the GramJS sidecar process.

    # Window 4: Frontend
    subprocess.run([
        "tmux", "new-window", "-t", TMUX_SESSION, "-n", "web",
    ])
    subprocess.run([
        "tmux", "send-keys", "-t", f"{TMUX_SESSION}:web",
        f"cd '{root}/frontend' && npm run dev 2>&1 | tee '{LOG_DIR}/web.log'",
        "Enter",
    ])

    # Window 5: Status
    subprocess.run([
        "tmux", "new-window", "-t", TMUX_SESSION, "-n", "status",
    ])
    subprocess.run([
        "tmux", "send-keys", "-t", f"{TMUX_SESSION}:status",
        "while true; do clear; oqim dev status; sleep 3; done",
        "Enter",
    ])

    # Focus status window
    subprocess.run([
        "tmux", "select-window", "-t", f"{TMUX_SESSION}:status",
    ])

    typer.echo("\n  Starting OQIM Business...")
    typer.echo("  Waiting for services to boot (~5s)...")
    time.sleep(5)

    # Show status inline
    status(json_mode=False)

    typer.echo(f"\n  tmux session: '{TMUX_SESSION}'")
    typer.echo(
        "  Windows: infra | api | gramjs | web | status"
        if local
        else "  Windows: infra | api | web | status"
    )
    typer.echo("")
    typer.echo(f"  Attach:  tmux attach -t {TMUX_SESSION}")
    typer.echo("  Switch:  Ctrl+B, then window number (1-5)")
    typer.echo("  Stop:    oqim dev stop")
    typer.echo("  Logs:    oqim dev logs [api|gramjs|web|docker]")
    typer.echo("")
    typer.echo("  Agent-friendly: logs at .dev-logs/*.log")
    typer.echo("")

    # Attach
    os.execvp("tmux", ["tmux", "attach", "-t", TMUX_SESSION])


@app.command()
def stop():
    """Kill the tmux dev session and stale port processes."""
    killed_session = False
    if _tmux_session_exists():
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION])
        typer.echo(f"  Session '{TMUX_SESSION}' stopped.")
        killed_session = True

    # Kill stale processes on app and local infra ports
    killed_any = False
    for name in ("backend", "frontend", "gramjs", "postgres", "redis"):
        port = PORTS[name]
        if _kill_port(port):
            typer.echo(f"  Killed stale process on port {port} ({name})")
            killed_any = True

    if not killed_session and not killed_any:
        typer.echo("  Nothing running.")


@app.command()
def status(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Check which services are running."""
    services = {
        "PostgreSQL": PORTS["postgres"],
        "Redis": PORTS["redis"],
        "GramJS sidecar": PORTS["gramjs"],
        "Backend (API)": PORTS["backend"],
        "Frontend": PORTS["frontend"],
    }

    if json_mode:
        import json as json_mod

        result = {}
        for name, port in services.items():
            result[name] = {"port": port, "up": _port_in_use(port)}
        result["gcloud_adc"] = {"valid": _gcloud_adc_valid()}
        result["event_spine"] = _get_event_spine_status(PORTS["redis"])
        typer.echo(json_mod.dumps(result, indent=2))
        return

    header("OQIM Business — Service Status")
    for name, port in services.items():
        up = _port_in_use(port)
        detail = f"(port {port})" if up else f"(port {port})  <- DOWN"
        status_line(f"{name:<18}", up, detail)

    # gcloud ADC
    adc_ok = _gcloud_adc_valid()
    if adc_ok:
        status_line("gcloud ADC        ", True, "(valid)")
    else:
        status_line(
            "gcloud ADC        ",
            False,
            "<- EXPIRED (run: gcloud auth application-default login)",
        )

    # Event Spine counters
    spine = _get_event_spine_status(PORTS["redis"])
    typer.echo("")
    if spine.get("status") == "unreachable":
        typer.echo(
            f"  Event Spine:      {typer.style('unreachable', fg=typer.colors.YELLOW)}"
            f"  ({spine.get('error', '')})"
        )
    else:
        pf = spine.get("publish_failures", 0)
        pf_str = (
            typer.style(str(pf), fg=typer.colors.RED)
            if pf > 0
            else typer.style(str(pf), fg=typer.colors.GREEN)
        )
        typer.echo(f"  Event Spine:      publish_failures={pf_str}")
        for kind, val in spine.get("divergences", {}).items():
            val_str = (
                typer.style(str(val), fg=typer.colors.RED)
                if val > 0
                else typer.style(str(val), fg=typer.colors.GREEN)
            )
            typer.echo(f"    {kind:<22} {val_str}")

    typer.echo(f"\n  Logs: {LOG_DIR}/")
    typer.echo("  View: oqim dev logs [api|gramjs|web|docker]")
    typer.echo("")


@app.command()
def logs(
    service: str = typer.Argument(
        default="",
        help="Service to tail: infra, api, gramjs, web, docker (empty = all)",
    ),
):
    """Tail dev log files."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not service:
        # Tail all logs
        log_files = list(LOG_DIR.glob("*.log"))
        if not log_files:
            typer.echo("  No log files found.")
            raise typer.Exit(1)
        os.execvp("tail", ["tail", "-f"] + [str(f) for f in log_files])
    else:
        log_file = LOG_DIR / f"{service}.log"
        if not log_file.exists():
            available = [
                f.stem for f in LOG_DIR.glob("*.log")
            ]
            typer.echo(f"  Available logs: {' '.join(available) if available else '(none)'}")
            raise typer.Exit(1)
        os.execvp("tail", ["tail", "-f", str(log_file)])
