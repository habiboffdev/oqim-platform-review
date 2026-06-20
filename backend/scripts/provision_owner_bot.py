"""Ops wrapper for the owner-bot provisioning FEATURE.

The feature itself lives in app.modules.telegram_control_bot.provisioner
(`build_workspace_provisioner` + `BotFatherProvisioner.provision`) and is
exposed to workspaces via POST /agent-control/owner-bot/provision. This script
just invokes the same entrypoint from a shell when no authenticated client is
handy (pilot ops).

    .venv/bin/python scripts/provision_owner_bot.py --workspace-id 1 \
        [--name "..."] [--username oqim_x_bot] [--pfp-url https://...] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import async_session
from app.models.workspace import Workspace
from app.modules.telegram_control_bot.provisioner import build_workspace_provisioner


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--pfp-url", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    async with async_session() as session:
        workspace = await session.get(Workspace, args.workspace_id)
        if workspace is None:
            print(f"workspace {args.workspace_id} not found", file=sys.stderr)
            return 1
        provisioner = build_workspace_provisioner(workspace_id=workspace.id)
        try:
            result = await provisioner.provision(
                workspace=workspace,
                display_name=args.name,
                username=args.username,
                pfp_url=args.pfp_url,
                force=args.force,
            )
        except Exception as exc:
            print(f"PROVISION FAILED: {exc}", file=sys.stderr)
            return 2
        await session.commit()

    print(f"bot: @{result.bot_username}  (https://t.me/{result.bot_username})")
    print(f"owner chat bound: {result.owner_chat_bound}")
    print(f"polished (name/desc/about): {result.polished}")
    print(f"pfp set: {result.pfp_set}")
    print("--- transcript ---")
    for line in result.transcript:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
