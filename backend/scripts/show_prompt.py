"""Thin ops wrapper: print the composed Hermes system prompt for an agent.

Usage (on the VM: env PYTHONPATH=/path/to/backend, source oqim.env):

    .venv/bin/python scripts/show_prompt.py --workspace-id 1
    .venv/bin/python scripts/show_prompt.py --workspace-id 1 --full

The layer math lives in app.modules.agent_runtime_v2.prompt_report (feature
code, tested); this script only loads + prints.
"""
# ruff: noqa: T201  — printing is this CLI's entire job

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import async_session
from app.models.agent import Agent
from app.modules.agent_runtime_v2.config_loader import AgentConfigLoader
from app.modules.agent_runtime_v2.prompt_report import build_prompt_report
from app.modules.agent_runtime_v2.reply_runtime import compose_hermes_system_prompt


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--agent-id", type=int, default=None, help="default: first ACTIVE agent in workspace")
    parser.add_argument("--full", action="store_true", help="also print the full composed prompt")
    args = parser.parse_args()

    async with async_session() as session:
        agent_id = args.agent_id
        if agent_id is None:
            # prefer the active agent — workspaces accumulate inactive legacy
            # agents (pilot ws 1 has two), and their AGENT.md is stale/empty
            agent_id = (
                await session.execute(
                    select(Agent.id)
                    .where(Agent.workspace_id == args.workspace_id)
                    .order_by(Agent.is_active.desc(), Agent.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if agent_id is None:
                print(f"no agents in workspace {args.workspace_id}", file=sys.stderr)
                return 1
        config = await AgentConfigLoader(session).load(
            workspace_id=args.workspace_id, agent_id=agent_id
        )

    report = build_prompt_report(config)
    print(f"workspace {report.workspace_id} · agent {report.agent_id} ({report.agent_kind})")
    print(f"composed system prompt: {report.total_chars} chars · sha {report.composed_sha256_12}")
    print()
    print(f"{'layer':<18} {'chars':>7}  {'sha12':<12}  source")
    for layer in report.layers:
        print(f"{layer.name:<18} {layer.chars:>7}  {layer.sha256_12:<12}  {layer.source}")
    if report.agent_md_sections:
        print()
        print("AGENT.md sections:")
        for heading, chars in report.agent_md_sections:
            print(f"  {chars:>6}  {heading}")
    if args.full:
        print()
        print("=" * 72)
        print(compose_hermes_system_prompt(config.agent_md, config.agent_kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
