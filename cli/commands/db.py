"""oqim db — database snapshots, reset, nuke, migrate."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import typer

from cli.config import BACKEND_DIR, DOCKER_CONTAINERS, PORTS, SNAPSHOTS_DIR
from cli.output import header, print_result, status_line, table

app = typer.Typer(no_args_is_help=True)

PG = DOCKER_CONTAINERS["postgres"]
RD = DOCKER_CONTAINERS["redis"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _is_port_open(port: int) -> bool:
    """Return True if something is listening on localhost:{port}."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _run(cmd: str, check: bool = True, input_file: Path | None = None) -> subprocess.CompletedProcess:
    """Run a shell command, optionally feeding a file to stdin."""
    if input_file:
        with open(input_file, "rb") as fh:
            return subprocess.run(
                cmd, shell=True, stdin=fh, check=check,
                capture_output=False,
            )
    return subprocess.run(cmd, shell=True, check=check)


def _confirm(message: str, yes: bool) -> bool:
    """Return True if confirmed (either via --yes flag or interactive prompt)."""
    if yes:
        return True
    return typer.confirm(f"  {message}")


def _snapshot_dir(name: str) -> Path:
    return SNAPSHOTS_DIR / name


def _meta_path(name: str) -> Path:
    return _snapshot_dir(name) / "meta.json"


def _read_meta(name: str) -> dict | None:
    p = _meta_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _ensure_snapshots_gitignore():
    """Add .snapshots/ to .gitignore at project root if not already present."""
    gitignore = SNAPSHOTS_DIR.parent / ".gitignore"
    if not gitignore.exists():
        return
    contents = gitignore.read_text()
    if ".snapshots/" not in contents and ".snapshots" not in contents:
        with open(gitignore, "a") as f:
            f.write("\n.snapshots/\n")


# ---------------------------------------------------------------------------
# oqim db save <name>
# ---------------------------------------------------------------------------

@app.command()
def save(
    name: str = typer.Argument(..., help="Snapshot name (e.g. 'before-onboarding')"),
):
    """Snapshot database + Redis state to .snapshots/<name>/."""
    snap = _snapshot_dir(name)

    if snap.exists():
        typer.echo(typer.style(f"  x  Snapshot '{name}' already exists. Delete it first or use a different name.", fg=typer.colors.RED))
        raise typer.Exit(1)

    snap.mkdir(parents=True)
    _ensure_snapshots_gitignore()
    typer.echo(f"\n  Saving snapshot '{name}'...")

    # --- DB dump ---
    db_dump = snap / "db.dump"
    typer.echo("  Dumping PostgreSQL...")
    result = subprocess.run(
        f"docker exec {PG} pg_dump -Fc -U postgres oqim_business",
        shell=True, capture_output=True,
    )
    if result.returncode != 0:
        typer.echo(typer.style(f"  x  pg_dump failed: {result.stderr.decode()}", fg=typer.colors.RED))
        raise typer.Exit(1)
    db_dump.write_bytes(result.stdout)
    typer.echo(typer.style(f"  +  DB dump: {db_dump.stat().st_size:,} bytes", fg=typer.colors.GREEN))

    # --- Redis db=0 ---
    typer.echo("  Saving Redis db=0...")
    subprocess.run(f"docker exec {RD} redis-cli -n 0 BGSAVE", shell=True, check=True, capture_output=True)
    time.sleep(2)
    subprocess.run(
        f"docker cp {RD}:/data/dump.rdb {snap}/redis-0.rdb",
        shell=True, check=True,
    )
    typer.echo(typer.style("  +  Redis db=0 saved", fg=typer.colors.GREEN))

    # --- Redis db=1 (legacy auxiliary state) ---
    typer.echo("  Saving Redis db=1 (legacy auxiliary state)...")
    subprocess.run(f"docker exec {RD} redis-cli -n 1 BGSAVE", shell=True, check=True, capture_output=True)
    time.sleep(2)
    subprocess.run(
        f"docker cp {RD}:/data/dump.rdb {snap}/redis-1.rdb",
        shell=True, check=True,
    )
    typer.echo(typer.style("  +  Redis db=1 saved", fg=typer.colors.GREEN))

    # --- Metadata ---
    meta = {
        "name": name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "db_size_bytes": db_dump.stat().st_size,
    }
    _meta_path(name).write_text(json.dumps(meta, indent=2))

    typer.echo(typer.style(f"\n  Snapshot '{name}' saved.", fg=typer.colors.GREEN))


# ---------------------------------------------------------------------------
# oqim db load <name>
# ---------------------------------------------------------------------------

@app.command()
def load(
    name: str = typer.Argument(..., help="Snapshot name to restore"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Restore database + Redis state from .snapshots/<name>/."""
    snap = _snapshot_dir(name)
    if not snap.exists():
        typer.echo(typer.style(f"  x  Snapshot '{name}' not found.", fg=typer.colors.RED))
        raise typer.Exit(1)

    meta = _read_meta(name)
    db_dump = snap / "db.dump"
    redis0 = snap / "redis-0.rdb"
    redis1 = snap / "redis-1.rdb"

    for f in [db_dump, redis0, redis1]:
        if not f.exists():
            typer.echo(typer.style(f"  x  Missing file: {f.name}", fg=typer.colors.RED))
            raise typer.Exit(1)

    # Warn if services are running
    backend_up = _is_port_open(PORTS["backend"])
    gramjs_up = _is_port_open(PORTS["gramjs"])
    if backend_up or gramjs_up:
        services = ", ".join(
            s for s, up in [("backend", backend_up), ("gramjs", gramjs_up)] if up
        )
        typer.echo(typer.style(
            f"\n  ! Services running ({services}). Run 'oqim dev stop' first to avoid state conflicts.",
            fg=typer.colors.YELLOW,
        ))

    # Confirmation
    saved_at = meta.get("timestamp", "?") if meta else "?"
    sha = meta.get("git_sha", "?") if meta else "?"
    typer.echo(f"\n  Loading snapshot '{name}' (saved {saved_at}, commit {sha})")
    if not _confirm("This will OVERWRITE current DB and Redis. Continue?", yes):
        typer.echo("  Aborted.")
        raise typer.Exit(0)

    # --- DB restore ---
    typer.echo("  Restoring PostgreSQL...")
    with open(db_dump, "rb") as fh:
        result = subprocess.run(
            f"docker exec -i {PG} pg_restore --clean --if-exists -U postgres -d oqim_business",
            shell=True, stdin=fh,
        )
    if result.returncode != 0:
        typer.echo(typer.style("  x  pg_restore failed (non-zero exit). Some errors are normal.", fg=typer.colors.YELLOW))
    else:
        typer.echo(typer.style("  +  PostgreSQL restored", fg=typer.colors.GREEN))

    # --- Redis db=0 ---
    typer.echo("  Restoring Redis db=0...")
    subprocess.run(f"docker exec {RD} redis-cli -n 0 FLUSHDB", shell=True, check=True, capture_output=True)
    subprocess.run(f"docker cp {snap}/redis-0.rdb {RD}:/data/dump.rdb", shell=True, check=True)
    subprocess.run(f"docker exec {RD} redis-cli DEBUG RELOAD", shell=True, check=True, capture_output=True)
    typer.echo(typer.style("  +  Redis db=0 restored", fg=typer.colors.GREEN))

    # --- Redis db=1 ---
    typer.echo("  Restoring Redis db=1 (legacy auxiliary state)...")
    subprocess.run(f"docker exec {RD} redis-cli -n 1 FLUSHDB", shell=True, check=True, capture_output=True)
    subprocess.run(f"docker cp {snap}/redis-1.rdb {RD}:/data/dump.rdb", shell=True, check=True)
    subprocess.run(f"docker exec {RD} redis-cli DEBUG RELOAD", shell=True, check=True, capture_output=True)
    typer.echo(typer.style("  +  Redis db=1 restored", fg=typer.colors.GREEN))

    typer.echo(typer.style(f"\n  Loaded snapshot '{name}' (saved {saved_at}, commit {sha})", fg=typer.colors.GREEN))


# ---------------------------------------------------------------------------
# oqim db list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_snapshots(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """List available snapshots with sizes and dates."""
    if not SNAPSHOTS_DIR.exists():
        if json_mode:
            typer.echo("[]")
        else:
            typer.echo("  No snapshots yet. Run 'oqim db save <name>' to create one.")
        return

    snapshots = sorted(SNAPSHOTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    json_rows = []

    for snap in snapshots:
        if not snap.is_dir():
            continue
        meta = _read_meta(snap.name)
        if meta is None:
            continue
        size_mb = f"{meta.get('db_size_bytes', 0) / 1_000_000:.1f} MB"
        ts = meta.get("timestamp", "?")
        sha = meta.get("git_sha", "?")
        rows.append([snap.name, ts, sha, size_mb])
        json_rows.append({"name": snap.name, "timestamp": ts, "git_sha": sha, "db_size": size_mb})

    if not rows:
        if json_mode:
            typer.echo("[]")
        else:
            typer.echo("  No snapshots found.")
        return

    if json_mode:
        typer.echo(json.dumps(json_rows, indent=2))
    else:
        header("Database Snapshots")
        table(["Name", "Saved At", "Git SHA", "DB Size"], rows)


# ---------------------------------------------------------------------------
# oqim db reset
# ---------------------------------------------------------------------------

@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Clear user data, keep workspace. Runs exact SQL from spec."""
    typer.echo(typer.style("\n  ! This will delete all messages, customers, conversations, AI replies, voice profiles,", fg=typer.colors.YELLOW))
    typer.echo(typer.style("    draft actions, and learning signals. Workspace rows are kept but reset.", fg=typer.colors.YELLOW))

    if not _confirm("Proceed with reset?", yes):
        typer.echo("  Aborted.")
        raise typer.Exit(0)

    sql = (
        "DELETE FROM conversation_pairs; "
        "DELETE FROM learning_signals; "
        "DELETE FROM draft_actions; "
        "DELETE FROM ai_replies; "
        "DELETE FROM voice_profiles; "
        "DELETE FROM catalog_item_images; "
        "DELETE FROM catalog_items; "
        "DELETE FROM business_knowledge; "
        "DELETE FROM messages; "
        "DELETE FROM conversations; "
        "DELETE FROM customers; "
        "UPDATE workspaces SET onboarding_completed = false, corrections_since_refresh = 0;"
    )

    typer.echo("  Resetting database...")
    result = subprocess.run(
        f'docker exec {PG} psql -U postgres -d oqim_business -c "{sql}"',
        shell=True,
    )
    if result.returncode != 0:
        typer.echo(typer.style("  x  SQL reset failed.", fg=typer.colors.RED))
        raise typer.Exit(1)
    typer.echo(typer.style("  +  Database reset", fg=typer.colors.GREEN))

    # Clear ingestion:* keys from Redis db=0
    typer.echo("  Clearing Redis ingestion keys (db=0)...")
    scan_result = subprocess.run(
        f"docker exec {RD} redis-cli -n 0 --scan --pattern 'ingestion:*'",
        shell=True, capture_output=True, text=True,
    )
    keys = [k.strip() for k in scan_result.stdout.splitlines() if k.strip()]
    if keys:
        keys_str = " ".join(keys)
        subprocess.run(
            f"docker exec {RD} redis-cli -n 0 DEL {keys_str}",
            shell=True, check=True, capture_output=True,
        )
        typer.echo(typer.style(f"  +  Deleted {len(keys)} ingestion key(s)", fg=typer.colors.GREEN))
    else:
        typer.echo("  No ingestion keys found.")

    typer.echo(typer.style("\n  Reset complete.", fg=typer.colors.GREEN))


# ---------------------------------------------------------------------------
# oqim db nuke
# ---------------------------------------------------------------------------

@app.command()
def nuke(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Full drop + recreate schema + flush Redis + run migrations."""
    typer.echo(typer.style("\n  !! DANGER: This drops the entire public schema and recreates it from scratch.", fg=typer.colors.RED))
    typer.echo(typer.style("     All data will be permanently lost.", fg=typer.colors.RED))

    if not _confirm("Type 'yes' to confirm NUKE", yes):
        typer.echo("  Aborted.")
        raise typer.Exit(0)

    steps = [
        (
            "Drop + recreate public schema",
            f'docker exec {PG} psql -U postgres -d oqim_business -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"',
        ),
        (
            "Create pgvector extension",
            f'docker exec {PG} psql -U postgres -d oqim_business -c "CREATE EXTENSION IF NOT EXISTS vector;"',
        ),
        (
            "Flush Redis db=0",
            f"docker exec {RD} redis-cli -n 0 FLUSHDB",
        ),
        (
            "Flush Redis db=1",
            f"docker exec {RD} redis-cli -n 1 FLUSHDB",
        ),
    ]

    for label, cmd in steps:
        typer.echo(f"  {label}...")
        result = subprocess.run(cmd, shell=True)
        if result.returncode != 0:
            typer.echo(typer.style(f"  x  Failed: {label}", fg=typer.colors.RED))
            raise typer.Exit(1)
        typer.echo(typer.style(f"  +  {label}", fg=typer.colors.GREEN))

    # Run migrations
    typer.echo("  Running migrations (alembic upgrade head)...")
    result = subprocess.run(
        "alembic upgrade head",
        shell=True, cwd=BACKEND_DIR,
    )
    if result.returncode != 0:
        typer.echo(typer.style("  x  alembic upgrade head failed.", fg=typer.colors.RED))
        raise typer.Exit(1)
    typer.echo(typer.style("  +  Migrations applied", fg=typer.colors.GREEN))

    typer.echo(typer.style("\n  Nuke complete. Fresh database ready.", fg=typer.colors.GREEN))


# ---------------------------------------------------------------------------
# oqim db migrate
# ---------------------------------------------------------------------------

@app.command()
def migrate():
    """Run alembic upgrade head."""
    typer.echo("  Running migrations...")
    result = subprocess.run(
        "alembic upgrade head",
        shell=True, cwd=BACKEND_DIR,
    )
    if result.returncode != 0:
        typer.echo(typer.style("  x  Migration failed.", fg=typer.colors.RED))
        raise typer.Exit(1)
    typer.echo(typer.style("  +  Migrations applied successfully.", fg=typer.colors.GREEN))
