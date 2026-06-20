from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.agent_runtime_context.contracts import AgentRuntimeContextRequest
from app.modules.agent_runtime_context.service import AgentRuntimeContextService
from app.modules.agent_runtime_v2.grounding import format_agent_grounding
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService

pytestmark = pytest.mark.asyncio


async def test_agent_runtime_context_assembles_documents_skills_grants_and_latest_50(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    agent.agent_type = "seller"
    agent.tools_config = {
        "permission_mode": "ask_always",
        "tool_scopes": [
            "telegram.read_messages",
            "telegram.send_message",
            "brain.search",
            "conversation.get_context",
        ],
    }
    docs = AgentDocumentService(db_session)
    await docs.upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="business",
            subject_type="workspace",
            section_key="what_we_sell",
            title="Nima sotamiz",
            body="Kurslar va obunalar sotiladi.",
            order_index=10,
        ),
    )
    await docs.upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="agent",
            subject_type="agent",
            subject_id=agent.id,
            section_key="role",
            title="Rol",
            body="Dalilga tayanib javob beradi.",
            order_index=10,
        ),
    )
    workspace_skill = await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(
            slug="default-evidence",
            name="Default evidence",
            description="Always cite source-backed facts.",
            instructions="Source refs must stay visible to the agent runtime.",
        ),
    )
    agent_skill = await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(
            slug="seller-reply",
            name="Customer turn",
            description="Write grounded seller replies.",
            agent_id=agent.id,
        ),
    )
    other_agent = Agent(workspace_id=workspace.id, name="Other", agent_type="support")
    db_session.add(other_agent)
    await db_session.flush()
    await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(
            slug="other-agent-skill",
            name="Other agent skill",
            agent_id=other_agent.id,
        ),
    )
    await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(
            slug="disabled-skill",
            name="Disabled skill",
            agent_id=agent.id,
            enabled=False,
        ),
    )
    await docs.upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="skill",
            subject_type="skill",
            subject_id=workspace_skill.id,
            section_key="policy",
            title="Policy",
            body="Only active skill sections are cached.",
        ),
    )
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(
            agent_id=agent.id,
            scope="telegram.read_messages",
            grant_reason="read before answering",
        ),
    )
    base_time = datetime(2026, 5, 18, tzinfo=UTC)
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type="customer" if index % 2 == 0 else "seller",
                content=f"message {index}",
                created_at=base_time + timedelta(seconds=index),
            )
            for index in range(55)
        ]
    )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            include_grounding=False,
        )
    )

    assert context.agent_kind == "seller_agent"
    assert "Kurslar va obunalar" in context.documents.business_md.markdown
    assert "Dalilga tayanib" in context.documents.agent_md.markdown
    rendered_skill_titles = {doc.title for doc in context.documents.skill_md}
    assert rendered_skill_titles == {
        "SKILL.md — Default evidence",
        "SKILL.md — Customer turn",
    }
    assert len(context.recent_messages) == 50
    assert context.recent_messages[0].content == "message 5"
    assert context.recent_messages[-1].content == "message 54"
    assert context.permissions.internal_capabilities == [
        "brain.search",
        "conversation.get_context",
    ]
    assert context.permissions.active_external_scopes == ["telegram.read_messages"]
    assert context.permissions.missing_external_scopes == ["telegram.send_message"]
    assert context.permissions.permission_mode == "ask_always"
    assert context.cache_plan.cache_key.startswith(
        f"agent-runtime-context:v1:{workspace.id}:{agent.id}:"
    )
    assert f"agent:{agent.id}:tool_grants" in context.cache_plan.invalidation_refs
    assert "other-agent-skill" not in context.prompt_sections["static"]["agent_md"]
    assert agent_skill.slug in context.documents.agent_md.markdown


async def test_agent_runtime_context_orders_recent_messages_by_telegram_order(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    base_time = datetime(2026, 6, 6, 16, 28, 56, tzinfo=UTC)
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Assalomu alaykum",
                telegram_message_id=1628,
                telegram_timestamp=base_time,
                created_at=base_time + timedelta(milliseconds=300),
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="sat bormi",
                telegram_message_id=1629,
                telegram_timestamp=base_time,
                created_at=base_time + timedelta(milliseconds=900),
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="narxi qancha",
                telegram_message_id=1630,
                telegram_timestamp=base_time,
                created_at=base_time + timedelta(milliseconds=600),
            ),
        ]
    )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            include_grounding=False,
        )
    )

    assert [message.content for message in context.recent_messages[-3:]] == [
        "Assalomu alaykum",
        "sat bormi",
        "narxi qancha",
    ]


async def test_agent_runtime_context_includes_agent_session_summary_and_transcript_hits(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
    customer,
) -> None:
    service = AgentSessionService(db_session)
    agent_session = await service.get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    agent_session.summary = "Mijoz SAT tayyorgarlik platformasi haqida so'rayapti."
    first = Message(
        conversation_id=conversation.id,
        sender_type="customer",
        content="Assalomu alaykum",
        telegram_message_id=501,
    )
    second = Message(
        conversation_id=conversation.id,
        sender_type="customer",
        content="narxi qancha",
        telegram_message_id=502,
    )
    db_session.add_all([first, second])
    await db_session.flush()
    for message in (first, second):
        await service.append_event(
            agent_session_id=agent_session.id,
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            agent_id=agent.id,
            event_type="customer_message",
            direction="inbound",
            message_id=message.id,
            text=message.content,
            payload={"telegram_message_id": message.telegram_message_id},
            idempotency_key=f"message:{message.id}:customer_message:agent:{agent.id}",
        )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            agent_session_id=agent_session.id,
            hermes_session_id=agent_session.hermes_session_id,
            include_grounding=False,
        )
    )

    assert context.session_summary == "Mijoz SAT tayyorgarlik platformasi haqida so'rayapti."
    assert context.transcript_hits == [
        "customer_message inbound: Assalomu alaykum",
        "customer_message inbound: narxi qancha",
    ]
    assert context.prompt_sections["dynamic"]["agent_session"] == {
        "agent_session_id": agent_session.id,
        "hermes_session_id": agent_session.hermes_session_id,
        "summary": "Mijoz SAT tayyorgarlik platformasi haqida so'rayapti.",
        "transcript_hits": [
            "customer_message inbound: Assalomu alaykum",
            "customer_message inbound: narxi qancha",
        ],
    }
    assert context.telemetry["agent_session"]["event_count"] == 2


async def test_agent_runtime_context_uses_retrieval_core_for_grounding(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    agent.agent_type = "support"
    memory = BusinessBrainMemoryService(
        repository=CommercialSpineRepository(db_session),
    )
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="knowledge_fact:delivery",
            fact_type="knowledge_fact",
            entity_ref="business:faq:delivery",
            value={
                "question": "Yetkazib berish nechchi kun?",
                "answer": "Toshkent bo'ylab yetkazib berish 1-2 kun.",
                "topic": "delivery",
            },
            source_refs=["source:delivery-policy"],
            source="manual",
            status="active",
            approval_state="confirmed",
            confidence=0.91,
            risk_tier="low",
            correlation_id="corr:delivery",
            idempotency_key="idem:delivery",
        )
    )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            query_text="Toshkent yetkazib berish necha kun?",
            requested_fact_types=["knowledge_fact"],
            enable_semantic=False,
            enable_contextual_rank=False,
            enable_agentic_search=False,
        )
    )

    assert context.agent_kind == "support_agent"
    assert context.grounding is not None
    assert "knowledge_fact" in context.grounding.families
    assert (
        context.grounding.families["knowledge_fact"][0]["fact_id"]
        == "knowledge_fact:delivery"
    )
    assert context.telemetry["schema_version"] == "agent_runtime_context_telemetry.v1"
    assert context.telemetry["latency"]["total_ms"] >= 0
    assert context.telemetry["latency"]["grounding_ms"] >= 0
    assert context.telemetry["grounding"]["candidate_count"] >= 1
    assert context.telemetry["grounding"]["source_ref_count"] >= 1
    assert context.telemetry["grounding"]["evidence_backed"] is True
    assert "lexical" in context.telemetry["grounding"]["retrieval_channels"]
    assert context.prompt_sections["dynamic"]["grounding"]["families"][
        "knowledge_fact"
    ][0]["source_refs"] == ["source:delivery-policy"]


async def test_seller_agent_grounds_on_real_catalog_product(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    # The reply-agent eval previously faked products as a knowledge_fact. This
    # proves the real chain: a seeded catalog_product fact flows through
    # Retrieval Core grounding into the v2 reply prompt as a [MAHSULOT] line.
    agent.agent_type = "seller"
    memory = BusinessBrainMemoryService(
        repository=CommercialSpineRepository(db_session),
    )
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="catalog_product:binafsha-sumka",
            fact_type="catalog_product",
            entity_ref="catalog:product:binafsha-sumka",
            value={
                "name": "Binafsha charm sumka",
                "price": "180 000 so'm",
                "availability": "bor",
            },
            source_refs=["source:catalog:binafsha"],
            source="manual",
            status="active",
            approval_state="confirmed",
            confidence=0.95,
            risk_tier="low",
            correlation_id="corr:catalog:binafsha",
            idempotency_key="idem:catalog:binafsha",
        )
    )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            query_text="Binafsha charm sumka narxi qancha?",
            requested_fact_types=["catalog_product"],
            enable_semantic=False,
            enable_contextual_rank=False,
            enable_agentic_search=False,
        )
    )

    assert context.agent_kind == "seller_agent"
    assert context.grounding is not None
    assert "catalog_product" in context.grounding.families
    assert (
        context.grounding.families["catalog_product"][0]["fact_id"]
        == "catalog_product:binafsha-sumka"
    )

    # The v2 reply runtime renders this bundle into prompt evidence lines; the
    # product must arrive as a labelled [MAHSULOT] line the model can sell from.
    lines = format_agent_grounding(context.grounding)
    assert any("[MAHSULOT]" in line and "Binafsha charm sumka" in line for line in lines)


async def test_seller_agent_default_fact_types_surface_seller_rules(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    # Seller rules are stored as fact_type "seller_rule_fact", but the seller
    # agent's DEFAULT fact-type set requested "seller_rule" — so when no explicit
    # types are passed (the live path), the type filter dropped every rule and the
    # agent could never ground on them (it escalated instead of answering). This
    # exercises the DEFAULT path (no requested_fact_types) the other tests bypass.
    agent.agent_type = "seller"
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="seller_rule:marathon-repost",
            fact_type="seller_rule_fact",
            entity_ref="business:rule:marathon",
            value={
                "rule": "Marafonga qo'shilish uchun e'lonni o'z kanalingizga repost qiling.",
            },
            source_refs=["telegram:channel:satstation:message:7"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.9,
            risk_tier="low",
            correlation_id="corr:rule:marathon",
            idempotency_key="idem:rule:marathon",
        )
    )
    await db_session.flush()

    # No requested_fact_types -> falls back to DEFAULT_FACT_TYPES_BY_AGENT_KIND.
    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            query_text="Marafonga qanday qo'shilaman, repost kerakmi?",
            enable_semantic=False,
            enable_contextual_rank=False,
            enable_agentic_search=False,
        )
    )

    assert context.agent_kind == "seller_agent"
    assert context.grounding is not None
    assert "seller_rule_fact" in context.grounding.families

    # And it must render with its proper [QOIDA] label, not the raw fact type.
    lines = format_agent_grounding(context.grounding)
    assert any("[QOIDA]" in line and "repost" in line for line in lines)


async def test_agent_runtime_context_does_not_rehydrate_retired_autocrm_customer_state(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    # The Intelligence layer (autocrm) distils each turn into a durable
    # current_interest fact. The context service must surface the recent arc
    # (latest-first, deduplicated, customer-scoped) so the agent carries the
    # customer's established situation even when the originating turns scroll out
    # of the raw message window.
    agent.agent_type = "seller"
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    customer_ref = f"customer:{conversation.customer_id}"
    moments = [
        "customer is unable to complete registration for June 6th",
        "customer is unable to complete registration for June 6th",  # duplicate
        "customer asks whether registration assistance is a service",
    ]
    for index, moment in enumerate(moments):
        await memory.write_memory_fact(
            MemoryFactWriteInput(
                workspace_id=workspace.id,
                fact_id=f"autocrm_current_interest:tail:{index}",
                fact_type="autocrm_current_interest",
                entity_ref=customer_ref,
                value={
                    "current_interest": {
                        "sales_moment": moment,
                        "detected_intent": "support",
                    }
                },
                source_refs=[f"message:{index}"],
                source="manual",
                status="active",
                approval_state="confirmed",
                confidence=0.95,
                risk_tier="low",
                correlation_id=f"corr:interest:{index}",
                idempotency_key=f"idem:interest:{index}",
            )
        )
    # An interest belonging to a DIFFERENT customer must never leak in.
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="autocrm_current_interest:other-customer",
            fact_type="autocrm_current_interest",
            entity_ref="customer:999999",
            value={"current_interest": {"sales_moment": "different customer wants a refund"}},
            source_refs=["message:x"],
            source="manual",
            status="active",
            approval_state="confirmed",
            confidence=0.95,
            risk_tier="low",
            correlation_id="corr:interest:other",
            idempotency_key="idem:interest:other",
        )
    )
    await db_session.flush()

    context = await AgentRuntimeContextService(db_session).build(
        AgentRuntimeContextRequest(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            include_grounding=False,
        )
    )

    # The legacy AutoCRM current-interest lane is retired. Runtime situation now
    # comes from recent chat, Chat Memory, Retrieval Core, and action signals.
    # The packet should not keep an always-empty compatibility lane around.
    assert not hasattr(context, "customer_state")
    assert "customer_state" not in context.prompt_sections["dynamic"]
    assert "customer_state_ms" not in context.telemetry["latency"]


async def test_seller_default_fact_types_include_commerce_facets():
    from app.modules.agent_runtime_context.service import DEFAULT_FACT_TYPES_BY_AGENT_KIND

    seller_types = DEFAULT_FACT_TYPES_BY_AGENT_KIND["seller_agent"]

    assert "catalog_product" in seller_types
    assert "catalog_variant" in seller_types
    assert "catalog_offer" in seller_types
    assert seller_types.index("catalog_product") < seller_types.index("catalog_variant")
    assert seller_types.index("catalog_variant") < seller_types.index("catalog_offer")
