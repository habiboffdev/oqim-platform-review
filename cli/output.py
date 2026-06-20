"""Shared output helpers for human and agent consumption."""
import json
from typing import Any

import typer


def print_result(data: dict, json_mode: bool = False):
    if json_mode:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        for key, value in data.items():
            typer.echo(f"  {key}: {value}")


def status_line(name: str, ok: bool, detail: str = ""):
    symbol = "+" if ok else "x"
    color = typer.colors.GREEN if ok else typer.colors.RED
    styled = typer.style(f"  {symbol}  {name}", fg=color)
    typer.echo(f"{styled}  {detail}")


def table(headers: list[str], rows: list[list[Any]], json_mode: bool = False):
    if json_mode:
        typer.echo(json.dumps(
            [dict(zip(headers, row)) for row in rows],
            indent=2, ensure_ascii=False, default=str,
        ))
        return
    if not rows:
        typer.echo("  (no data)")
        return
    widths = [
        max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    typer.echo(f"  {header_line}")
    typer.echo(f"  {'-+-'.join('-' * w for w in widths)}")
    for row in rows:
        line = " | ".join(str(v).ljust(w) for v, w in zip(row, widths))
        typer.echo(f"  {line}")


def header(text: str):
    typer.echo(f"\n  {text}")
    typer.echo(f"  {'─' * len(text)}")
