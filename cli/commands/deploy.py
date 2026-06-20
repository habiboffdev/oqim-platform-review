"""Deploy commands — push, logs, ssh, restart, status."""
import json
import os
import shutil
import subprocess
import time
from typing import Any

import typer

from cli.config import PROD_DIR, PROD_DOMAIN, PROD_HOST, PROD_USER

app = typer.Typer(no_args_is_help=True)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _remote_with_env(command: str) -> str:
    return (
        f"cd {PROD_DIR} && "
        "export $(grep -v '^#' .env.production | grep -v '^$' | xargs) && "
        f"{command}"
    )


def _run_ssh(command: str, *, capture_output: bool = False, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", f"{PROD_USER}@{PROD_HOST}", command],
        capture_output=capture_output,
        text=True,
        timeout=timeout,
        check=False,
    )


def _watch_deploy(expected_sha: str):
    """Poll GitHub Actions deploy.yml for the pushed commit until it completes or times out."""
    if not shutil.which("gh"):
        typer.echo("  gh CLI not found — check GitHub Actions manually.")
        return

    typer.echo(f"  Watching deploy.yml for {expected_sha[:7]} (max 10 min)...")
    deadline = time.time() + 600
    tracked_run_id: int | None = None

    while time.time() < deadline:
        try:
            r = subprocess.run(
                [
                    "gh", "run", "list",
                    "--workflow=deploy.yml", "--limit=10",
                    "--json", "databaseId,status,conclusion,headSha",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                time.sleep(10)
                continue

            runs = json.loads(r.stdout)
            if not runs:
                time.sleep(10)
                continue

            run = next((item for item in runs if item.get("headSha") == expected_sha), None)
            if not run:
                typer.echo("  Waiting for deploy run to appear...")
                time.sleep(10)
                continue

            run_id = run["databaseId"]
            run_status = run["status"]
            conclusion = run.get("conclusion") or ""
            tracked_run_id = run_id
            if run_status == "completed":
                if conclusion == "success":
                    typer.echo(typer.style("  Deploy succeeded.", fg=typer.colors.GREEN))
                else:
                    typer.echo(typer.style(f"  Deploy failed: {conclusion}", fg=typer.colors.RED))
                    typer.echo("  Run: oqim deploy logs backend")
                return

            typer.echo(f"  status={run_status} (run {run_id})")
        except Exception as e:
            typer.echo(f"  Error polling: {e}")

        time.sleep(10)

    if tracked_run_id is not None:
        typer.echo(typer.style(f"  Timed out waiting for run {tracked_run_id}.", fg=typer.colors.YELLOW))
    else:
        typer.echo(typer.style("  Timed out before deploy run appeared.", fg=typer.colors.YELLOW))


@app.command()
def push():
    """Direct deploy: merge current branch to main and push."""
    # Check current branch
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    deployed_sha = ""

    if branch == "main":
        typer.echo("  Already on main. Pushing...")
        result = subprocess.run(["git", "push", "origin", "main"])
        if result.returncode != 0:
            typer.echo(typer.style("  Push failed.", fg=typer.colors.RED))
            raise typer.Exit(1)
        deployed_sha = _git_head()
    else:
        typer.echo(f"  Merging {branch} → main...")
        # Checkout main, merge, push, go back
        subprocess.run(["git", "checkout", "main"], check=True)
        result = subprocess.run(["git", "merge", branch, "--no-edit"])
        if result.returncode != 0:
            typer.echo(typer.style("  Merge failed. Resolve conflicts first.", fg=typer.colors.RED))
            subprocess.run(["git", "checkout", branch])
            raise typer.Exit(1)

        result = subprocess.run(["git", "push", "origin", "main"])
        if result.returncode != 0:
            typer.echo(typer.style("  Push failed.", fg=typer.colors.RED))
            subprocess.run(["git", "checkout", branch])
            raise typer.Exit(1)
        deployed_sha = _git_head()

        # Go back to feature branch
        subprocess.run(["git", "checkout", branch])

    typer.echo(typer.style("  Pushed to main.", fg=typer.colors.GREEN))
    _watch_deploy(expected_sha=deployed_sha)


@app.command()
def pr(
    title: str = typer.Option("", "--title", "-t", help="PR title (auto-generated if empty)"),
    draft: bool = typer.Option(False, "--draft", "-d", help="Create as draft PR"),
):
    """Create a PR from current branch, run CI, then optionally merge & deploy."""
    if not shutil.which("gh"):
        typer.echo(typer.style("  gh CLI not found. Install: brew install gh", fg=typer.colors.RED))
        raise typer.Exit(1)

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    if branch == "main":
        typer.echo(typer.style("  Can't create PR from main. Switch to a feature branch.", fg=typer.colors.RED))
        raise typer.Exit(1)

    # Push branch
    typer.echo(f"  Pushing {branch}...")
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)

    # Generate title from branch name if not provided
    if not title:
        title = branch.replace("/", ": ").replace("-", " ").replace("_", " ")

    # Create PR
    typer.echo(f"  Creating PR: {title}")
    cmd = ["gh", "pr", "create", "--title", title, "--body", "Auto-created by `oqim deploy pr`", "--base", "main"]
    if draft:
        cmd.append("--draft")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # PR might already exist
        if "already exists" in result.stderr:
            typer.echo("  PR already exists.")
        else:
            typer.echo(typer.style(f"  PR creation failed: {result.stderr.strip()}", fg=typer.colors.RED))
            raise typer.Exit(1)
    else:
        pr_url = result.stdout.strip()
        typer.echo(typer.style(f"  PR created: {pr_url}", fg=typer.colors.GREEN))

    # Watch CI
    typer.echo("  Waiting for CI to start...")
    time.sleep(5)

    ci_deadline = time.time() + 600  # 10 min for CI
    while time.time() < ci_deadline:
        try:
            r = subprocess.run(
                ["gh", "pr", "checks", branch, "--json", "name,state,conclusion"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                time.sleep(10)
                continue

            checks = json.loads(r.stdout)
            if not checks:
                typer.echo("  Waiting for checks to appear...")
                time.sleep(10)
                continue

            all_done = all(c.get("state") == "COMPLETED" for c in checks)
            all_pass = all(c.get("conclusion") == "SUCCESS" for c in checks)

            if all_done:
                if all_pass:
                    typer.echo(typer.style("  CI passed.", fg=typer.colors.GREEN))
                    break
                else:
                    failed = [c["name"] for c in checks if c.get("conclusion") != "SUCCESS"]
                    typer.echo(typer.style(f"  CI failed: {', '.join(failed)}", fg=typer.colors.RED))
                    typer.echo("  Fix the failures, push again, then re-run `oqim deploy pr`.")
                    raise typer.Exit(1)

            running = [c["name"] for c in checks if c.get("state") != "COMPLETED"]
            typer.echo(f"  Running: {', '.join(running)}")
        except typer.Exit:
            raise
        except Exception as e:
            typer.echo(f"  Error: {e}")

        time.sleep(15)

    # Ask to merge
    if not typer.confirm("\n  CI passed. Merge and deploy?"):
        typer.echo("  Skipped. Merge manually when ready.")
        return

    typer.echo("  Merging PR...")
    result = subprocess.run(
        ["gh", "pr", "merge", branch, "--merge", "--delete-branch"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        typer.echo(typer.style(f"  Merge failed: {result.stderr.strip()}", fg=typer.colors.RED))
        raise typer.Exit(1)

    typer.echo(typer.style("  Merged.", fg=typer.colors.GREEN))

    # Pull main locally
    subprocess.run(["git", "checkout", "main"], check=True)
    subprocess.run(["git", "pull", "origin", "main"], check=True)

    _watch_deploy(expected_sha=_git_head())


@app.command()
def logs(
    service: str = typer.Argument("backend", help="Service name, or 'all'"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow log output"),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of tail lines"),
):
    """View prod logs via SSH."""
    cmd = _remote_with_env(f"docker compose -f docker-compose.prod.yml logs --tail={lines}")
    if service != "all":
        cmd += f" {service}"
    if follow:
        cmd += " --follow"
        os.execvp("ssh", ["ssh", f"{PROD_USER}@{PROD_HOST}", cmd])
    else:
        subprocess.run(["ssh", f"{PROD_USER}@{PROD_HOST}", cmd])


@app.command()
def ssh():
    """Open an interactive SSH session to the prod VM."""
    os.execvp("ssh", ["ssh", f"{PROD_USER}@{PROD_HOST}"])


@app.command()
def restart(
    service: str = typer.Argument(..., help="Service to restart (e.g. backend, gramjs-sidecar)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Restart a prod service without a full redeploy."""
    if not yes:
        typer.confirm(f"Restart {service} on production?", abort=True)
    subprocess.run(["ssh", f"{PROD_USER}@{PROD_HOST}", _remote_with_env(
        f"docker compose -f docker-compose.prod.yml restart {service}"
    )])


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Prod health check — API latency + disk usage."""
    import httpx

    results: dict[str, Any] = {}

    # HTTPS health check
    try:
        start = time.time()
        r = httpx.get(f"https://{PROD_DOMAIN}/health", timeout=10)
        results["api"] = {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "latency_ms": int((time.time() - start) * 1000),
        }
    except Exception as e:
        results["api"] = {"ok": False, "error": str(e)}

    # SSH disk check
    try:
        r = _run_ssh("df -h / | tail -1", capture_output=True, timeout=10)
        results["disk"] = r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        results["disk"] = "unknown"

    # Container status via docker compose on the VM
    try:
        r = _run_ssh(
            _remote_with_env("docker compose -f docker-compose.prod.yml ps --format '{{.Name}}|{{.Status}}'"),
            capture_output=True,
            timeout=20,
        )
        services: dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "|" not in line:
                continue
            name, svc_status = line.split("|", 1)
            services[name.strip()] = svc_status.strip()
        results["services"] = services if r.returncode == 0 else {}
    except Exception:
        results["services"] = {}

    if json_output:
        typer.echo(json.dumps(results, indent=2))
        return

    # Human-readable output
    api = results["api"]
    if api.get("ok"):
        typer.echo(
            typer.style(
                f"API  OK  {api['latency_ms']}ms  (HTTP {api['status_code']})",
                fg=typer.colors.GREEN,
            )
        )
    else:
        detail = api.get("error") or f"HTTP {api.get('status_code')}"
        typer.echo(typer.style(f"API  FAIL  {detail}", fg=typer.colors.RED))

    disk = results.get("disk", "unknown")
    typer.echo(f"Disk {disk}")

    services = results.get("services") or {}
    if services:
        typer.echo("Services")
        for name, svc_status in services.items():
            typer.echo(f"  {name}: {svc_status}")
