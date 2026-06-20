"""Atomic handoff: one call -> task + owner notification, one idempotency stem.

Spec: docs/superpowers/specs/2026-06-10-conversation-state-handoff-design.md.
Replaces the fragile create_task+notify convention the model could half-do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commercial_action import CommercialActionProposalRecord
from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.modules.agent_business_actions.service import AgentBusinessActionService
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


async def _agent_session(db_session, workspace, conversation, customer, agent):
    return await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )


async def test_handoff_creates_task_and_notification_atomically(
    db_session, workspace, conversation, customer, agent
):
    session = await _agent_session(db_session, workspace, conversation, customer, agent)

    result = await AgentBusinessActionService(db_session).handoff(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-test",
        kind="lead",
        title="Kursga qiziqqan mijoz: Jasur",
        detail="Mijoz raqam qoldirdi: +998901635207",
        idempotency_key="handoff-test-key",
    )

    assert result.kind == "lead"
    assert result.task_ref
    assert result.notification_ref
    # task row exists and carries the handoff kind in source_refs
    task_row = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.workspace_id == workspace.id,
                CommercialActionProposalRecord.action_type == "create_business_task",
            )
        )
    ).scalar_one()
    assert "handoff:lead" in task_row.source_refs
    # notification projection exists, kind-tagged, linked to the task
    notification_row = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.projection_type == "owner_notification"
            )
        )
    ).scalar_one()
    assert "handoff:lead" in notification_row.source_refs
    assert notification_row.state["bot_payload"]["task_ref"] == result.task_ref


async def test_handoff_is_idempotent_on_replay(
    db_session, workspace, conversation, customer, agent
):
    session = await _agent_session(db_session, workspace, conversation, customer, agent)
    service = AgentBusinessActionService(db_session)
    kwargs = dict(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-replay",
        kind="complaint",
        title="Shikoyat: yetkazib berish",
        detail="Mijoz norozi, operator kerak",
        idempotency_key="handoff-replay-key",
    )

    first = await service.handoff(**kwargs)
    second = await service.handoff(**kwargs)

    assert first.task_ref == second.task_ref
    assert first.notification_ref == second.notification_ref
    count = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.action_type == "create_business_task"
            )
        )
    ).scalars().all()
    assert len(count) == 1


async def test_handoff_dedups_repeat_same_kind_across_turns(
    db_session, workspace, conversation, customer, agent
):
    """A weak model re-emits work.handoff on every 'still waiting' turn, each
    with a DIFFERENT idempotency_key. Without dedup these stack into permanent
    'operator busy' anchors that feed back as queued/stale handoffs (live
    output->input loop, 2026-06-13). The service must reuse the open same-kind
    handoff and create exactly ONE proposal."""
    session = await _agent_session(db_session, workspace, conversation, customer, agent)
    service = AgentBusinessActionService(db_session)
    base = dict(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        kind="lead",
        title="Kursga qiziqqan mijoz",
        detail="Mijoz raqam qoldirdi",
    )

    first = await service.handoff(
        **base, hermes_run_id="hermes_run:turn-1", idempotency_key="turn-1-key"
    )
    # Next turn: same kind + conversation, DIFFERENT idempotency_key.
    second = await service.handoff(
        **base, hermes_run_id="hermes_run:turn-2", idempotency_key="turn-2-key"
    )

    assert second.kind == "lead"
    assert second.task_ref == first.task_ref  # reused the open one, no new card
    proposals = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.action_type == "create_business_task"
            )
        )
    ).scalars().all()
    assert len(proposals) == 1


async def test_handoff_allows_different_kind_when_one_open(
    db_session, workspace, conversation, customer, agent
):
    """Dedup is per-kind: a genuinely new need (complaint) is still recorded
    even when a lead handoff is already open."""
    session = await _agent_session(db_session, workspace, conversation, customer, agent)
    service = AgentBusinessActionService(db_session)
    base = dict(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        title="t",
        detail="d",
    )
    await service.handoff(**base, kind="lead", hermes_run_id="hr:1", idempotency_key="k1")
    await service.handoff(
        **base, kind="complaint", hermes_run_id="hr:2", idempotency_key="k2"
    )
    proposals = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.action_type == "create_business_task"
            )
        )
    ).scalars().all()
    assert len(proposals) == 2


async def test_handoff_rejects_unknown_kind(
    db_session, workspace, conversation, customer, agent
):
    session = await _agent_session(db_session, workspace, conversation, customer, agent)

    with pytest.raises(ValueError, match="invalid_handoff_kind"):
        await AgentBusinessActionService(db_session).handoff(
            workspace_id=workspace.id,
            agent_session_id=session.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            customer_id=customer.id,
            hermes_run_id=None,
            kind="party",
            title="x",
            detail="y",
            idempotency_key="k",
        )


async def test_handoff_enriches_notification_with_customer_and_chat_summary(
    db_session, workspace, conversation, customer, agent
):
    """Owner UX: the notification must say WHO and summarize the chat."""
    session = await _agent_session(db_session, workspace, conversation, customer, agent)
    session.summary = "Mijoz kursga qiziqdi, narxni so'radi, raqam qoldirdi."
    await db_session.flush()

    result = await AgentBusinessActionService(db_session).handoff(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-enrich",
        kind="lead",
        title="Yangi lid: Alisher",
        detail="Raqam: +998912345678",
        idempotency_key="handoff-enrich-key",
    )

    notification_row = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.projection_ref == result.notification_ref
            )
        )
    ).scalar_one()
    bot_payload = notification_row.state["bot_payload"]
    assert bot_payload["customer_label"] == "Alisher Valiev (+998912345678)"
    assert "raqam qoldirdi" in bot_payload["chat_summary"]


async def test_handoff_merges_notification_into_the_approval_card(
    db_session, workspace, conversation, customer, agent
):
    """Founder UX call (2026-06-10): ONE bot message per handoff. The
    approval card absorbs the notification context (customer, chat summary,
    next step); the notification row stays for audit but is never sent."""
    session = await _agent_session(db_session, workspace, conversation, customer, agent)
    session.summary = "Mijoz kursga qiziqdi, raqam qoldirdi."
    await db_session.flush()

    result = await AgentBusinessActionService(db_session).handoff(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-merge",
        kind="lead",
        title="Yangi lid: Alisher",
        detail="Raqam: +998912345678",
        idempotency_key="handoff-merge-key",
    )

    # the task proposal carries the owner UX context for the card renderer
    task_row = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.idempotency_key == "handoff-merge-key:task"
            )
        )
    ).scalar_one()
    context = task_row.payload["owner_task"]["context"]
    assert context["customer_label"] == "Alisher Valiev (+998912345678)"
    assert "raqam qoldirdi" in context["chat_summary"]
    assert context["recommended_action"]

    # the notification exists for audit but will never flush as a message
    notification_row = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.projection_ref == result.notification_ref
            )
        )
    ).scalar_one()
    assert notification_row.state["status"] == "merged_into_card"
    assert result.status == "merged_into_card"


async def test_handoff_stores_agent_recorded_customer_details(
    db_session, workspace, conversation, customer, agent
):
    """The AGENT records who the customer is (it judged the chat) — the host
    only stores what the tool call says. No host-side chat parsing (founder,
    2026-06-10: deterministic extraction = anti-pattern)."""
    customer.display_name = "SATStation"  # Telegram profile name, not the real one
    customer.phone_number = None
    customer.telegram_id = 6598527321
    await db_session.flush()
    session = await _agent_session(db_session, workspace, conversation, customer, agent)

    await AgentBusinessActionService(db_session).handoff(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-details",
        kind="lead",
        title="Yangi lid: Jasur",
        detail="Mijoz kutmoqda.",
        customer_name="Jasur",
        customer_phone="+998901635207",
        idempotency_key="handoff-details-key",
    )

    task_row = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.idempotency_key == "handoff-details-key:task"
            )
        )
    ).scalar_one()
    context = task_row.payload["owner_task"]["context"]
    # agent-known name wins for display; profile name stays in parentheses
    assert context["customer_label"] == "Jasur"
    assert context["customer_phone"] == "+998901635207"
    assert context["telegram_link"] == "tg://user?id=6598527321"
    # agent-recorded phone becomes customer data (host bookkeeps the record)
    await db_session.refresh(customer)
    assert customer.phone_number == "+998901635207"


async def test_handoff_link_prefers_username_over_id_mention(
    db_session, workspace, conversation, customer, agent
):
    """t.me/<username> always renders clickable; tg://user?id mentions are
    stripped by Telegram for users who never interacted with the bot."""
    customer.telegram_id = 6598527321
    customer.telegram_username = "jasur_biz"
    await db_session.flush()
    session = await _agent_session(db_session, workspace, conversation, customer, agent)

    await AgentBusinessActionService(db_session).handoff(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:handoff-link",
        kind="lead",
        title="Yangi lid",
        detail="x",
        idempotency_key="handoff-link-key",
    )

    task_row = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.idempotency_key == "handoff-link-key:task"
            )
        )
    ).scalar_one()
    assert task_row.payload["owner_task"]["context"]["telegram_link"] == "https://t.me/jasur_biz"


async def test_intake_upsert_stores_telegram_username(db_session, workspace):
    """Platform metadata from the sidecar (not chat parsing): the sender's
    @username lands on the customer row so cards can build t.me links."""
    from app.modules.conversation_core.service import _find_or_create_customer

    created = await _find_or_create_customer(
        db_session,
        workspace_id=workspace.id,
        telegram_id=777001,
        display_name="Yangi Mijoz",
        telegram_username="@yangi_mijoz",
    )
    assert created.telegram_username == "yangi_mijoz"  # @ stripped

    # existing row picks up a later/changed username
    updated = await _find_or_create_customer(
        db_session,
        workspace_id=workspace.id,
        telegram_id=777001,
        display_name="Yangi Mijoz",
        telegram_username="yangi_mijoz_2",
    )
    assert updated.id == created.id
    assert updated.telegram_username == "yangi_mijoz_2"
