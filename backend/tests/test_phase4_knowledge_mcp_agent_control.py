from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hermes_run import HermesRun, HermesRunEvent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.conversation import Conversation
from app.models.knowledge_mcp import (
    KnowledgeCandidateRecord,
    KnowledgeChunkRecord,
    KnowledgeItemRecord,
)
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.agent_control.contracts import AgentControlActionInput
from app.modules.agent_control.service import AgentControlService
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.knowledge_mcp.contracts import (
    KnowledgeAttachToCollectionInput,
    KnowledgeCandidateInput,
    KnowledgeCatalogSearchRequest,
    KnowledgeChatMemorySearchRequest,
    KnowledgeExplainSourcesRequest,
    KnowledgeGetItemRequest,
    KnowledgeMediaSearchRequest,
    KnowledgeSaveInput,
    KnowledgeScope,
    KnowledgeSearchRequest,
    KnowledgeTagItemInput,
)
from app.modules.knowledge_mcp.service import KnowledgeMCPService
from app.modules.telegram_control_bot.service import (
    DisabledTelegramControlBotClient,
    TelegramControlBotService,
)


def _personal_scope(workspace: Workspace) -> KnowledgeScope:
    owner_id = (
        f"user:{workspace.telegram_user_id}"
        if workspace.telegram_user_id
        else f"workspace-user:{workspace.id}"
    )
    return KnowledgeScope(
        owner_type="user",
        owner_id=owner_id,
    )


def _business_scope(workspace: Workspace) -> KnowledgeScope:
    return KnowledgeScope(
        owner_type="workspace",
        owner_id=f"workspace:{workspace.id}",
        workspace_id=workspace.id,
    )


def _memory_fact(
    *,
    workspace: Workspace,
    fact_id: str,
    fact_type: str,
    entity_ref: str | None = None,
    value: dict,
    source_refs: list[str] | None = None,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type=fact_type,
        entity_ref=entity_ref or fact_id,
        value=value,
        source_refs=source_refs or [f"source:{fact_id}"],
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.9,
        risk_tier="low",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


class _FakeControlBotClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.answered: list[dict] = []

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict,
    ) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"ok": True}

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict,
    ) -> dict:
        self.edited.append(
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        )
        return {"ok": True}

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str,
        show_alert: bool = False,
    ) -> dict:
        self.answered.append(
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return {"ok": True}


async def test_disabled_telegram_control_bot_client_accepts_control_bot_calls() -> None:
    client = DisabledTelegramControlBotClient()
    expected = {"ok": False, "skipped": True, "reason": "telegram_control_bot_disabled"}

    assert await client.send_message(chat_id=1, text="Approve?", reply_markup={}) == expected
    assert await client.edit_message_reply_markup(
        chat_id=1,
        message_id=2,
        reply_markup={},
    ) == expected
    assert await client.answer_callback_query(
        callback_query_id="cb-1",
        text="Disabled",
        show_alert=True,
    ) == expected


class _FakeKnowledgeEmbeddingService:
    async def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * 3072
        lower = text.lower()
        if "aurora" in lower or "northern lights" in lower:
            vector[0] = 1.0
        elif "sneaker" in lower:
            vector[1] = 1.0
        else:
            vector[2] = 1.0
        return vector


async def test_personal_script_save_indexes_collection_and_search(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _personal_scope(workspace)

    item = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="script",
            title="Instagram launch script",
            body_text="Bugun SATStation uchun starter coins promo script yozdik.",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["script", "instagram", "promo"],
            authority_state="source",
            visibility="private",
            created_by="agent",
            created_by_ref="agent:marketing",
            correlation_id="corr-personal-script",
            idempotency_key="personal-script-1",
        )
    )
    result = await service.search(
        KnowledgeSearchRequest(
            scope=scope,
            query="starter coins promo",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["script"],
        )
    )

    assert item.kind == "script"
    assert item.authority_state == "source"
    assert result.hits[0].item.item_id == item.item_id
    assert result.hits[0].citations[0]["source_refs"] == item.source_refs


async def test_knowledge_mcp_stats_reports_storage_candidates_and_tool_calls(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    personal_scope = _personal_scope(workspace)
    business_scope = _business_scope(workspace)

    personal_item = await service.save_item(
        KnowledgeSaveInput(
            scope=personal_scope,
            kind="script",
            title="Launch caption",
            body_text="Aurora launch caption for Instagram.",
            collection_ids=["marketing/scripts"],
            tags=["script"],
            authority_state="source",
            visibility="private",
            created_by="agent",
            created_by_ref="agent:marketing",
            correlation_id="corr-stats-personal",
            idempotency_key="stats-personal-1",
        )
    )
    source_doc = await service.save_item(
        KnowledgeSaveInput(
            scope=business_scope,
            kind="source",
            title="Delivery note",
            body_text="Delivery takes two days.",
            collection_ids=["business/sources"],
            tags=["delivery"],
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:seller",
            correlation_id="corr-stats-business",
            idempotency_key="stats-business-1",
        )
    )
    await service.propose_candidate(
        KnowledgeCandidateInput(
            scope=business_scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="policy",
            proposed_payload={"topic": "delivery", "answer": "Delivery takes two days."},
            evidence_refs=source_doc.source_refs,
            confidence=0.8,
            created_by_ref="agent:seller",
            hermes_run_id="knowledge-stats-run",
            correlation_id="corr-stats-candidate",
            idempotency_key="stats-candidate-1",
        )
    )
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        _memory_fact(
            workspace=workspace,
            fact_id="catalog_product:stats-launch-pack",
            fact_type="catalog_product",
            entity_ref="catalog:stats-launch-pack",
            value={
                "title": "Stats launch pack",
                "description": "A catalog item visible through the Knowledge MCP adapter.",
            },
            source_refs=["catalog:stats-launch-pack"],
        )
    )
    run = HermesRun(
        run_id="knowledge-stats-run",
        workspace_id=workspace.id,
        agent_kind="seller",
        lane="fast_interactive",
        run_mode="reply",
        trigger_type="manual",
        trigger_id="knowledge-stats",
        correlation_id="corr-stats-run",
        idempotency_key="knowledge-stats-run",
        state="completed",
        source_refs=[],
        input_summary="stats",
        details={},
        payload={},
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        HermesRunEvent(
            hermes_run_id=run.id,
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_id="knowledge-stats-event",
            sequence=1,
            kind="tool_called",
            visibility="internal",
            tool_name="knowledge_search",
            tool_state="ok",
            correlation_id="corr-stats-run",
            idempotency_key="knowledge-stats-event",
            payload={
                "query": "launch",
                "scope": "personal",
                "latency_ms": 123.4,
                "hit_count": 1,
                "citation_count": 1,
                "source_ref_count": 1,
                "evidence_backed": True,
                "retrieval_channels": ["lexical"],
                "top_score": 1.0,
                "citations": [
                    {
                        "item_id": personal_item.item_id,
                        "source_refs": personal_item.source_refs,
                        "retrieval_channels": ["lexical"],
                    }
                ],
            },
        )
    )
    db_session.add(
        HermesRunEvent(
            hermes_run_id=run.id,
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_id="knowledge-stats-context-event",
            sequence=2,
            kind="context_gathered",
            visibility="internal",
            correlation_id="corr-stats-run",
            idempotency_key="knowledge-stats-context-event",
            payload={
                "grounding_lines": 1,
                "history_lines": 1,
                "latency_ms": 45.5,
                "grounding_ms": 20.0,
                "candidate_count": 2,
                "source_ref_count": 2,
                "evidence_backed": True,
                "retrieval_channels": ["structured", "lexical"],
                "context_metrics": {
                    "schema_version": "turn_context_telemetry.v1",
                    "latency": {
                        "total_ms": 45.5,
                        "grounding_ms": 20.0,
                    },
                    "grounding": {
                        "truth_evidence_count": 1,
                        "candidate_count": 2,
                        "source_ref_count": 2,
                        "evidence_backed": True,
                        "retrieval_channels": ["structured", "lexical"],
                        "degraded_count": 0,
                    },
                },
            },
        )
    )
    await db_session.flush()

    stats = await service.stats(
        workspace_id=workspace.id,
        personal_owner_id=personal_scope.owner_id,
    )

    assert stats["schema_version"] == "knowledge_mcp_stats.v1"
    assert stats["empty"] is False
    assert stats["totals"]["items"] == 2
    assert stats["totals"]["sources"] == 2
    assert stats["totals"]["chunks"] == 2
    assert stats["totals"]["candidates"] == 1
    assert stats["totals"]["catalog_facts"] == 1
    assert stats["totals"]["catalog_active_facts"] == 1
    assert stats["totals"]["catalog_products"] == 1
    assert stats["totals"]["catalog_active_products"] == 1
    assert stats["totals"]["knowledge_tool_calls"] == 1
    assert stats["totals"]["knowledge_actions"] == 1
    assert stats["items_by_kind"] == {"script": 1, "source": 1}
    assert stats["items_by_authority_state"] == {"source": 2}
    assert stats["chunks_by_embedding_state"] == {"pending": 2}
    assert stats["candidates_by_status"] == {"pending": 1}
    assert stats["catalog_adapter"]["facts_by_type"] == {"catalog_product": 1}
    assert stats["catalog_adapter"]["facts_by_status"] == {"active": 1}
    assert stats["catalog_adapter"]["recent_facts"][0]["title"] == "Stats launch pack"
    assert stats["retrieval"]["total_search_calls"] == 1
    assert stats["retrieval"]["latency"]["avg_ms"] == 123.4
    assert stats["retrieval"]["quality_proxy"]["zero_hit_rate"] == 0
    assert stats["retrieval"]["quality_proxy"]["evidence_backed_rate"] == 1
    assert stats["retrieval"]["features"]["retrieval_channels"] == {"lexical": 1}
    assert stats["retrieval"]["by_tool"]["knowledge_search"]["avg_hit_count"] == 1
    assert stats["retrieval"]["eager_context"]["total_context_gathers"] == 1
    assert stats["retrieval"]["eager_context"]["latency"]["avg_ms"] == 45.5
    assert stats["retrieval"]["eager_context"]["latency"]["grounding_avg_ms"] == 20
    assert stats["retrieval"]["eager_context"]["quality_proxy"]["avg_candidate_count"] == 2
    assert (
        stats["retrieval"]["eager_context"]["quality_proxy"]["evidence_backed_rate"]
        == 1
    )
    assert stats["actions_by_lifecycle"] == {"waiting_approval": 1}
    assert stats["recent_items"][0]["item_id"] in {
        personal_item.item_id,
        source_doc.item_id,
    }
    assert stats["recent_candidates"][0]["proposed_kind"] == "policy"
    assert stats["recent_tool_calls"][0]["tool_name"] == "knowledge_search"
    assert stats["recent_tool_calls"][0]["latency_ms"] == 123.4


async def test_personal_script_search_uses_semantic_chunk_embeddings(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(
        db_session,
        embedding_service=_FakeKnowledgeEmbeddingService(),
        embed_on_write=True,
        enable_semantic=True,
    )
    scope = _personal_scope(workspace)
    aurora = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="script",
            title="Aurora bundle launch",
            body_text="Polar sky campaign angle for the evening bundle.",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["script"],
            created_by_ref="agent:marketing",
            correlation_id="corr-semantic-aurora",
            idempotency_key="semantic-aurora",
        )
    )
    await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="script",
            title="Sneaker discount launch",
            body_text="Footwear sale script for returning customers.",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["script"],
            created_by_ref="agent:marketing",
            correlation_id="corr-semantic-sneaker",
            idempotency_key="semantic-sneaker",
        )
    )

    result = await service.search(
        KnowledgeSearchRequest(
            scope=scope,
            query="northern lights offer",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["script"],
            enable_semantic=True,
        )
    )

    chunk_row = await db_session.scalar(
        select(KnowledgeChunkRecord).where(KnowledgeChunkRecord.item_id == aurora.item_id)
    )
    assert chunk_row is not None
    assert chunk_row.embedding_state == "ready"
    assert result.hits[0].item.item_id == aurora.item_id
    citation = result.hits[0].citations[0]
    assert citation["type"] == "knowledge_chunk"
    assert citation["chunk_id"].startswith("knowledge_chunk:")
    assert citation["retrieval_scores"]["semantic"] > 0.99


async def test_knowledge_get_item_and_explain_sources_return_evidence(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _personal_scope(workspace)
    item = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="note",
            title="Launch note",
            body_text="Starter promo source text.",
            created_by_ref="agent:marketing",
            correlation_id="corr-get-item",
            idempotency_key="get-item-1",
        )
    )

    detail = await service.get_item(
        KnowledgeGetItemRequest(scope=scope, item_id=item.item_id)
    )
    explanation = await service.explain_sources(
        KnowledgeExplainSourcesRequest(scope=scope, item_id=item.item_id)
    )

    assert detail is not None
    assert detail.item.item_id == item.item_id
    assert detail.sources[0].source_id == item.source_refs[0]
    assert detail.sources[0].raw_content == "Starter promo source text."
    assert detail.chunks[0].citation["item_id"] == item.item_id
    assert explanation is not None
    assert explanation.item_id == item.item_id
    assert explanation.source_refs == item.source_refs
    assert explanation.citations[0]["source_id"] == item.source_refs[0]


async def test_knowledge_attach_and_tag_update_retrieval_metadata_only(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _personal_scope(workspace)
    item = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="script",
            title="Short reel script",
            body_text="Starter coins uchun 15 sekundlik reels script.",
            collection_ids=["Drafts"],
            tags=["video"],
            created_by_ref="agent:marketing",
            correlation_id="corr-attach-tag",
            idempotency_key="attach-tag-1",
        )
    )

    attached = await service.attach_to_collection(
        KnowledgeAttachToCollectionInput(
            scope=scope,
            item_id=item.item_id,
            collection_ids=["Mirzo / Marketing Scripts", "Drafts"],
        )
    )
    tagged = await service.tag_item(
        KnowledgeTagItemInput(
            scope=scope,
            item_id=item.item_id,
            tags=["promo", "video"],
        )
    )
    result = await service.search(
        KnowledgeSearchRequest(
            scope=scope,
            query="starter reels",
            collection_ids=["Mirzo / Marketing Scripts"],
            tags=["promo"],
        )
    )

    assert attached.authority_state == "source"
    assert attached.collection_ids == ["Drafts", "Mirzo / Marketing Scripts"]
    assert tagged.tags == ["video", "promo"]
    assert result.hits[0].item.item_id == item.item_id


async def test_chat_memory_search_returns_historical_hits_with_citations(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
) -> None:
    debt_message = Message(
        conversation_id=conversation.id,
        sender_type="seller",
        content="Alisherga starter coins paketini qarzga berdim, juma kuni eslat.",
        telegram_message_id=4455,
    )
    unrelated = Message(
        conversation_id=conversation.id,
        sender_type="customer",
        content="Salom, oddiy savol.",
        telegram_message_id=4456,
    )
    db_session.add_all([debt_message, unrelated])
    await db_session.flush()

    result = await KnowledgeMCPService(db_session).search_chat_memory(
        KnowledgeChatMemorySearchRequest(
            workspace_id=workspace.id,
            query="starter coins qarz",
            sender_types=["seller"],
            limit=5,
        )
    )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.item.kind == "chat"
    assert hit.item.authority_state == "source"
    assert hit.item.collection_ids == ["chat_memory"]
    assert hit.item.body_text == debt_message.content
    assert f"conversation:{conversation.id}" in hit.item.source_refs
    assert f"message:{debt_message.id}" in hit.item.source_refs
    assert f"telegram_message:{conversation.telegram_chat_id}:{debt_message.telegram_message_id}" in hit.item.source_refs
    citation = hit.citations[0]
    assert citation["type"] == "chat_message"
    assert citation["conversation_id"] == conversation.id
    assert citation["message_id"] == debt_message.id
    assert citation["sender_type"] == "seller"
    assert citation["telegram_message_id"] == debt_message.telegram_message_id


async def test_knowledge_catalog_search_cites_approved_catalog_and_excludes_chat_examples(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        _memory_fact(
            workspace=workspace,
            fact_id="catalog_product:starter-coins",
            fact_type="catalog_product",
            value={
                "title": "Starter coins",
                "description": "SATStation starter coins package.",
            },
            source_refs=["catalog:starter:approved"],
        )
    )
    await memory.write_memory_fact(
        _memory_fact(
            workspace=workspace,
            fact_id="conversation_pair:starter-chat-example",
            fact_type="conversation_pair_fact",
            entity_ref="conversation:example",
            value={
                "customer": "starter coins narxi qancha",
                "seller": "old chat example should not be catalog authority",
            },
            source_refs=["message:old-chat-example"],
        )
    )

    result = await KnowledgeMCPService(db_session).search_catalog(
        KnowledgeCatalogSearchRequest(
            workspace_id=workspace.id,
            query="starter coins package",
            enable_semantic=False,
            enable_rerank=False,
        )
    )

    assert [hit.item.metadata["fact_type"] for hit in result.hits] == ["catalog_product"]
    hit = result.hits[0]
    assert hit.item.kind == "catalog"
    assert hit.item.authority_state == "approved"
    assert hit.item.collection_ids == ["business/catalog"]
    assert "fact:catalog_product:starter-coins" in hit.item.source_refs
    assert hit.citations[0]["fact_id"] == "catalog_product:starter-coins"
    assert hit.citations[0]["source_refs"] == ["catalog:starter:approved"]
    assert "conversation_pair_fact" not in hit.citations[0]["expanded_fact_types"]


async def test_knowledge_media_search_routes_image_queries_to_multimodal_candidates(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        _memory_fact(
            workspace=workspace,
            fact_id="catalog_product:ruby-ring",
            fact_type="catalog_product",
            value={"title": "Ruby ring", "description": "Formal jewelry product."},
            source_refs=["catalog:ruby-ring"],
        )
    )
    await memory.write_memory_fact(
        _memory_fact(
            workspace=workspace,
            fact_id="catalog_media:ruby-ring:main",
            fact_type="catalog_media",
            entity_ref="catalog_product:ruby-ring",
            value={
                "media_ref": "catalog_media:ruby-ring:main",
                "product_ref": "catalog_product:ruby-ring",
                "alt_text": "customer photo shows ruby ring front side",
                "approved": True,
            },
            source_refs=["catalog:ruby-ring:image"],
        )
    )

    result = await KnowledgeMCPService(db_session).search_media(
        KnowledgeMediaSearchRequest(
            workspace_id=workspace.id,
            query="customer photo ruby ring front side",
            query_modalities=["image"],
            enable_semantic=False,
            enable_rerank=False,
        )
    )

    assert result.hits[0].item.kind == "media"
    assert result.hits[0].item.metadata["fact_type"] == "catalog_media"
    assert result.hits[0].item.collection_ids == ["business/media"]
    citation = result.hits[0].citations[0]
    assert citation["fact_id"] == "catalog_media:ruby-ring:main"
    assert "multimodal" in citation["retrieval_channels"]
    assert citation["expanded_fact_types"] == [
        "catalog_product",
        "catalog_media",
        "business_source_media_fact",
    ]


async def test_business_source_doc_stays_non_authority_and_candidate_requires_approval(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _business_scope(workspace)
    source_doc = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="source",
            title="Owner pasted delivery update",
            body_text="Toshkent ichida yetkazish 12 soat ichida.",
            collection_ids=["SATStation / Delivery Policies"],
            tags=["delivery", "source"],
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:seller",
            source_kind="paste",
            correlation_id="corr-business-source",
            idempotency_key="business-source-1",
        )
    )

    proposal = await service.propose_candidate(
        KnowledgeCandidateInput(
            scope=scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="policy",
            proposed_payload={"topic": "delivery", "answer": "12 soat ichida"},
            evidence_refs=source_doc.source_refs,
            confidence=0.84,
            created_by_ref="agent:seller",
            hermes_run_id="phase4-hermes-run:candidate",
            correlation_id="corr-candidate",
            idempotency_key="candidate-delivery-1",
        )
    )

    assert source_doc.authority_state == "source"
    assert proposal.candidate.status == "pending"
    assert proposal.action.action_kind == "knowledge.promote"
    assert proposal.action.hermes_run_id == "phase4-hermes-run:candidate"
    assert "agent_run:phase4-hermes-run:candidate" in proposal.action.evidence_refs
    assert proposal.action.status == "pending"
    assert proposal.action.policy_decision == "approve"

    row = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.proposal_id == proposal.action.action_id,
        )
    )
    assert row is not None
    assert row.action_type == "knowledge.promote"
    assert row.lifecycle_state == "waiting_approval"
    assert row.requires_approval is True


async def test_candidate_approval_promotes_authority_and_business_fact(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _business_scope(workspace)
    source_doc = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="source",
            title="Owner pasted policy",
            body_text="Starter paket narxi 40000 so'm.",
            tags=["price", "source"],
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:seller",
            source_kind="paste",
            correlation_id="corr-policy-source",
            idempotency_key="policy-source-1",
        )
    )
    proposal = await service.propose_candidate(
        KnowledgeCandidateInput(
            scope=scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="policy",
            proposed_payload={
                "topic": "starter_price",
                "answer": "Starter paket narxi 40000 so'm.",
            },
            evidence_refs=source_doc.source_refs,
            confidence=0.91,
            created_by_ref="agent:seller",
            correlation_id="corr-policy-candidate",
            idempotency_key="candidate-policy-1",
        )
    )

    promoted = await service.approve_candidate_action(
        workspace_id=workspace.id,
        action_id=proposal.action.action_id,
        actor_ref="telegram_user:42",
        correlation_id="corr-policy-approved",
    )

    assert promoted.candidate.status == "approved"
    assert promoted.action.status == "approved"

    candidate_row = await db_session.scalar(
        select(KnowledgeCandidateRecord).where(
            KnowledgeCandidateRecord.candidate_id == proposal.candidate.candidate_id,
        )
    )
    assert candidate_row is not None
    item_id = candidate_row.metadata_json["promoted_item_id"]

    item_row = await db_session.scalar(
        select(KnowledgeItemRecord).where(KnowledgeItemRecord.item_id == item_id)
    )
    assert item_row is not None
    assert item_row.authority_state == "approved"
    assert item_row.kind == "policy"

    fact_row = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.fact_id
            == f"knowledge:{proposal.candidate.candidate_id}",
        )
    )
    assert fact_row is not None
    assert fact_row.status == "active"
    assert fact_row.fact_type == "seller_rule_fact"


async def test_reply_and_knowledge_approval_share_agent_control_store(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = AgentControlService(CommercialSpineRepository(db_session))

    reply = await service.create_action(
        AgentControlActionInput(
            workspace_id=workspace.id,
            user_id=f"workspace:{workspace.id}",
            action_kind="reply.send",
            target_ref="conversation:42",
            proposed_payload={"text": "Assalomu alaykum"},
            risk_level="low",
            evidence_refs=["hermes_run:reply"],
            approval_required=True,
            correlation_id="corr-reply-control",
            idempotency_key="reply-control-1",
        )
    )
    knowledge = await service.create_action(
        AgentControlActionInput(
            workspace_id=workspace.id,
            user_id=f"workspace:{workspace.id}",
            action_kind="knowledge.promote",
            target_ref="knowledge_candidate:price",
            proposed_payload={"price": "40000"},
            risk_level="medium",
            evidence_refs=["source:catalog-post"],
            approval_required=True,
            correlation_id="corr-knowledge-control",
            idempotency_key="knowledge-control-1",
        )
    )
    await service.approve(
        workspace_id=workspace.id,
        action_id=reply.action_id,
        actor_ref=f"workspace:{workspace.id}",
        correlation_id="corr-approve-reply",
    )
    await service.reject(
        workspace_id=workspace.id,
        action_id=knowledge.action_id,
        actor_ref=f"workspace:{workspace.id}",
        correlation_id="corr-reject-knowledge",
    )

    rows = (
        await db_session.execute(
            select(
                CommercialActionProposalRecord.action_type,
                CommercialActionProposalRecord.lifecycle_state,
            )
            .where(CommercialActionProposalRecord.workspace_id == workspace.id)
            .order_by(CommercialActionProposalRecord.action_type)
        )
        ).all()

    assert ("knowledge.promote", "rejected") in rows
    assert ("send_reply", "approved") in rows
    fetched_reply = await service.get_action(workspace_id=workspace.id, action_id=reply.action_id)
    assert fetched_reply is not None
    assert fetched_reply.action_kind == "reply.send"
    assert fetched_reply.hermes_run_id == "reply"


async def test_agent_control_create_action_replays_existing_idempotent_action(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    control = AgentControlService(CommercialSpineRepository(db_session))
    payload = AgentControlActionInput(
        workspace_id=workspace.id,
        user_id=f"workspace:{workspace.id}",
        action_kind="reply.send",
        target_ref="conversation:123",
        proposed_payload={"text": "Salom"},
        risk_level="low",
        evidence_refs=["agent_run:hermes_run:idempotent-action"],
        approval_required=False,
        correlation_id="test-agent-control-idempotent",
        idempotency_key="reply-send:123",
    )

    first = await control.create_action(payload)
    second = await control.create_action(payload)

    assert second.action_id == first.action_id
    rows = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.workspace_id == workspace.id,
                CommercialActionProposalRecord.idempotency_key == "agent-control:reply-send:123",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_telegram_control_bot_approves_reply_from_inline_callback(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    control = AgentControlService(CommercialSpineRepository(db_session))
    reply = await control.create_action(
        AgentControlActionInput(
            workspace_id=workspace.id,
            user_id=f"workspace:{workspace.id}",
            action_kind="reply.send",
            target_ref="conversation:77",
            proposed_payload={"text": "Salom"},
            risk_level="low",
            evidence_refs=["agent_run:reply-77"],
            approval_required=True,
            correlation_id="corr-reply-bot",
            idempotency_key="reply-bot-1",
        )
    )
    fake_client = _FakeControlBotClient()
    service = TelegramControlBotService(session=db_session, client=fake_client)

    result = await service.handle_update(
        {
            "callback_query": {
                "id": "cb-reply-approve",
                "from": {"id": 4242},
                "data": f"oqim:a:{workspace.id}:{reply.action_id}",
                "message": {"message_id": 11, "chat": {"id": 9001}},
            }
        }
    )

    assert result.action == "approve"
    assert result.action_kind == "reply.send"
    assert result.status == "approved"
    assert fake_client.answered[0]["text"] == "Approved."
    assert fake_client.edited[0]["reply_markup"] == {"inline_keyboard": []}

    row = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.proposal_id == reply.action_id,
        )
    )
    assert row is not None
    assert row.lifecycle_state == "approved"
    assert row.action_type == "send_reply"


async def test_telegram_control_bot_rejects_reply_from_inline_callback(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    control = AgentControlService(CommercialSpineRepository(db_session))
    reply = await control.create_action(
        AgentControlActionInput(
            workspace_id=workspace.id,
            user_id=f"workspace:{workspace.id}",
            action_kind="reply.send",
            target_ref="conversation:78",
            proposed_payload={"text": "Salom"},
            risk_level="low",
            evidence_refs=["agent_run:reply-78"],
            approval_required=True,
            correlation_id="corr-reply-bot-reject",
            idempotency_key="reply-bot-reject-1",
        )
    )
    fake_client = _FakeControlBotClient()
    service = TelegramControlBotService(session=db_session, client=fake_client)

    result = await service.handle_update(
        {
            "callback_query": {
                "id": "cb-reply-reject",
                "from": {"id": 4242},
                "data": f"oqim:r:{workspace.id}:{reply.action_id}",
                "message": {"message_id": 13, "chat": {"id": 9001}},
            }
        }
    )

    assert result.action == "reject"
    assert result.action_kind == "reply.send"
    assert result.status == "rejected"
    assert fake_client.answered[0]["text"] == "Rejected."

    row = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.proposal_id == reply.action_id,
        )
    )
    assert row is not None
    assert row.lifecycle_state == "rejected"
    assert row.action_type == "send_reply"


async def test_telegram_control_bot_approves_knowledge_promotion_callback(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _business_scope(workspace)
    source_doc = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="source",
            title="Owner pasted price source",
            body_text="Starter paket narxi 40000 so'm.",
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:seller",
            source_kind="paste",
            correlation_id="corr-bot-knowledge-approve-source",
            idempotency_key="bot-knowledge-approve-source-1",
        )
    )
    proposal = await service.propose_candidate(
        KnowledgeCandidateInput(
            scope=scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="policy",
            proposed_payload={
                "topic": "starter_price",
                "answer": "Starter paket narxi 40000 so'm.",
            },
            evidence_refs=source_doc.source_refs,
            confidence=0.9,
            created_by_ref="agent:seller",
            hermes_run_id="hermes-run-knowledge-approve",
            correlation_id="corr-bot-knowledge-approve-candidate",
            idempotency_key="bot-knowledge-approve-candidate-1",
        )
    )
    fake_client = _FakeControlBotClient()
    bot = TelegramControlBotService(session=db_session, client=fake_client)

    result = await bot.handle_update(
        {
            "callback_query": {
                "id": "cb-knowledge-approve",
                "from": {"id": 4242},
                "data": f"oqim:a:{workspace.id}:{proposal.action.action_id}",
                "message": {"message_id": 14, "chat": {"id": 9001}},
            }
        }
    )

    assert result.action == "approve"
    assert result.action_kind == "knowledge.promote"
    assert result.status == "approved"
    assert fake_client.answered[0]["text"] == "Approved."

    candidate_row = await db_session.scalar(
        select(KnowledgeCandidateRecord).where(
            KnowledgeCandidateRecord.candidate_id == proposal.candidate.candidate_id,
        )
    )
    assert candidate_row is not None
    assert candidate_row.status == "approved"
    assert candidate_row.metadata_json["promoted_item_id"].startswith("knowledge:")


async def test_telegram_control_bot_rejects_knowledge_promotion_callback(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = KnowledgeMCPService(db_session)
    scope = _business_scope(workspace)
    source_doc = await service.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="source",
            title="Owner pasted source",
            body_text="Delivery source text.",
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:seller",
            source_kind="paste",
            correlation_id="corr-bot-knowledge-source",
            idempotency_key="bot-knowledge-source-1",
        )
    )
    proposal = await service.propose_candidate(
        KnowledgeCandidateInput(
            scope=scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="policy",
            proposed_payload={"topic": "delivery", "answer": "Delivery source text."},
            evidence_refs=source_doc.source_refs,
            confidence=0.8,
            created_by_ref="agent:seller",
            correlation_id="corr-bot-knowledge-candidate",
            idempotency_key="bot-knowledge-candidate-1",
        )
    )
    fake_client = _FakeControlBotClient()
    bot = TelegramControlBotService(session=db_session, client=fake_client)

    result = await bot.handle_update(
        {
            "callback_query": {
                "id": "cb-knowledge-reject",
                "from": {"id": 4242},
                "data": f"oqim:r:{workspace.id}:{proposal.action.action_id}",
                "message": {"message_id": 12, "chat": {"id": 9001}},
            }
        }
    )

    assert result.action == "reject"
    assert result.action_kind == "knowledge.promote"
    assert result.status == "rejected"
    assert fake_client.answered[0]["text"] == "Rejected."

    candidate_row = await db_session.scalar(
        select(KnowledgeCandidateRecord).where(
            KnowledgeCandidateRecord.candidate_id == proposal.candidate.candidate_id,
        )
    )
    assert candidate_row is not None
    assert candidate_row.status == "rejected"


def test_skills_rules_and_automations_are_not_ordinary_knowledge_items() -> None:
    scope = KnowledgeScope(owner_type="user", owner_id="user:1")

    for wrong_kind in ("skill", "rule", "automation"):
        with pytest.raises(ValidationError):
            KnowledgeSaveInput(
                scope=scope,
                kind=wrong_kind,
                title="Wrong store",
                body_text="This belongs in its own registry.",
                created_by_ref="agent:test",
                correlation_id=f"corr-{wrong_kind}",
                idempotency_key=f"wrong-{wrong_kind}",
            )
