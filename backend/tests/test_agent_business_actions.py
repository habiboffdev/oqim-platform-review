from __future__ import annotations

import pytest

from app.models.commerce_catalog import CatalogOfferRecord, CatalogProductRecord
from app.modules.action_runtime.service import ActionRuntimeService
from app.modules.agent_business_actions.service import AgentBusinessActionService
from app.modules.agent_conversation_state.service import AgentConversationStateService
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.commercial_spine.repository import CommercialSpineRepository

pytestmark = pytest.mark.asyncio


async def test_hermes_can_create_idempotent_owner_task_and_bot_ready_notification(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
) -> None:
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    state = await AgentConversationStateService(db_session).set_state(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:work-task",
        summary="Customer wants to buy one item but payment details are missing.",
        stage="checkout",
        active_intent="buy",
        selected_items=[{"item_ref": "catalog_item:any-store:starter"}],
        shown_prices=[{"amount": 40000, "currency": "UZS", "authority_ref": "offer:any-store:starter"}],
        missing_authority=["payment_details:click"],
        next_best_action="ask_owner_for_payment_details",
        source_refs=["message:customer:1"],
        idempotency_key="state:work-task",
    )
    service = AgentBusinessActionService(db_session)

    first_task = await service.create_task(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:work-task",
        conversation_state_snapshot_id=state.snapshot_id,
        task_kind="payment",
        title="Send Click payment details",
        reason="Customer chose Click, but approved payment details are missing.",
        priority="high",
        selected_item_refs=["catalog_item:any-store:starter"],
        missing_authority=["payment_details:click"],
        source_refs=["message:customer:1", "state:work-task"],
        idempotency_key="task:payment-details:click",
    )
    replayed_task = await service.create_task(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:work-task",
        conversation_state_snapshot_id=state.snapshot_id,
        task_kind="payment",
        title="duplicate should not create another task",
        reason="replay",
        source_refs=["message:customer:1"],
        idempotency_key="task:payment-details:click",
    )
    notification = await service.notify_owner(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:work-task",
        task_ref=first_task.task_ref,
        title="Customer is waiting for Click details",
        summary="Customer wants to pay by Click. Send approved details or update payment rules.",
        recommended_action="Send payment details or add approved payment authority.",
        selected_item_refs=["catalog_item:any-store:starter"],
        shown_price_refs=["offer:any-store:starter"],
        missing_authority=["payment_details:click"],
        source_refs=["message:customer:1", first_task.task_ref],
        idempotency_key="notify:owner:payment-details:click",
    )
    replayed_notification = await service.notify_owner(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:work-task",
        task_ref=first_task.task_ref,
        title="duplicate",
        summary="duplicate",
        recommended_action="duplicate",
        source_refs=["message:customer:1"],
        idempotency_key="notify:owner:payment-details:click",
    )

    owner_tasks = await ActionRuntimeService(
        CommercialSpineRepository(db_session)
    ).owner_tasks(workspace_id=workspace.id)
    stored_notification = await CommercialSpineRepository(db_session).get_projection(
        workspace_id=workspace.id,
        projection_ref=notification.notification_ref,
    )

    assert first_task.task_ref == replayed_task.task_ref
    assert first_task.proposal_id == replayed_task.proposal_id
    assert first_task.status == "proposed"
    assert first_task.payload["owner_task"]["task_kind"] == "payment"
    assert first_task.payload["owner_task"]["conversation_state_snapshot_id"] == state.snapshot_id
    assert first_task.payload["owner_task"]["missing_authority"] == ["payment_details:click"]
    assert notification.notification_ref == replayed_notification.notification_ref
    assert notification.bot_payload["task_ref"] == first_task.task_ref
    assert notification.bot_payload["customer_id"] == customer.id
    assert notification.bot_payload["missing_authority"] == ["payment_details:click"]
    assert stored_notification is not None
    assert stored_notification.projection_type == "owner_notification"
    assert stored_notification.state["bot_payload"]["recommended_action"].startswith("Send payment")
    assert len([item for item in owner_tasks.items if item.proposal_id == first_task.proposal_id]) == 1


async def test_business_action_tools_are_available_to_generic_interactive_and_action_modes(agent) -> None:
    from tools.registry import registry

    from app.modules.agent_runtime_v2.hermes.oqim_tools import register_oqim_tools

    register_oqim_tools()
    config = AgentConfig(
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        name=agent.name,
        agent_md="# Agent\nSell or support the workspace catalog.",
        trust_mode="autopilot",
        auto_send_threshold=0.85,
    )
    interactive_profile = RuntimeProfileCompiler().compile_agent(
        config=config,
        agent_kind="seller_agent",
    )
    action_profile = RuntimeProfileCompiler().compile_agent(
        config=config,
        agent_kind="seller_agent",
        execution_mode="action",
    )

    names = set(registry.get_tool_names_for_toolset("oqim"))
    assert "work.create_task" in names
    assert "owner.notify" in names
    assert "conversation.record_intelligence" in names
    assert "commerce.create_order" in names
    assert "commerce.create_checkout_intent" in names
    # Slice 3: the interactive seller no longer records handoffs or intelligence
    # itself — the post-reply records pass is the sole commercial-state recorder.
    # work.handoff / record_intelligence / task / notify are all action/setup-only.
    assert "work.handoff" not in interactive_profile.allowed_tool_names
    assert "work.create_task" not in interactive_profile.allowed_tool_names
    assert "owner.notify" not in interactive_profile.allowed_tool_names
    assert "conversation.record_intelligence" not in interactive_profile.allowed_tool_names
    # Slice 5: commerce.create_order is still registered (above) but no longer
    # granted to the interactive seller — the records pass captures items
    # post-reply; the action agent keeps it (asserted below).
    assert "commerce.create_order" not in interactive_profile.allowed_tool_names
    assert "commerce.create_checkout_intent" not in interactive_profile.allowed_tool_names
    assert "knowledge_extract_candidates" not in interactive_profile.allowed_tool_names
    assert "work.create_task" in action_profile.allowed_tool_names
    assert "owner.notify" in action_profile.allowed_tool_names
    assert "conversation.record_intelligence" in action_profile.allowed_tool_names
    assert "commerce.create_order" in action_profile.allowed_tool_names
    assert "commerce.create_checkout_intent" in action_profile.allowed_tool_names


async def test_hermes_can_record_idempotent_customer_intelligence(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
) -> None:
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    service = AgentBusinessActionService(db_session)

    first = await service.record_intelligence(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:intelligence",
        lead_stage="interested",
        buying_signals=["asked for price", "asked how to pay"],
        objections=["needs payment details"],
        preferences={"payment_method": "click"},
        next_best_action="send approved payment details or ask owner",
        owner_notes=["Customer looks ready to buy after payment details are clarified."],
        risk_flags=["missing_payment_authority"],
        source_refs=["message:customer:intelligence"],
        idempotency_key="customer-intelligence:ready-to-buy",
    )
    replayed = await service.record_intelligence(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:intelligence",
        lead_stage="cold",
        buying_signals=["duplicate"],
        idempotency_key="customer-intelligence:ready-to-buy",
    )
    stored = await CommercialSpineRepository(db_session).get_projection(
        workspace_id=workspace.id,
        projection_ref=first.intelligence_ref,
    )

    assert first.intelligence_ref == replayed.intelligence_ref
    assert first.status == "recorded"
    assert replayed.payload["lead_stage"] == "interested"
    assert stored is not None
    assert stored.projection_type == "customer_intelligence"
    assert stored.state["buying_signals"] == ["asked for price", "asked how to pay"]
    assert stored.state["preferences"]["payment_method"] == "click"


async def test_hermes_can_create_pending_checkout_intent_for_generic_catalog_item(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
) -> None:
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace.id,
                product_ref="product:hoodie",
                name="Black Hoodie",
                aliases=["hoodie"],
                description="Generic store product",
                attributes={},
                authority_state="approved",
                source_refs=["source:catalog:hoodie"],
                source_fact_ids=["catalog_product:hoodie"],
                freshness={},
            ),
            CatalogOfferRecord(
                workspace_id=workspace.id,
                offer_ref="offer:hoodie:main",
                product_ref="product:hoodie",
                price="120000",
                currency="UZS",
                stock_state="available",
                availability="available",
                authority_state="approved",
                source_refs=["source:catalog:hoodie"],
                source_fact_ids=["catalog_offer:hoodie:main"],
            ),
        ]
    )
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    task = await AgentBusinessActionService(db_session).create_task(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:checkout",
        task_kind="payment",
        title="Send Click details",
        reason="Payment details are missing.",
        missing_authority=["payment_details:click"],
        source_refs=["message:customer:checkout"],
        idempotency_key="task:checkout:payment-details",
    )
    service = AgentBusinessActionService(db_session)

    first = await service.create_checkout_intent(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:checkout",
        selected_items=[
            {
                "product_ref": "product:hoodie",
                "offer_ref": "offer:hoodie:main",
                "quantity": 1,
            }
        ],
        shown_prices=[
            {
                "offer_ref": "offer:hoodie:main",
                "amount": "120000",
                "currency": "UZS",
                "authority_ref": "catalog_offer:hoodie:main",
            }
        ],
        payment_method="click",
        fulfillment_method="pickup",
        status="pending",
        missing_fields=["payment_details:click"],
        linked_task_refs=[task.task_ref],
        source_refs=["message:customer:checkout"],
        idempotency_key="checkout:hoodie:click",
    )
    replayed = await service.create_checkout_intent(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:checkout",
        selected_items=[{"product_ref": "product:hoodie", "offer_ref": "offer:hoodie:main"}],
        shown_prices=[],
        payment_method="click",
        fulfillment_method="pickup",
        source_refs=["message:customer:checkout"],
        idempotency_key="checkout:hoodie:click",
    )
    projection = await CommercialSpineRepository(db_session).get_projection(
        workspace_id=workspace.id,
        projection_ref=first.checkout_ref,
    )

    assert first.checkout_ref == replayed.checkout_ref
    assert first.proposal_id == replayed.proposal_id
    assert first.status == "blocked"
    assert first.missing_fields == ["payment_details:click"]
    assert first.authority_refs == ["catalog_offer:hoodie:main"]
    assert projection is not None
    assert projection.projection_type == "commerce_checkout_intent"
    assert projection.state["selected_items"][0]["product_ref"] == "product:hoodie"
    assert projection.state["linked_task_refs"] == [task.task_ref]


async def test_hermes_can_create_generic_order_intent_alias(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
) -> None:
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace.id,
                product_ref="product:course",
                name="Online Course",
                aliases=["course"],
                description="Generic service product",
                attributes={},
                authority_state="approved",
                source_refs=["source:catalog:course"],
                source_fact_ids=["catalog_product:course"],
                freshness={},
            ),
            CatalogOfferRecord(
                workspace_id=workspace.id,
                offer_ref="offer:course:main",
                product_ref="product:course",
                price="300000",
                currency="UZS",
                stock_state="available",
                availability="available",
                authority_state="approved",
                source_refs=["source:catalog:course"],
                source_fact_ids=["catalog_offer:course:main"],
            ),
        ]
    )
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )

    result = await AgentBusinessActionService(db_session).create_order_intent(
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:order-intent",
        selected_items=[
            {
                "product_ref": "product:course",
                "offer_ref": "offer:course:main",
                "quantity": 1,
            }
        ],
        shown_prices=[
            {
                "offer_ref": "offer:course:main",
                "amount": "300000",
                "currency": "UZS",
            }
        ],
        payment_method="cash",
        fulfillment_method="online",
        status="pending",
        source_refs=["message:customer:order"],
        idempotency_key="order-intent:course:cash",
    )
    stored = await CommercialSpineRepository(db_session).get_projection(
        workspace_id=workspace.id,
        projection_ref=result.order_ref,
    )

    assert result.order_ref.startswith("order_intent:")
    assert result.checkout_ref == result.order_ref
    assert result.status == "pending"
    assert result.missing_fields == []
    assert stored is not None
    assert stored.projection_type == "commerce_order_intent"
    assert stored.state["order_ref"] == result.order_ref
