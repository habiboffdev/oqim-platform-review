import sys

import typer

import cli.agentio as agentio
import cli.remote as remote
from cli.commands import agent_cmd, ai, audit, crm_cmd, db, deploy, dev, eval_cmd, metrics_cmd, mock_cmd, test_cmd

app = typer.Typer(
    name="oqim",
    help="OQIM Business CLI — dev, test, deploy, debug",
    no_args_is_help=True,
)


@app.callback()
def _root(
    ctx: typer.Context,
    prod: bool = typer.Option(False, "--prod", help="Run this command on the prod VM over SSH"),
    json_mode: bool = typer.Option(False, "--json", help="Emit compact JSON instead of terse text"),
):
    """OQIM Business CLI — dev, test, deploy, debug."""
    if json_mode:
        agentio.OUTPUT_JSON = True
    if prod:
        code = remote.bridge_to_prod(remote.remote_argv(sys.argv[1:]))
        raise typer.Exit(code)


app.add_typer(agent_cmd.app, name="agent", help="Inspect agent conversations (read-only)")
app.add_typer(dev.app, name="dev", help="Dev lifecycle — start, stop, status, logs")
app.add_typer(db.app, name="db", help="Database snapshots, reset, migrate")
app.add_typer(mock_cmd.app, name="mock", help="Mock helpers — append canonical EventSpine test messages")
app.add_typer(test_cmd.app, name="test", help="Run tests, evals, and checks")
app.add_typer(eval_cmd.app, name="eval", help="Run product and AI evaluation suites")
app.add_typer(audit.app, name="audit", help="Audit proof readiness and runtime gaps")
app.add_typer(deploy.app, name="deploy", help="Deploy — push, logs, ssh, restart, status")
app.add_typer(ai.app, name="ai", help="AI debugging — reply, search, voice, prepass, classify, sandbox")
app.add_typer(crm_cmd.app, name="crm", help="Inspect + refresh the amoCRM schema (no reconnect)")


app.command("metrics")(metrics_cmd.metrics)
app.command("push")(deploy.push)


@app.command("check")
def check(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Quick local health check for the OQIM stack."""
    dev.status(json_mode=json_mode)
