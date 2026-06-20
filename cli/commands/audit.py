"""Audit commands for runtime proof readiness."""

from __future__ import annotations

import json

import typer

from cli.output import header, status_line, table
from cli.runtime_audit import runtime_audit_report

app = typer.Typer(no_args_is_help=True)


@app.command(name="runtime")
def runtime(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Report runtime proof status against the active architecture contract."""
    report = runtime_audit_report()

    if json_mode:
        typer.echo(json.dumps(report, indent=2))
        raise typer.Exit(0 if report["passed"] else 1)

    header("OQIM — Runtime Audit")
    summary = report["summary"]
    status_line(
        "runtime_audit",
        report["passed"],
        (
            f"{summary['implemented']}/{summary['total']} implemented, "
            f"{summary['partial']} partial, {summary['target']} target"
        ),
    )

    typer.echo("")
    table(
        ["plane", "level", "status", "command", "remaining_gap"],
        [
            [
                gate["plane"],
                gate["level"],
                gate["status"],
                gate["command"],
                gate["remaining_gap"],
            ]
            for gate in report["gates"]
        ],
    )

    typer.echo("")
    typer.echo("  Next actions")
    for action in report["next_actions"]:
        typer.echo(f"  - {action}")

    raise typer.Exit(0 if report["passed"] else 1)
