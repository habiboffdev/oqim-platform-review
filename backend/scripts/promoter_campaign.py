"""Guided campaign creation script (no sending).

Usage:
    cd backend
    .venv/bin/python scripts/promoter_campaign.py

The script prompts for workspace_id, connection_id, campaign name, goal, and
base message, shows personalized sample openers, then asks for confirmation
before persisting the campaign + targets.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import async_session
from app.modules.bi_promoter.contracts import SegmentSpec
from app.modules.bi_promoter.script_ops import (
    approve_and_start,
    create_and_materialize,
    preview_samples,
)
from app.modules.crm_connector.amocrm import AmoCrmProvider


async def _main() -> None:
    print("=== OQIM Promoter — Campaign Creator (no sending) ===")  # noqa: T201

    workspace_id = int(input("Workspace ID: ").strip())
    connection_id = int(input("CRM Connection ID: ").strip())
    name = input("Campaign name: ").strip()
    goal = input("Goal (e.g. reactivate / upsell): ").strip() or "reactivate"
    base_message = input("Base message (Uzbek): ").strip()

    segment = SegmentSpec()  # default: all contacts

    async with async_session() as session:
        provider = AmoCrmProvider()

        print("\nFetching sample contacts and personalizing openers…")  # noqa: T201
        samples = await preview_samples(
            session, provider=provider, workspace_id=workspace_id,
            connection_id=connection_id, base_message=base_message,
            segment=segment, limit=3)

        if not samples:
            print("No reachable contacts found for this connection.")  # noqa: T201
            return

        print(f"\n--- Sample openers ({len(samples)} shown) ---")  # noqa: T201
        for i, s in enumerate(samples, 1):
            print(f"  {i}. {s['name']} ({s['phone']})")  # noqa: T201
            print(f"     → {s['opener']}")  # noqa: T201

        confirm = input("\nCreate and materialize campaign? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")  # noqa: T201
            return

        campaign = await create_and_materialize(
            session, provider=provider, workspace_id=workspace_id,
            connection_id=connection_id, name=name, goal=goal,
            base_message=base_message, segment=segment)

        # Re-open session to count targets (commit already closed the savepoint)
        async with async_session() as count_session:
            from sqlalchemy import func, select

            from app.models.outreach import OutreachTarget

            target_count = (await count_session.execute(
                select(func.count()).select_from(OutreachTarget)
                .where(OutreachTarget.campaign_id == campaign.id)
            )).scalar_one()

        print(f"\nDone. Campaign ID={campaign.id}, targets materialized={target_count}")  # noqa: T201

        start_now = input("\nStart campaign now (approve)? [y/N] ").strip().lower()
        if start_now in ("y", "yes"):
            async with async_session() as start_session:
                started = await approve_and_start(
                    start_session, workspace_id=workspace_id, campaign_id=campaign.id)
            print(f"Campaign ID={started.id} is now running (approved_at={started.approved_at}).")  # noqa: T201
        else:
            print("Campaign left as 'draft'. Run approve_and_start() when ready to send.")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(_main())
