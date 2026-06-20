"""Seed and clean a live-browser app capability smoke fixture.

This is intentionally not a production route. The CLI uses it to create one
small, authenticated seller workspace in the local database, then Playwright
verifies the real browser can open the app against live services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, or_, select, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import hash_password
from app.db.session import async_session
from app.models.action_runtime import ActionRuntime
from app.models.agent import Agent
from app.models.commercial_spine import (
    BusinessBrainFactRecord,
    BusinessBrainIndexRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
    CommercialEventRecord,
    LLMGatewayTraceRecord,
)
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.learning_signal import LearningSignal
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.models.message_insight import MessageInsight
from app.models.seller_agent_reply import SellerAgentReply
from app.models.seller_agent_reply_action import SellerAgentReplyAction
from app.models.seller_agent_workspace_runtime import SellerAgentWorkspaceRuntime
from app.models.telegram_session import TelegramSession
from app.models.workspace import Workspace

SMOKE_PASSWORD = "SmokePass123!"


def _phone_for_run() -> str:
    # Keep the phone valid for auth validation while avoiding parallel smoke collisions.
    return f"+1999{secrets.randbelow(10_000_000_000):010d}"


async def seed(*, telegram_connected: bool = False, admin: bool = False) -> dict:
    run_id = secrets.token_hex(4)
    now = datetime.now(UTC)
    phone = _phone_for_run()
    telegram_user_id = int(f"99{secrets.randbelow(10_000_000_000):010d}")

    async with async_session() as session:
        workspace = Workspace(
            phone_number=phone,
            name=f"OQIM Smoke {'Admin' if admin else 'Seller'} {run_id}",
            password_hash=hash_password(SMOKE_PASSWORD),
            type="internal" if admin else "ecommerce",
            monthly_revenue_band="smoke",
            subscription_tier="admin" if admin else "free",
            telegram_connected=telegram_connected,
            telegram_user_id=telegram_user_id if telegram_connected else None,
            onboarding_completed=True,
        )
        session.add(workspace)
        await session.flush()

        agent = Agent(
            workspace_id=workspace.id,
            name="Smoke AI Seller",
            is_active=True,
            is_default=True,
            agent_type="customer",
            contact_scope="business",
            trust_mode="draft",
            persona={"role": "Sales assistant", "tone": "Friendly"},
            tools_config={"enabled_tools": []},
            knowledge_config={"use_catalog": True, "use_knowledge": True},
        )
        customer = Customer(
            workspace_id=workspace.id,
            channel="telegram_dm",
            external_id=f"smoke-customer-{run_id}",
            display_name="Smoke Customer",
            contact_type="customer",
            classification_confidence=0.98,
            classification_corrected=True,
        )
        session.add_all([agent, customer])
        await session.flush()

        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            external_chat_id=f"smoke-chat-{run_id}",
            pipeline_stage="new",
            message_sequence=2,
            message_revision=2,
            last_message_at=now,
            summary="Smoke latest seller reply",
        )
        session.add(conversation)
        await session.flush()

        first_message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="Smoke salom, iPhone bormi?",
            is_read=True,
            external_message_id=f"smoke-in-{run_id}",
            telegram_timestamp=now - timedelta(minutes=2),
            created_at=now - timedelta(minutes=2),
            conversation_seq=1,
        )
        latest_message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="seller",
            content="Smoke ha, 128GB bor.",
            is_read=True,
            external_message_id=f"smoke-out-{run_id}",
            telegram_timestamp=now,
            created_at=now,
            delivery_state="confirmed",
            conversation_seq=2,
        )
        session.add_all([first_message, latest_message])
        await session.commit()

        return {
            "workspace_id": workspace.id,
            "phone": phone,
            "password": SMOKE_PASSWORD,
            "admin": admin,
            "telegram_connected": telegram_connected,
            "telegram_user_id": telegram_user_id if telegram_connected else None,
            "conversation_id": conversation.id,
            "customer_name": customer.display_name,
            "first_message": first_message.content,
            "latest_message": latest_message.content,
        }


async def _cleanup_once(workspace_id: int) -> dict:
    async with async_session() as session:
        conversation_ids = (
            await session.scalars(
                select(Conversation.id).where(Conversation.workspace_id == workspace_id)
            )
        ).all()
        customer_ids = (
            await session.scalars(
                select(Customer.id).where(Customer.workspace_id == workspace_id)
            )
        ).all()

        if conversation_ids:
            message_ids = (
                await session.scalars(
                    select(Message.id).where(Message.conversation_id.in_(conversation_ids))
                )
            ).all()
            reply_ids = (
                await session.scalars(
                    select(SellerAgentReply.id).where(
                        SellerAgentReply.conversation_id.in_(conversation_ids)
                    )
                )
            ).all()
            if reply_ids:
                await session.execute(
                    delete(SellerAgentReplyAction).where(
                        SellerAgentReplyAction.ai_reply_id.in_(reply_ids)
                    )
                )
            await session.execute(
                delete(ConversationTurnSession).where(
                    ConversationTurnSession.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ActionRuntime).where(ActionRuntime.conversation_id.in_(conversation_ids))
            )
            await session.execute(
                delete(DeliveryRuntime).where(
                    DeliveryRuntime.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(MediaRuntime).where(MediaRuntime.conversation_id.in_(conversation_ids))
            )
            await session.execute(
                delete(MessageInsight).where(
                    MessageInsight.conversation_id.in_(conversation_ids)
                )
            )
            legacy_journey_events = await session.scalar(
                text("SELECT to_regclass('public.customer_journey_events')")
            )
            if legacy_journey_events:
                await session.execute(
                    text(
                        """
                        DELETE FROM customer_journey_events
                        WHERE conversation_id = ANY(:conversation_ids)
                        """
                    ),
                    {"conversation_ids": list(conversation_ids)},
                )
            if message_ids:
                # Background action workers can create rows after the first cleanup pass.
                legacy_follow_up_events = await session.scalar(
                    text("SELECT to_regclass('public.follow_up_events')")
                )
                if legacy_follow_up_events:
                    await session.execute(
                        text(
                            """
                            DELETE FROM follow_up_events
                            WHERE workspace_id = :workspace_id
                            """
                        ),
                        {"workspace_id": workspace_id},
                    )
                await session.execute(
                    delete(ActionRuntime).where(ActionRuntime.message_id.in_(message_ids))
                )
                await session.execute(
                    delete(DeliveryRuntime).where(DeliveryRuntime.message_id.in_(message_ids))
                )
                await session.execute(
                    delete(MediaRuntime).where(MediaRuntime.message_id.in_(message_ids))
                )
                await session.execute(
                    delete(MessageInsight).where(MessageInsight.message_id.in_(message_ids))
                )
                await session.execute(
                    delete(ConversationTurnSession).where(
                        or_(
                            ConversationTurnSession.first_customer_message_id.in_(message_ids),
                            ConversationTurnSession.latest_customer_message_id.in_(message_ids),
                        )
                    )
                )
            await session.execute(
                delete(SellerAgentReply).where(
                    SellerAgentReply.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(Message).where(Message.conversation_id.in_(conversation_ids))
            )
            await session.execute(
                delete(Conversation).where(Conversation.id.in_(conversation_ids))
            )
        await session.execute(
            delete(LearningSignal).where(LearningSignal.workspace_id == workspace_id)
        )
        legacy_training_data = await session.scalar(
            text("SELECT to_regclass('public.ai_training_data')")
        )
        if legacy_training_data:
            await session.execute(
                text(
                    """
                    DELETE FROM ai_training_data
                    WHERE workspace_id = :workspace_id
                    """
                ),
                {"workspace_id": workspace_id},
            )
        await session.execute(
            delete(BusinessBrainIndexRecord).where(
                BusinessBrainIndexRecord.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(BusinessBrainUpdateRecord).where(
                BusinessBrainUpdateRecord.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(BusinessBrainFactRecord).where(
                BusinessBrainFactRecord.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(LLMGatewayTraceRecord).where(
                LLMGatewayTraceRecord.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(CommercialEventRecord).where(
                CommercialEventRecord.workspace_id == workspace_id
            )
        )
        legacy_voice_profiles = await session.scalar(
            text("SELECT to_regclass('public.voice_profiles')")
        )
        if legacy_voice_profiles:
            await session.execute(
                text("DELETE FROM voice_profiles WHERE workspace_id = :workspace_id"),
                {"workspace_id": workspace_id},
            )
        await session.execute(
            delete(SellerAgentWorkspaceRuntime).where(
                SellerAgentWorkspaceRuntime.workspace_id == workspace_id
            )
        )
        await session.execute(delete(TelegramSession).where(TelegramSession.workspace_id == workspace_id))
        if customer_ids:
            await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
        await session.execute(delete(Agent).where(Agent.workspace_id == workspace_id))
        result = await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await session.commit()

        return {
            "workspace_id": workspace_id,
            "deleted_workspace_rows": result.rowcount or 0,
        }


async def cleanup(workspace_id: int) -> dict:
    last_error = ""
    for attempt in range(1, 6):
        try:
            return await _cleanup_once(workspace_id)
        except IntegrityError as exc:
            last_error = str(exc)
            # Action workers can still attach rows to the seeded messages while
            # the browser smoke is ending. Retry from a fresh transaction.
            await asyncio.sleep(0.25 * attempt)
    raise RuntimeError(f"fixture cleanup failed after retries: {last_error}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--telegram-connected", action="store_true")
    seed_parser.add_argument("--admin", action="store_true")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--workspace-id", type=int, required=True)
    args = parser.parse_args()

    if args.command == "seed":
        payload = await seed(telegram_connected=args.telegram_connected, admin=args.admin)
    else:
        payload = await cleanup(args.workspace_id)
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
