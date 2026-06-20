"""OQIM tools registered into the packaged Hermes registry."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

from sqlalchemy import select

from app.db.session import async_session
from app.models.media_vault import MediaVaultRecord
from app.models.workspace import Workspace
from app.modules.agent_business_actions.service import AgentBusinessActionService
from app.modules.agent_control.contracts import AgentControlActionInput
from app.modules.agent_control.service import AgentControlService
from app.modules.agent_conversation_state.service import AgentConversationStateService
from app.modules.agent_runtime_v2.hermes.tool_context import current_tool_context
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
)
from app.modules.hermes_runtime.service import HermesRunService
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

_WS_RE = re.compile(r"\s+")
_KNOWLEDGE_TIMEOUT_S = 10
_LEGACY_RETRIEVAL_TOOL_NAMES = (
    "search_catalog_truth",
    "search_business_rules",
    "search_voice_examples",
    "recall_business_facts",
)


def _norm(query: str) -> str:
    return _WS_RE.sub(" ", (query or "").strip().lower())


async def _scope_for_knowledge_db(db, *, workspace_id: int, raw_scope: str | None) -> KnowledgeScope:
    scope = (raw_scope or "business").strip().lower()
    if scope != "personal":
        return KnowledgeScope(
            owner_type="workspace",
            owner_id=f"workspace:{workspace_id}",
            workspace_id=workspace_id,
        )
    telegram_user_id = await db.scalar(
        select(Workspace.telegram_user_id).where(Workspace.id == workspace_id)
    )
    owner_id = f"user:{telegram_user_id}" if telegram_user_id else f"workspace-user:{workspace_id}"
    return KnowledgeScope(owner_type="user", owner_id=owner_id)


async def _knowledge_search_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    raw_scope: str | None,
    query: str,
    collection_ids: list[str],
    tags: list[str],
    enable_semantic: bool,
    limit: int,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        result = await KnowledgeMCPService(db, enable_semantic=enable_semantic).search(
            KnowledgeSearchRequest(
                scope=scope,
                query=query,
                collection_ids=collection_ids,
                tags=tags,
                enable_semantic=enable_semantic,
                limit=limit,
            )
        )
        result_payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_search",
            correlation_id=f"knowledge-search:{workspace_id}",
            idempotency_key=f"knowledge-search:{workspace_id}:{_norm(query)}",
            payload={
                "query": query,
                "scope": scope.model_dump(mode="json"),
                "collection_ids": collection_ids,
                "tags": tags,
                "enable_semantic": enable_semantic,
                **_knowledge_result_metrics(result_payload, started_at=started_at),
            },
        )
        await db.commit()
    return result_payload


async def _knowledge_chat_memory_search_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    query: str,
    conversation_id: int | None,
    sender_types: list[str],
    limit: int,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with async_session() as db:
        result = await KnowledgeMCPService(db).search_chat_memory(
            KnowledgeChatMemorySearchRequest(
                workspace_id=workspace_id,
                query=query,
                conversation_id=conversation_id,
                sender_types=sender_types,  # type: ignore[arg-type]
                limit=limit,
            )
        )
        result_payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_search_chat_memory",
            correlation_id=f"knowledge-chat-search:{workspace_id}",
            idempotency_key=f"knowledge-chat-search:{workspace_id}:{_norm(query)}",
            payload={
                "query": query,
                "conversation_id": conversation_id,
                "sender_types": sender_types,
                **_knowledge_result_metrics(result_payload, started_at=started_at),
            },
        )
        await db.commit()
    return result_payload


async def _knowledge_catalog_search_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    query: str,
    query_modalities: list[str],
    include_media: bool,
    enable_semantic: bool,
    enable_rerank: bool,
    limit: int,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with async_session() as db:
        result = await KnowledgeMCPService(db).search_catalog(
            KnowledgeCatalogSearchRequest(
                workspace_id=workspace_id,
                query=query,
                query_modalities=query_modalities,  # type: ignore[arg-type]
                include_media=include_media,
                enable_semantic=enable_semantic,
                enable_rerank=enable_rerank,
                limit=limit,
            )
        )
        result_payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_search_catalog",
            correlation_id=f"knowledge-catalog-search:{workspace_id}",
            idempotency_key=f"knowledge-catalog-search:{workspace_id}:{_norm(query)}",
            payload={
                "query": query,
                "query_modalities": query_modalities,
                "include_media": include_media,
                "enable_semantic": enable_semantic,
                "enable_rerank": enable_rerank,
                **_knowledge_result_metrics(result_payload, started_at=started_at),
            },
        )
        await db.commit()
    return result_payload


async def _knowledge_media_search_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    query: str,
    query_modalities: list[str],
    enable_semantic: bool,
    enable_rerank: bool,
    limit: int,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with async_session() as db:
        result = await KnowledgeMCPService(db).search_media(
            KnowledgeMediaSearchRequest(
                workspace_id=workspace_id,
                query=query,
                query_modalities=query_modalities,  # type: ignore[arg-type]
                enable_semantic=enable_semantic,
                enable_rerank=enable_rerank,
                limit=limit,
            )
        )
        result_payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_search_media",
            correlation_id=f"knowledge-media-search:{workspace_id}",
            idempotency_key=f"knowledge-media-search:{workspace_id}:{_norm(query)}",
            payload={
                "query": query,
                "query_modalities": query_modalities,
                "enable_semantic": enable_semantic,
                "enable_rerank": enable_rerank,
                **_knowledge_result_metrics(result_payload, started_at=started_at),
            },
        )
        await db.commit()
    return result_payload


async def _knowledge_get_item_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    raw_scope: str | None,
    item_id: str,
) -> dict[str, Any]:
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        result = await KnowledgeMCPService(db).get_item(
            KnowledgeGetItemRequest(scope=scope, item_id=item_id)
        )
        payload = {"status": "not_found", "item_id": item_id}
        if result is not None:
            payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_get_item",
            tool_state="not_found" if result is None else "ok",
            correlation_id=f"knowledge-get-item:{workspace_id}",
            idempotency_key=f"knowledge-get-item:{workspace_id}:{item_id}",
            payload={
                "item_id": item_id,
                "scope": scope.model_dump(mode="json"),
                "found": result is not None,
                "source_refs": (
                    list(result.item.source_refs)
                    if result is not None
                    else []
                ),
            },
        )
        await db.commit()
    return payload


async def _knowledge_explain_sources_async(
    *,
    workspace_id: int,
    hermes_run_id: str | None = None,
    raw_scope: str | None,
    item_id: str,
) -> dict[str, Any]:
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        result = await KnowledgeMCPService(db).explain_sources(
            KnowledgeExplainSourcesRequest(scope=scope, item_id=item_id)
        )
        payload = {"status": "not_found", "item_id": item_id}
        if result is not None:
            payload = result.model_dump(mode="json")
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_explain_sources",
            tool_state="not_found" if result is None else "ok",
            correlation_id=f"knowledge-explain-sources:{workspace_id}",
            idempotency_key=f"knowledge-explain-sources:{workspace_id}:{item_id}",
            payload={
                "item_id": item_id,
                "scope": scope.model_dump(mode="json"),
                "found": result is not None,
                "source_refs": (
                    list(result.source_refs)
                    if result is not None
                    else []
                ),
                "citations": (
                    list(result.citations)
                    if result is not None
                    else []
                ),
            },
        )
        await db.commit()
    return payload


async def _knowledge_save_async(
    *,
    workspace_id: int,
    agent_id: int,
    hermes_run_id: str | None,
    raw_scope: str | None,
    kind: str,
    title: str,
    body_text: str,
    collection_ids: list[str],
    tags: list[str],
    created_by_ref: str,
    source_kind: str,
    correlation_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        item = await KnowledgeMCPService(
            db,
            embed_on_write=True,
            enable_semantic=True,
        ).save_item(
            KnowledgeSaveInput(
                scope=scope,
                kind=kind,  # type: ignore[arg-type]
                title=title,
                body_text=body_text,
                collection_ids=collection_ids,
                tags=tags,
                source_kind=source_kind,  # type: ignore[arg-type]
                authority_state="source",
                visibility="private" if scope.owner_type == "user" else "workspace",
                created_by="agent",
                created_by_ref=created_by_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        action = await _record_executed_knowledge_write(
            db=db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            scope=scope,
            item_id=item.item_id,
            operation=f"knowledge.{kind}.save",
            proposed_payload={
                "kind": kind,
                "title": title,
                "collection_ids": collection_ids,
                "tags": tags,
                "source_kind": source_kind,
            },
            evidence_refs=item.source_refs,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        await db.commit()
    return {
        **item.model_dump(mode="json"),
        "agent_control_action": action.model_dump(mode="json"),
    }


async def _knowledge_attach_to_collection_async(
    *,
    workspace_id: int,
    agent_id: int,
    hermes_run_id: str | None,
    raw_scope: str | None,
    item_id: str,
    collection_ids: list[str],
) -> dict[str, Any]:
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        item = await KnowledgeMCPService(db).attach_to_collection(
            KnowledgeAttachToCollectionInput(
                scope=scope,
                item_id=item_id,
                collection_ids=collection_ids,
            )
        )
        action = await _record_executed_knowledge_write(
            db=db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            scope=scope,
            item_id=item.item_id,
            operation="knowledge.attach_to_collection",
            proposed_payload={
                "item_id": item.item_id,
                "collection_ids": collection_ids,
            },
            evidence_refs=item.source_refs,
            correlation_id=f"knowledge-attach:{workspace_id}:{agent_id}:{item.item_id}",
            idempotency_key=f"knowledge-attach:{workspace_id}:{agent_id}:{item.item_id}:{','.join(collection_ids)}",
        )
        await db.commit()
    return {
        **item.model_dump(mode="json"),
        "agent_control_action": action.model_dump(mode="json"),
    }


async def _knowledge_tag_item_async(
    *,
    workspace_id: int,
    agent_id: int,
    hermes_run_id: str | None,
    raw_scope: str | None,
    item_id: str,
    tags: list[str],
) -> dict[str, Any]:
    async with async_session() as db:
        scope = await _scope_for_knowledge_db(
            db,
            workspace_id=workspace_id,
            raw_scope=raw_scope,
        )
        item = await KnowledgeMCPService(db).tag_item(
            KnowledgeTagItemInput(scope=scope, item_id=item_id, tags=tags)
        )
        action = await _record_executed_knowledge_write(
            db=db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            scope=scope,
            item_id=item.item_id,
            operation="knowledge.tag_item",
            proposed_payload={
                "item_id": item.item_id,
                "tags": tags,
            },
            evidence_refs=item.source_refs,
            correlation_id=f"knowledge-tag:{workspace_id}:{agent_id}:{item.item_id}",
            idempotency_key=f"knowledge-tag:{workspace_id}:{agent_id}:{item.item_id}:{','.join(tags)}",
        )
        await db.commit()
    return {
        **item.model_dump(mode="json"),
        "agent_control_action": action.model_dump(mode="json"),
    }


async def _knowledge_candidate_async(
    *,
    workspace_id: int,
    source_id: str,
    proposed_kind: str,
    proposed_payload: dict[str, Any],
    evidence_refs: list[str],
    confidence: float,
    created_by_ref: str,
    hermes_run_id: str | None,
    correlation_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await KnowledgeMCPService(db).propose_candidate(
            KnowledgeCandidateInput(
                scope=KnowledgeScope(
                    owner_type="workspace",
                    owner_id=f"workspace:{workspace_id}",
                    workspace_id=workspace_id,
                ),
                source_id=source_id,
                proposed_kind=proposed_kind,
                proposed_payload=proposed_payload,
                evidence_refs=evidence_refs,
                confidence=confidence,
                created_by_ref=created_by_ref,
                hermes_run_id=hermes_run_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await _record_knowledge_tool_event(
            db=db,
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="knowledge_propose_candidate",
            action_proposal_id=result.action.action_id,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:hermes-event",
            payload={
                "source_id": source_id,
                "proposed_kind": proposed_kind,
                "evidence_refs": evidence_refs,
                "confidence": confidence,
                "candidate_id": result.candidate.candidate_id,
                "action": result.action.model_dump(mode="json"),
            },
        )
        await db.commit()
    return result.model_dump(mode="json")


async def _conversation_set_state_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    summary: str,
    stage: str,
    active_intent: str | None,
    selected_items: list[dict[str, Any]],
    shown_prices: list[dict[str, Any]],
    customer_details: dict[str, Any],
    payment: dict[str, Any],
    fulfillment: dict[str, Any],
    missing_authority: list[str],
    next_best_action: str | None,
    risk_flags: list[str],
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentConversationStateService(db).set_state(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            summary=summary,
            stage=stage,
            active_intent=active_intent,
            selected_items=selected_items,
            shown_prices=shown_prices,
            customer_details=customer_details,
            payment=payment,
            fulfillment=fulfillment,
            missing_authority=missing_authority,
            next_best_action=next_best_action,
            risk_flags=risk_flags,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return result.compact_state()


async def _work_create_task_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    conversation_state_snapshot_id: int | None,
    task_kind: str,
    title: str,
    reason: str,
    priority: str,
    selected_item_refs: list[str],
    missing_authority: list[str],
    due_at: str | None,
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).create_task(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            conversation_state_snapshot_id=conversation_state_snapshot_id,
            task_kind=task_kind,
            title=title,
            reason=reason,
            priority=priority,
            selected_item_refs=selected_item_refs,
            missing_authority=missing_authority,
            due_at=due_at,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return {
            "task_ref": result.task_ref,
            "proposal_id": result.proposal_id,
            "status": result.status,
            "payload": result.payload,
        }


async def _owner_edit_doc_async(
    *,
    workspace_id: int,
    agent_id: int,
    section_key: str,
    body: str,
) -> dict[str, Any]:
    """Propose an AGENT.md section edit as an approval-gated action (spike #439).

    The owner/setup agent never writes AGENT.md directly. This persists a
    CommercialActionProposal (requires_approval=True) so the edit flows through
    the Action Runtime → owner-bot card → approve → execute → audit seam. The
    `agent.update_owner_config` executor (action_runtime) applies the section.
    """
    payload = {
        "op": "edit_doc",
        "agent_id": agent_id,
        "section_key": section_key,
        "body": body,
    }
    fingerprint = _payload_fingerprint(payload)
    proposal_id = f"owner_config:{workspace_id}:{fingerprint}"
    proposal = CommercialActionProposal(
        proposal_id=proposal_id,
        workspace_id=workspace_id,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_owner_config",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="medium",
        requires_approval=True,
        priority="medium",
        confidence=1.0,
        reason_code="owner_config_edit",
        source_refs=[f"owner:setup_agent:{agent_id}"],
        idempotency_key=proposal_id,
        payload=payload,
    )
    async with async_session() as db:
        inserted = await CommercialSpineRepository(db).persist_action_proposal(proposal)
        await db.commit()
    return {"status": "ok", "proposal_id": proposal_id, "deduped": not inserted}


async def _media_store_async(
    *,
    workspace_id: int,
    handle: str,
    cdn_url: str,
    media_type: str,
    mime_type: str | None = None,
    file_name: str | None = None,
    caption: str | None = None,
) -> dict[str, Any]:
    """Store (or upsert) a reusable media asset in the owner's vault (spike #439).

    Spike behaviour: persists a MediaVaultRecord directly with the provided
    cdn_url. PRODUCTION should route this through the approval seam (the
    agent.update_owner_config executor) and the sidecar vault.store path that
    uploads bytes once to Telegram cloud and fills the pointer columns.
    """
    # The seller send path requires an http(s) URL (the resolver hands it to the
    # sidecar). Reject anything else at curation time instead of deferring the
    # failure to a customer-facing send (and to avoid storing a fetch/SSRF vector).
    if not (cdn_url.startswith("https://") or cdn_url.startswith("http://")):
        return {"status": "blocked", "reason": "invalid_url"}
    async with async_session() as db:
        existing = await db.scalar(
            select(MediaVaultRecord).where(
                MediaVaultRecord.workspace_id == workspace_id,
                MediaVaultRecord.handle == handle,
            )
        )
        if existing is not None:
            existing.cdn_url = cdn_url
            existing.media_type = media_type
            existing.mime_type = mime_type
            existing.file_name = file_name
            existing.caption = caption
            await db.commit()
            return {"status": "ok", "handle": handle, "updated": True}
        db.add(
            MediaVaultRecord(
                workspace_id=workspace_id,
                handle=handle,
                media_type=media_type,
                cdn_url=cdn_url,
                mime_type=mime_type,
                file_name=file_name,
                caption=caption,
                created_by="owner",
            )
        )
        await db.commit()
        return {"status": "ok", "handle": handle, "updated": False}


async def _media_list_async(*, workspace_id: int) -> dict[str, Any]:
    """List the owner's curated media vault (READ-tier; no approval)."""
    async with async_session() as db:
        rows = (
            await db.scalars(
                select(MediaVaultRecord)
                .where(MediaVaultRecord.workspace_id == workspace_id)
                .order_by(MediaVaultRecord.created_at)
            )
        ).all()
    return {
        "status": "ok",
        "items": [
            {
                "handle": row.handle,
                "media_type": row.media_type,
                "caption": row.caption,
                "mime_type": row.mime_type,
            }
            for row in rows
        ],
    }


async def _work_handoff_async(**kwargs: Any) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).handoff(**kwargs)
        await db.commit()
        return {
            "status": "ok",
            "kind": result.kind,
            "task_ref": result.task_ref,
            "notification_ref": result.notification_ref,
        }


async def _owner_notify_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    task_ref: str | None,
    order_ref: str | None,
    title: str,
    summary: str,
    recommended_action: str,
    selected_item_refs: list[str],
    shown_price_refs: list[str],
    missing_authority: list[str],
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).notify_owner(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            task_ref=task_ref,
            order_ref=order_ref,
            title=title,
            summary=summary,
            recommended_action=recommended_action,
            selected_item_refs=selected_item_refs,
            shown_price_refs=shown_price_refs,
            missing_authority=missing_authority,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return {
            "notification_ref": result.notification_ref,
            "status": result.status,
            "bot_payload": result.bot_payload,
        }


async def _conversation_record_intelligence_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    lead_stage: str,
    buying_signals: list[str],
    objections: list[str],
    preferences: dict[str, Any],
    next_best_action: str | None,
    owner_notes: list[str],
    risk_flags: list[str],
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).record_intelligence(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            lead_stage=lead_stage,
            buying_signals=buying_signals,
            objections=objections,
            preferences=preferences,
            next_best_action=next_best_action,
            owner_notes=owner_notes,
            risk_flags=risk_flags,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return {
            "intelligence_ref": result.intelligence_ref,
            "status": result.status,
            "payload": result.payload,
        }


async def _commerce_create_checkout_intent_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    selected_items: list[dict[str, Any]],
    shown_prices: list[dict[str, Any]],
    payment_method: str | None,
    fulfillment_method: str | None,
    status: str,
    missing_fields: list[str],
    linked_task_refs: list[str],
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).create_checkout_intent(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            selected_items=selected_items,
            shown_prices=shown_prices,
            payment_method=payment_method,
            fulfillment_method=fulfillment_method,
            status=status,
            missing_fields=missing_fields,
            linked_task_refs=linked_task_refs,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return {
            "checkout_ref": result.checkout_ref,
            "order_ref": result.order_ref,
            "proposal_id": result.proposal_id,
            "status": result.status,
            "missing_fields": result.missing_fields,
            "authority_refs": result.authority_refs,
            "payload": result.payload,
        }


async def _commerce_create_order_async(
    *,
    workspace_id: int,
    agent_session_id: int,
    agent_id: int,
    conversation_id: int,
    customer_id: int | None,
    hermes_run_id: str | None,
    selected_items: list[dict[str, Any]],
    shown_prices: list[dict[str, Any]],
    payment_method: str | None,
    fulfillment_method: str | None,
    status: str,
    missing_fields: list[str],
    linked_task_refs: list[str],
    source_refs: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    async with async_session() as db:
        result = await AgentBusinessActionService(db).create_order_intent(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            selected_items=selected_items,
            shown_prices=shown_prices,
            payment_method=payment_method,
            fulfillment_method=fulfillment_method,
            status=status,
            missing_fields=missing_fields,
            linked_task_refs=linked_task_refs,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        await db.commit()
        return {
            "order_ref": result.order_ref,
            "checkout_ref": result.checkout_ref,
            "proposal_id": result.proposal_id,
            "status": result.status,
            "missing_fields": result.missing_fields,
            "authority_refs": result.authority_refs,
            "payload": result.payload,
        }


async def _record_executed_knowledge_write(
    *,
    db,
    workspace_id: int,
    agent_id: int,
    hermes_run_id: str | None,
    scope: KnowledgeScope,
    item_id: str,
    operation: str,
    proposed_payload: dict[str, Any],
    evidence_refs: list[str],
    correlation_id: str,
    idempotency_key: str,
) -> Any:
    control = AgentControlService(CommercialSpineRepository(db))
    action = await control.create_action(
        AgentControlActionInput(
            workspace_id=workspace_id,
            user_id=scope.owner_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            action_kind="knowledge.write",
            target_ref=item_id,
            proposed_payload={
                "operation": operation,
                **proposed_payload,
            },
            risk_level="low",
            evidence_refs=evidence_refs,
            approval_required=False,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:agent-control",
        )
    )
    await control.mark_executed(
        workspace_id=workspace_id,
        action_id=action.action_id,
        actor_ref=f"agent:{agent_id}",
        correlation_id=correlation_id,
        execution_payload={
            "operation": operation,
            "item_id": item_id,
        },
    )
    executed = await control.get_action(workspace_id=workspace_id, action_id=action.action_id)
    final_action = executed or action
    await _record_knowledge_tool_event(
        db=db,
        workspace_id=workspace_id,
        hermes_run_id=hermes_run_id,
        tool_name=operation,
        action_proposal_id=final_action.action_id,
        correlation_id=correlation_id,
        idempotency_key=f"{idempotency_key}:hermes-event",
        payload={
            "operation": operation,
            "item_id": item_id,
            "scope": scope.model_dump(mode="json"),
            "action": final_action.model_dump(mode="json"),
        },
    )
    return final_action


async def _record_knowledge_tool_event(
    *,
    db,
    workspace_id: int,
    hermes_run_id: str | None,
    tool_name: str,
    payload: dict[str, Any],
    correlation_id: str,
    idempotency_key: str,
    tool_state: str = "ok",
    action_proposal_id: str | None = None,
) -> None:
    if not hermes_run_id:
        return
    try:
        await HermesRunService(db).record_event(
            HermesRunEventInput(
                run_id=hermes_run_id,
                workspace_id=workspace_id,
                kind=HermesRunEventKind.TOOL_CALLED,
                visibility="internal",
                tool_name=tool_name,
                tool_state=tool_state,
                action_proposal_id=action_proposal_id,
                payload=payload,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        )
    except Exception:
        # Tool audit is best-effort: missing local run fixtures must not turn a
        # successful knowledge operation into a failed customer-facing turn.
        return


def _knowledge_result_citations(result: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for hit in list(result.get("hits") or []):
        if not isinstance(hit, dict):
            continue
        item = hit.get("item") if isinstance(hit.get("item"), dict) else {}
        for citation in list(hit.get("citations") or []):
            if not isinstance(citation, dict):
                continue
            citations.append(
                {
                    "item_id": item.get("item_id"),
                    "title": item.get("title"),
                    "kind": item.get("kind"),
                    "authority_state": item.get("authority_state"),
                    **citation,
                }
            )
    return citations[:10]


def _knowledge_result_metrics(
    result: dict[str, Any],
    *,
    started_at: float,
) -> dict[str, Any]:
    hits = [hit for hit in list(result.get("hits") or []) if isinstance(hit, dict)]
    citations = _knowledge_result_citations(result)
    source_refs: list[str] = []
    retrieval_channels: list[str] = []
    scores: list[float] = []
    for hit in hits:
        item = hit.get("item") if isinstance(hit.get("item"), dict) else {}
        source_refs.extend(str(ref) for ref in list(item.get("source_refs") or []) if ref)
        raw_score = hit.get("score")
        if isinstance(raw_score, int | float):
            scores.append(float(raw_score))
        for citation in list(hit.get("citations") or []):
            if not isinstance(citation, dict):
                continue
            source_refs.extend(str(ref) for ref in list(citation.get("source_refs") or []) if ref)
            if citation.get("source_id"):
                source_refs.append(str(citation["source_id"]))
            retrieval_channels.extend(
                str(channel)
                for channel in list(citation.get("retrieval_channels") or [])
                if channel
            )
    unique_source_refs = list(dict.fromkeys(source_refs))
    unique_channels = list(dict.fromkeys(retrieval_channels))
    return {
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "hit_count": len(hits),
        "citation_count": len(citations),
        "source_ref_count": len(unique_source_refs),
        "retrieval_channels": unique_channels,
        "top_score": max(scores) if scores else None,
        "evidence_backed": bool(hits) and bool(citations) and bool(unique_source_refs),
        "citations": citations,
    }


def _run_knowledge_coro(ctx, coro, *, error_prefix: str) -> dict[str, Any]:
    if ctx.loop is None:
        coro.close()
        ctx.tool_errors.append(f"{error_prefix}:no_loop")
        return {"status": "degraded", "error": "no_loop"}
    fut = None
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, ctx.loop)
        return fut.result(timeout=_KNOWLEDGE_TIMEOUT_S)
    except Exception as exc:
        if fut is not None:
            fut.cancel()
        else:
            coro.close()
        ctx.tool_errors.append(f"{error_prefix}_failed:{type(exc).__name__}")
        return {"status": "degraded", "error": type(exc).__name__}


def knowledge_search(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"status": "empty", "note": "A search query is required."}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_search_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope"),
            query=query,
            collection_ids=_string_list(args.get("collection_ids")),
            tags=_string_list(args.get("tags")),
            enable_semantic=_bool_arg(args.get("enable_semantic"), default=True),
            limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
        ),
        error_prefix="knowledge_search",
    )
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def knowledge_search_chat_memory(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"status": "empty", "note": "A chat-memory search query is required."}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_chat_memory_search_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            query=query,
            conversation_id=_optional_positive_int(args.get("conversation_id")),
            sender_types=_allowed_sender_types(args.get("sender_types")),
            limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
        ),
        error_prefix="knowledge_chat_memory_search",
    )
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def knowledge_search_catalog(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"status": "empty", "note": "A catalog search query is required."}, ensure_ascii=False)
    if ctx.max_catalog_searches is not None and ctx.catalog_search_count >= ctx.max_catalog_searches:
        ctx.tool_errors.append("knowledge_catalog_search:catalog_search_limit")
        return json.dumps(
            {
                "status": "blocked",
                "reason": "catalog_search_limit",
                "limit": ctx.max_catalog_searches,
            },
            ensure_ascii=False,
        )
    ctx.catalog_search_count += 1
    enable_semantic = (
        ctx.catalog_enable_semantic
        if ctx.catalog_enable_semantic is not None
        else _bool_arg(args.get("enable_semantic"), default=True)
    )
    enable_rerank = (
        ctx.catalog_enable_rerank
        if ctx.catalog_enable_rerank is not None
        else _bool_arg(args.get("enable_rerank"), default=True)
    )
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_catalog_search_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            query=query,
            query_modalities=_allowed_modalities(args.get("query_modalities")),
            include_media=_bool_arg(args.get("include_media"), default=True),
            enable_semantic=enable_semantic,
            enable_rerank=enable_rerank,
            limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
        ),
        error_prefix="knowledge_catalog_search",
    )
    ctx.tool_authority_lines.extend(_catalog_authority_lines_from_tool_payload(payload))
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _catalog_authority_lines_from_tool_payload(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        item = hit.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("authority_state") != "approved":
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        fact_type = str(metadata.get("fact_type") or "")
        value = metadata.get("value") if isinstance(metadata.get("value"), dict) else {}
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if fact_type in {"catalog_product", "catalog_variant"}:
            lines.append(f"[PRODUCT] {title}")
        elif fact_type == "catalog_offer":
            price = _catalog_value_text(value, "price", "amount", "narx")
            currency = _catalog_value_text(value, "currency")
            stock = _catalog_value_text(value, "stock", "availability")
            price_text = " ".join(part for part in (price, currency) if part)
            detail = price_text or stock
            lines.append(f"[OFFER] {title}: {detail}" if detail else f"[OFFER] {title}")
        elif fact_type == "catalog_media":
            media_text = _catalog_value_text(value, "caption", "visual_summary", "ocr_text")
            lines.append(f"[MEDIA] {title}: {media_text}" if media_text else f"[MEDIA] {title}")
    return list(dict.fromkeys(line for line in lines if line.strip()))


def _catalog_value_text(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = value.get(key)
        if raw not in (None, ""):
            return str(raw).strip()
    return ""


def knowledge_search_media(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"status": "empty", "note": "A media search query is required."}, ensure_ascii=False)
    modalities = _allowed_modalities(args.get("query_modalities")) or ["image"]
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_media_search_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            query=query,
            query_modalities=modalities,
            enable_semantic=_bool_arg(args.get("enable_semantic"), default=True),
            enable_rerank=_bool_arg(args.get("enable_rerank"), default=True),
            limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
        ),
        error_prefix="knowledge_media_search",
    )
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def knowledge_get_item(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    item_id = (args.get("item_id") or "").strip()
    if not item_id:
        return json.dumps({"status": "empty", "note": "item_id is required."}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_get_item_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope"),
            item_id=item_id,
        ),
        error_prefix="knowledge_get_item",
    )
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def knowledge_explain_sources(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    item_id = (args.get("item_id") or "").strip()
    if not item_id:
        return json.dumps({"status": "empty", "note": "item_id is required."}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_explain_sources_async(
            workspace_id=ctx.workspace_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope"),
            item_id=item_id,
        ),
        error_prefix="knowledge_explain_sources",
    )
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def knowledge_save_script(args: dict, **kw) -> str:
    return _knowledge_save_tool(args, kind="script")


def knowledge_save_note(args: dict, **kw) -> str:
    return _knowledge_save_tool(args, kind="note")


def knowledge_create_source_doc(args: dict, **kw) -> str:
    return _knowledge_save_tool(args, kind="source", default_scope="business", source_kind="paste")


def knowledge_attach_to_collection(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    item_id = (args.get("item_id") or "").strip()
    collection_ids = _string_list(args.get("collection_ids"))
    if not item_id or not collection_ids:
        return json.dumps(
            {"status": "empty", "note": "item_id and collection_ids are required."},
            ensure_ascii=False,
        )
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_attach_to_collection_async(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope"),
            item_id=item_id,
            collection_ids=collection_ids,
        ),
        error_prefix="knowledge_attach_to_collection",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "item": payload}, ensure_ascii=False)


def knowledge_tag_item(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    item_id = (args.get("item_id") or "").strip()
    tags = _string_list(args.get("tags"))
    if not item_id or not tags:
        return json.dumps(
            {"status": "empty", "note": "item_id and tags are required."},
            ensure_ascii=False,
        )
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_tag_item_async(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope"),
            item_id=item_id,
            tags=tags,
        ),
        error_prefix="knowledge_tag_item",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "item": payload}, ensure_ascii=False)


def knowledge_propose_candidate(args: dict, **kw) -> str:
    return json.dumps(_knowledge_candidate_tool(args), ensure_ascii=False)


def knowledge_propose_catalog_update(args: dict, **kw) -> str:
    return json.dumps(
        _knowledge_candidate_tool(args, proposed_kind="catalog_product"),
        ensure_ascii=False,
    )


def knowledge_propose_policy_update(args: dict, **kw) -> str:
    return json.dumps(
        _knowledge_candidate_tool(args, proposed_kind="policy"),
        ensure_ascii=False,
    )


def knowledge_propose_faq_update(args: dict, **kw) -> str:
    return json.dumps(
        _knowledge_candidate_tool(args, proposed_kind="faq"),
        ensure_ascii=False,
    )


def knowledge_propose_rule(args: dict, **kw) -> str:
    return json.dumps(
        _knowledge_candidate_tool(args, proposed_kind="rule"),
        ensure_ascii=False,
    )


def knowledge_extract_candidates(args: dict, **kw) -> str:
    candidates = args.get("candidates")
    source_id = (args.get("source_id") or "").strip()
    if not source_id or not isinstance(candidates, list) or not candidates:
        return json.dumps(
            {"status": "empty", "note": "source_id and candidates are required."},
            ensure_ascii=False,
        )
    proposals: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:10]):
        if not isinstance(candidate, dict):
            errors.append({"index": index, "error": "candidate_not_object"})
            continue
        proposed_payload = candidate.get("proposed_payload") or candidate.get("payload")
        proposed_kind = str(
            candidate.get("proposed_kind") or candidate.get("kind") or ""
        ).strip()
        if not proposed_kind or not isinstance(proposed_payload, dict):
            errors.append({"index": index, "error": "candidate_missing_kind_or_payload"})
            continue
        result = _knowledge_candidate_tool(
            {
                "source_id": source_id,
                "proposed_kind": proposed_kind,
                "proposed_payload": proposed_payload,
                "evidence_refs": _string_list(candidate.get("evidence_refs"))
                or _string_list(args.get("evidence_refs"))
                or [source_id],
                "confidence": candidate.get("confidence") or args.get("confidence") or 0.75,
            }
        )
        if result.get("status") == "ok":
            proposals.append(result)
        else:
            errors.append({"index": index, "error": result})
    return json.dumps(
        {
            "status": "ok" if proposals else "empty",
            "proposals": proposals,
            "errors": errors,
        },
        ensure_ascii=False,
    )


def conversation_set_state(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("conversation_set_state:no_agent_session")
        return json.dumps(
            {"status": "blocked", "reason": "no_agent_session"},
            ensure_ascii=False,
        )
    summary = (args.get("summary") or "").strip()
    stage = (args.get("stage") or "unknown").strip()
    active_intent = (args.get("active_intent") or "").strip() or None
    state_payload = {
        "summary": summary,
        "stage": stage,
        "active_intent": active_intent,
        "selected_items": _object_list(args.get("selected_items")),
        "shown_prices": _object_list(args.get("shown_prices")),
        "customer_details": _object_arg(args.get("customer_details")),
        "payment": _object_arg(args.get("payment")),
        "fulfillment": _object_arg(args.get("fulfillment")),
        "missing_authority": _string_list(args.get("missing_authority")),
        "next_best_action": (args.get("next_best_action") or "").strip() or None,
        "risk_flags": _string_list(args.get("risk_flags")),
        "source_refs": _string_list(args.get("source_refs")),
    }
    idempotency_key = (args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = (
            "conversation-state:"
            f"{ctx.workspace_id}:{ctx.agent_session_id}:"
            f"{_payload_fingerprint(state_payload)}"
        )
    payload = _run_knowledge_coro(
        ctx,
        _conversation_set_state_async(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            idempotency_key=idempotency_key,
            **state_payload,
        ),
        error_prefix="conversation_set_state",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "conversation_state": payload}, ensure_ascii=False)


def work_create_task(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("work_create_task:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    title = (args.get("title") or "").strip()
    reason = (args.get("reason") or args.get("summary") or "").strip()
    if not title or not reason:
        return json.dumps({"status": "empty", "note": "title and reason are required"}, ensure_ascii=False)
    key_payload = {
        "task_kind": args.get("task_kind") or args.get("kind") or "business",
        "title": title,
        "reason": reason,
        "selected_item_refs": _string_list(args.get("selected_item_refs")),
        "missing_authority": _string_list(args.get("missing_authority")),
        "source_refs": _string_list(args.get("source_refs")),
    }
    idempotency_key = (args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = (
            "work.create_task:"
            f"{ctx.workspace_id}:{ctx.agent_session_id}:"
            f"{_payload_fingerprint(key_payload)}"
        )
    payload = _run_knowledge_coro(
        ctx,
        _work_create_task_async(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            conversation_state_snapshot_id=_optional_positive_int(
                args.get("conversation_state_snapshot_id")
            ),
            task_kind=str(key_payload["task_kind"]),
            title=title,
            reason=reason,
            priority=(args.get("priority") or "medium").strip(),
            selected_item_refs=key_payload["selected_item_refs"],
            missing_authority=key_payload["missing_authority"],
            due_at=(args.get("due_at") or "").strip() or None,
            source_refs=key_payload["source_refs"],
            idempotency_key=idempotency_key,
        ),
        error_prefix="work_create_task",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    task_ref = str(payload.get("task_ref") or "").strip()
    if task_ref:
        ctx.business_action_refs.append(task_ref)
    return json.dumps({"status": "ok", "owner_task": payload}, ensure_ascii=False)


_OWNER_DOC_SECTION_KEYS = (
    "role_mission",
    "capabilities",
    "behavior_rules",
    "approval_rules",
    "examples",
    "must_never",
)


def owner_edit_doc(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    section_key = (args.get("section_key") or "").strip()
    body = (args.get("body") or "").strip()
    if section_key not in _OWNER_DOC_SECTION_KEYS:
        return json.dumps(
            {"status": "blocked", "reason": "invalid_section_key"}, ensure_ascii=False
        )
    if not body:
        return json.dumps({"status": "empty", "note": "body is required"}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _owner_edit_doc_async(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            section_key=section_key,
            body=body,
        ),
        error_prefix="owner_edit_doc",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "owner_config": payload}, ensure_ascii=False)


_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "metric": {
            "type": "string",
            "enum": ["conversations", "customers"],
            "description": "Which workspace count to read (0a: conversations or customers).",
        }
    },
    "required": ["metric"],
}

_ASK_METRIC_MODELS = {
    "conversations": ("app.models.conversation", "Conversation"),
    "customers": ("app.models.customer", "Customer"),
}


async def _ask_async(*, workspace_id: int, metric: str, session: Any = None) -> dict[str, Any]:
    import importlib

    from sqlalchemy import func, select

    module_path, cls_name = _ASK_METRIC_MODELS[metric]
    model = getattr(importlib.import_module(module_path), cls_name)
    stmt = (
        select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
    )
    # `session` is injected by tests (so the count sees their in-transaction rows);
    # production passes none and we open our own session on the runtime loop.
    if session is not None:
        count = (await session.execute(stmt)).scalar_one()
    else:
        async with async_session() as db:
            count = (await db.execute(stmt)).scalar_one()
    return {"status": "ok", "metric": metric, "count": int(count)}


def ask(args: dict, **kw) -> str:
    """READ verb (no approval): answer a simple structured count about the
    workspace. Phase 0a supports conversations/customers; richer BI/analytics
    (the precompute + 4-layer retrieval) lands in Phase 2."""
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    metric = (args.get("metric") or "").strip()
    if metric not in _ASK_METRIC_MODELS:
        return json.dumps(
            {"status": "blocked", "reason": "invalid_metric"}, ensure_ascii=False
        )
    payload = _run_knowledge_coro(
        ctx, _ask_async(workspace_id=ctx.workspace_id, metric=metric), error_prefix="ask"
    )
    return json.dumps(payload, ensure_ascii=False)


_MEDIA_TYPES = ("photo", "video", "document")


def media_store(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    handle = (args.get("handle") or "").strip()
    cdn_url = (args.get("cdn_url") or args.get("url") or "").strip()
    media_type = (args.get("media_type") or "photo").strip()
    if not handle or not cdn_url:
        return json.dumps(
            {"status": "empty", "note": "handle and cdn_url are required"},
            ensure_ascii=False,
        )
    if media_type not in _MEDIA_TYPES:
        return json.dumps(
            {"status": "blocked", "reason": "invalid_media_type"}, ensure_ascii=False
        )
    payload = _run_knowledge_coro(
        ctx,
        _media_store_async(
            workspace_id=ctx.workspace_id,
            handle=handle,
            cdn_url=cdn_url,
            media_type=media_type,
            mime_type=(args.get("mime_type") or "").strip() or None,
            file_name=(args.get("file_name") or "").strip() or None,
            caption=(args.get("caption") or "").strip() or None,
        ),
        error_prefix="media_store",
    )
    if payload.get("status") != "ok":
        # degraded / blocked (e.g. invalid_url) — relay as-is, do not wrap as ok.
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "media": payload}, ensure_ascii=False)


def media_list(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    payload = _run_knowledge_coro(
        ctx,
        _media_list_async(workspace_id=ctx.workspace_id),
        error_prefix="media_list",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "media": payload}, ensure_ascii=False)


def work_handoff(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("work_handoff:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    kind = (args.get("kind") or "").strip()
    title = (args.get("title") or "").strip()
    detail = (args.get("detail") or args.get("reason") or "").strip()
    if kind not in {"lead", "support", "complaint", "human_requested"}:
        return json.dumps({"status": "blocked", "reason": "invalid_kind"}, ensure_ascii=False)
    if not title or not detail:
        return json.dumps({"status": "empty", "note": "title and detail are required"}, ensure_ascii=False)
    idempotency_key = (args.get("idempotency_key") or "").strip() or (
        "work.handoff:"
        f"{ctx.workspace_id}:{ctx.agent_session_id}:"
        f"{_payload_fingerprint({'kind': kind, 'title': title, 'detail': detail})}"
    )
    payload = _run_knowledge_coro(
        ctx,
        _work_handoff_async(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            kind=kind,
            title=title,
            detail=detail,
            customer_name=(args.get("customer_name") or "").strip() or None,
            customer_phone=(args.get("customer_phone") or "").strip() or None,
            idempotency_key=idempotency_key,
        ),
        error_prefix="work_handoff",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    for ref_key in ("task_ref", "notification_ref"):
        ref = str(payload.get(ref_key) or "").strip()
        if ref:
            ctx.business_action_refs.append(ref)
    # kind-tagged ref so the dispatcher can derive turn-state facts from
    # committed refs (spec 2026-06-10) without re-reading projections
    ctx.business_action_refs.append(f"handoff:{kind}")
    return json.dumps(payload, ensure_ascii=False)


def owner_notify(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("owner_notify:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    title = (args.get("title") or "").strip()
    summary = (args.get("summary") or "").strip()
    recommended_action = (args.get("recommended_action") or "").strip()
    if not title or not summary or not recommended_action:
        return json.dumps(
            {"status": "empty", "note": "title, summary and recommended_action are required"},
            ensure_ascii=False,
        )
    key_payload = {
        "task_ref": (args.get("task_ref") or "").strip() or None,
        "order_ref": (args.get("order_ref") or "").strip() or None,
        "title": title,
        "summary": summary,
        "recommended_action": recommended_action,
        "selected_item_refs": _string_list(args.get("selected_item_refs")),
        "shown_price_refs": _string_list(args.get("shown_price_refs")),
        "missing_authority": _string_list(args.get("missing_authority")),
        "source_refs": _string_list(args.get("source_refs")),
    }
    idempotency_key = (args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = (
            "owner.notify:"
            f"{ctx.workspace_id}:{ctx.agent_session_id}:"
            f"{_payload_fingerprint(key_payload)}"
        )
    payload = _run_knowledge_coro(
        ctx,
        _owner_notify_async(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            idempotency_key=idempotency_key,
            **key_payload,
        ),
        error_prefix="owner_notify",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    notification_ref = str(payload.get("notification_ref") or "").strip()
    if notification_ref:
        ctx.business_action_refs.append(notification_ref)
    return json.dumps({"status": "ok", "owner_notification": payload}, ensure_ascii=False)


def _coerce_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def conversation_record(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("conversation_record:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    stage = (args.get("stage") or "").strip()
    if not stage:
        return json.dumps({"status": "empty", "note": "stage is required"}, ensure_ascii=False)
    handoff_in = _object_arg(args.get("handoff"))
    handoff = {
        "needed": _bool_arg(handoff_in.get("needed"), default=False),
        "kind": (handoff_in.get("kind") or "").strip(),
        "reason": (handoff_in.get("reason") or "").strip(),
    }
    record = {
        "stage": stage,
        "deal_value": _coerce_number(args.get("deal_value")),
        "currency": (args.get("currency") or "UZS").strip() or "UZS",
        "items": _object_list(args.get("items")),
        "customer": _object_arg(args.get("customer")),
        "summary": (args.get("summary") or "").strip(),
        "buying_signals": _string_list(args.get("buying_signals")),
        "objections": _string_list(args.get("objections")),
        "next_best_action": (args.get("next_best_action") or "").strip(),
        "risk_flags": _string_list(args.get("risk_flags")),
        "opted_out": _bool_arg(args.get("opted_out"), default=False),
        "payment_method": (args.get("payment_method") or "").strip(),
        "fulfillment": (args.get("fulfillment") or "").strip(),
        "pipeline_key": (args.get("pipeline_key") or "").strip(),
        "custom_fields": _object_list(args.get("custom_fields")),
        "tags": _string_list(args.get("tags")),
        "handoff": handoff,
    }
    ctx.record_payload = record
    if handoff["needed"] and handoff["kind"] in {
        "lead", "support", "complaint", "refund", "human_requested"
    }:
        ctx.business_action_refs.append(f"handoff:{handoff['kind']}")
    return json.dumps({"status": "ok", "record": record}, ensure_ascii=False)


def conversation_record_intelligence(args: dict, **kw) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append("conversation_record_intelligence:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    key_payload = {
        "lead_stage": (args.get("lead_stage") or args.get("stage") or "unknown").strip(),
        "buying_signals": _string_list(args.get("buying_signals")),
        "objections": _string_list(args.get("objections")),
        "preferences": _object_arg(args.get("preferences")),
        "next_best_action": (args.get("next_best_action") or "").strip() or None,
        "owner_notes": _string_list(args.get("owner_notes")),
        "risk_flags": _string_list(args.get("risk_flags")),
        "source_refs": _string_list(args.get("source_refs")),
    }
    # turn-local capture: the dispatcher's facts reducer reads the turn's
    # intelligence judgment from ctx without re-reading projections
    ctx.intelligence_payloads.append(
        {
            "lead_stage": (args.get("lead_stage") or "").strip(),
            "buying_signals": _string_list(args.get("buying_signals")),
            "objections": _string_list(args.get("objections")),
            "owner_notes": _string_list(args.get("owner_notes")),
            "next_best_action": (args.get("next_best_action") or "").strip(),
            "opted_out": args.get("opted_out") is True,
        }
    )
    idempotency_key = (args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = (
            "conversation.record_intelligence:"
            f"{ctx.workspace_id}:{ctx.agent_session_id}:"
            f"{_payload_fingerprint(key_payload)}"
        )
    payload = _run_knowledge_coro(
        ctx,
        _conversation_record_intelligence_async(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            idempotency_key=idempotency_key,
            **key_payload,
        ),
        error_prefix="conversation_record_intelligence",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    intelligence_ref = str(payload.get("intelligence_ref") or "").strip()
    if intelligence_ref:
        ctx.business_action_refs.append(intelligence_ref)
    return json.dumps({"status": "ok", "customer_intelligence": payload}, ensure_ascii=False)


def commerce_create_checkout_intent(args: dict, **kw) -> str:
    return _commerce_intent_tool(
        args,
        tool_name="commerce.create_checkout_intent",
        result_key="checkout_intent",
        coro_factory=_commerce_create_checkout_intent_async,
        error_prefix="commerce_create_checkout_intent",
    )


def commerce_create_order(args: dict, **kw) -> str:
    return _commerce_intent_tool(
        args,
        tool_name="commerce.create_order",
        result_key="order_intent",
        coro_factory=_commerce_create_order_async,
        error_prefix="commerce_create_order",
    )


def _commerce_intent_tool(
    args: dict,
    *,
    tool_name: str,
    result_key: str,
    coro_factory,
    error_prefix: str,
) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    if ctx.agent_session_id is None or ctx.conversation_id is None:
        ctx.tool_errors.append(f"{error_prefix}:no_agent_session")
        return json.dumps({"status": "blocked", "reason": "no_agent_session"}, ensure_ascii=False)
    selected_items = _object_list(args.get("selected_items"))
    if not selected_items:
        return json.dumps({"status": "empty", "note": "selected_items are required"}, ensure_ascii=False)
    key_payload = {
        "selected_items": selected_items,
        "shown_prices": _object_list(args.get("shown_prices")),
        "payment_method": (args.get("payment_method") or "").strip() or None,
        "fulfillment_method": (args.get("fulfillment_method") or "").strip() or None,
        "status": (args.get("status") or "pending").strip() or "pending",
        "missing_fields": _string_list(args.get("missing_fields")),
        "linked_task_refs": _string_list(args.get("linked_task_refs")),
        "source_refs": _string_list(args.get("source_refs")),
    }
    idempotency_key = (args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = (
            f"{tool_name}:"
            f"{ctx.workspace_id}:{ctx.agent_session_id}:"
            f"{_payload_fingerprint(key_payload)}"
        )
    payload = _run_knowledge_coro(
        ctx,
        coro_factory(
            workspace_id=ctx.workspace_id,
            agent_session_id=ctx.agent_session_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            customer_id=None,
            hermes_run_id=ctx.hermes_run_id,
            idempotency_key=idempotency_key,
            **key_payload,
        ),
        error_prefix=error_prefix,
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    action_ref = str(payload.get("order_ref") or payload.get("checkout_ref") or "").strip()
    if action_ref:
        ctx.business_action_refs.append(action_ref)
    return json.dumps({"status": "ok", result_key: payload}, ensure_ascii=False)


def _knowledge_candidate_tool(
    args: dict,
    *,
    proposed_kind: str | None = None,
) -> dict[str, Any]:
    ctx = current_tool_context.get()
    if ctx is None:
        return {"status": "error", "error": "no_active_context"}
    source_id = (args.get("source_id") or "").strip()
    proposed_kind = (proposed_kind or args.get("proposed_kind") or "").strip()
    proposed_payload = args.get("proposed_payload")
    if not source_id or not proposed_kind or not isinstance(proposed_payload, dict):
        return {"status": "empty", "note": "source_id, proposed_kind and proposed_payload are required."}
    fingerprint = _payload_fingerprint(proposed_payload)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_candidate_async(
            workspace_id=ctx.workspace_id,
            source_id=source_id,
            proposed_kind=proposed_kind,
            proposed_payload=proposed_payload,
            evidence_refs=_string_list(args.get("evidence_refs")) or [source_id],
            confidence=float(args.get("confidence") or 0.75),
            created_by_ref=f"agent:{ctx.agent_id}",
            hermes_run_id=ctx.hermes_run_id,
            correlation_id=f"knowledge-candidate:{ctx.workspace_id}:{ctx.agent_id}",
            idempotency_key=(
                "knowledge-candidate:"
                f"{ctx.workspace_id}:{ctx.agent_id}:{source_id}:{proposed_kind}:{fingerprint}"
            ),
        ),
        error_prefix="knowledge_candidate",
    )
    return {"status": "ok", **payload}


def _knowledge_save_tool(
    args: dict,
    *,
    kind: str,
    default_scope: str = "personal",
    source_kind: str = "agent_note",
) -> str:
    ctx = current_tool_context.get()
    if ctx is None:
        return json.dumps({"status": "error", "error": "no_active_context"})
    title = (args.get("title") or "").strip()
    body_text = (args.get("body_text") or args.get("text") or "").strip()
    if not title or not body_text:
        return json.dumps({"status": "empty", "note": "title and body_text are required."}, ensure_ascii=False)
    payload = _run_knowledge_coro(
        ctx,
        _knowledge_save_async(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            hermes_run_id=ctx.hermes_run_id,
            raw_scope=args.get("scope") or default_scope,
            kind=kind,
            title=title,
            body_text=body_text,
            collection_ids=_string_list(args.get("collection_ids")),
            tags=_string_list(args.get("tags")),
            created_by_ref=f"agent:{ctx.agent_id}",
            source_kind=source_kind,
            correlation_id=f"knowledge-save:{ctx.workspace_id}:{ctx.agent_id}",
            idempotency_key=f"knowledge-save:{ctx.workspace_id}:{ctx.agent_id}:{kind}:{_norm(title)}",
        ),
        error_prefix=f"knowledge_save_{kind}",
    )
    if payload.get("status") == "degraded":
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"status": "ok", "item": payload}, ensure_ascii=False)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _object_arg(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _allowed_sender_types(value: Any) -> list[str]:
    allowed = {"seller", "customer", "ai"}
    return [sender for sender in _string_list(value) if sender in allowed]


def _allowed_modalities(value: Any) -> list[str]:
    allowed = {"text", "image", "audio", "video", "pdf", "file"}
    return [modality for modality in _string_list(value) if modality in allowed]


def _bool_arg(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _payload_fingerprint(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_KNOWLEDGE_SEARCH_SCHEMA = {
    "name": "knowledge_search",
    "description": (
        "Search OQIM Knowledge MCP storage in personal or business scope by "
        "collection and tags. Use price or policy facts as truth only when they "
        "come from approved authority; source results are evidence or drafts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
            "collection_ids": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "enable_semantic": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
}

_KNOWLEDGE_CHAT_MEMORY_SEARCH_SCHEMA = {
    "name": "knowledge_search_chat_memory",
    "description": (
        "Search workspace chat memory for historical or list-style questions. "
        "Results include message and conversation citations and are low-authority "
        "evidence. Do not use them as catalog, price, stock, or policy truth."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "conversation_id": {"type": "integer"},
            "sender_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["seller", "customer", "ai"]},
            },
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
}

_KNOWLEDGE_CATALOG_SEARCH_SCHEMA = {
    "name": "knowledge_search_catalog",
    "description": (
        "Search approved catalog, product, variant, offer, and media authority "
        "through Knowledge MCP. Chat examples are excluded; results include fact "
        "and source citations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "query_modalities": {
                "type": "array",
                "items": {"type": "string", "enum": ["text", "image", "audio", "video", "pdf", "file"]},
            },
            "include_media": {"type": "boolean"},
            "enable_semantic": {"type": "boolean"},
            "enable_rerank": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
}

_KNOWLEDGE_MEDIA_SEARCH_SCHEMA = {
    "name": "knowledge_search_media",
    "description": (
        "Search catalog media and source media candidates using the buyer's media "
        "description, OCR text, visual summary, or transcript. Default modality "
        "is image; results include multimodal retrieval traces and citations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "query_modalities": {
                "type": "array",
                "items": {"type": "string", "enum": ["text", "image", "audio", "video", "pdf", "file"]},
            },
            "enable_semantic": {"type": "boolean"},
            "enable_rerank": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
}

_KNOWLEDGE_GET_ITEM_SCHEMA = {
    "name": "knowledge_get_item",
    "description": (
        "Fetch a Knowledge MCP item with source and chunk evidence. The item may "
        "belong to personal or business scope."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
        },
        "required": ["item_id"],
    },
}

_KNOWLEDGE_EXPLAIN_SOURCES_SCHEMA = {
    "name": "knowledge_explain_sources",
    "description": (
        "Explain which source and chunk evidence produced a Knowledge MCP item, "
        "including citations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
        },
        "required": ["item_id"],
    },
}

_KNOWLEDGE_SAVE_SCRIPT_SCHEMA = {
    "name": "knowledge_save_script",
    "description": "Save a script to Knowledge storage only when the user clearly asks for it.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body_text": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
            "collection_ids": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "body_text"],
    },
}

_KNOWLEDGE_SAVE_NOTE_SCHEMA = {
    "name": "knowledge_save_note",
    "description": "Save a note to Knowledge storage only when the user clearly asks for it.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body_text": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
            "collection_ids": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "body_text"],
    },
}

_KNOWLEDGE_SOURCE_DOC_SCHEMA = {
    "name": "knowledge_create_source_doc",
    "description": (
        "Save business source evidence. This is not approved catalog or policy "
        "authority; authority requires a proposal and approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body_text": {"type": "string"},
            "collection_ids": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "body_text"],
    },
}

_KNOWLEDGE_ATTACH_TO_COLLECTION_SCHEMA = {
    "name": "knowledge_attach_to_collection",
    "description": (
        "Attach an existing Knowledge MCP item to collections. This updates "
        "retrieval metadata and does not change authority state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
            "collection_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["item_id", "collection_ids"],
    },
}

_KNOWLEDGE_TAG_ITEM_SCHEMA = {
    "name": "knowledge_tag_item",
    "description": (
        "Add retrieval tags to an existing Knowledge MCP item. This does not "
        "change authority state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "business"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["item_id", "tags"],
    },
}

_KNOWLEDGE_CANDIDATE_SCHEMA = {
    "name": "knowledge_propose_candidate",
    "description": (
        "Propose a catalog, policy, FAQ, or other typed candidate from business "
        "source evidence. This always creates an Agent Control approval proposal; "
        "there is no silent promotion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "proposed_kind": {"type": "string"},
            "proposed_payload": {"type": "object"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": ["source_id", "proposed_kind", "proposed_payload"],
    },
}


_KNOWLEDGE_EXTRACT_CANDIDATES_SCHEMA = {
    "name": "knowledge_extract_candidates",
    "description": (
        "Save structured candidates extracted by the agent from source text as "
        "Agent Control approval proposals. These are not authority."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposed_kind": {"type": "string"},
                        "proposed_payload": {"type": "object"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                    },
                    "required": ["proposed_kind", "proposed_payload"],
                },
            },
        },
        "required": ["source_id", "candidates"],
    },
}

_KNOWLEDGE_TYPED_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "proposed_payload": {"type": "object"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["source_id", "proposed_payload"],
}

_KNOWLEDGE_PROPOSE_CATALOG_UPDATE_SCHEMA = {
    "name": "knowledge_propose_catalog_update",
    "description": (
        "Create a catalog, product, or offer update candidate. It is not grounding "
        "authority until Agent Control approval is completed."
    ),
    "parameters": _KNOWLEDGE_TYPED_PROPOSAL_SCHEMA,
}

_KNOWLEDGE_PROPOSE_POLICY_UPDATE_SCHEMA = {
    "name": "knowledge_propose_policy_update",
    "description": (
        "Create a policy candidate. It is not reply grounding authority until "
        "Agent Control approval is completed."
    ),
    "parameters": _KNOWLEDGE_TYPED_PROPOSAL_SCHEMA,
}

_KNOWLEDGE_PROPOSE_FAQ_UPDATE_SCHEMA = {
    "name": "knowledge_propose_faq_update",
    "description": (
        "Create an FAQ candidate. It is not authority until Agent Control approval "
        "is completed."
    ),
    "parameters": _KNOWLEDGE_TYPED_PROPOSAL_SCHEMA,
}

_KNOWLEDGE_PROPOSE_RULE_SCHEMA = {
    "name": "knowledge_propose_rule",
    "description": (
        "Create a business or routing rule candidate. It must not be used as a "
        "rule until Agent Control approval is completed."
    ),
    "parameters": _KNOWLEDGE_TYPED_PROPOSAL_SCHEMA,
}

_CONVERSATION_SET_STATE_SCHEMA = {
    "name": "conversation.set_state",
    "description": (
        "Write compact state for the current Agent Session: stage, intent, "
        "selected items, payment and fulfillment progress, missing authority, "
        "next action, and risk flags. This is not catalog authority; it is session "
        "memory and owner analytics state for later turns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "stage": {"type": "string"},
            "active_intent": {"type": "string"},
            "selected_items": {"type": "array", "items": {"type": "object"}},
            "shown_prices": {"type": "array", "items": {"type": "object"}},
            "customer_details": {"type": "object"},
            "payment": {"type": "object"},
            "fulfillment": {"type": "object"},
            "missing_authority": {"type": "array", "items": {"type": "string"}},
            "next_best_action": {"type": "string"},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
    },
}

_CONVERSATION_RECORD_SCHEMA = {
    "name": "conversation.record",
    "description": (
        "Record this conversation's commercial state as ONE structured snapshot. "
        "deal_value is the price you QUOTED this customer (so'm) — what you told "
        "them, session memory, NOT catalog authority; record it even if it is not "
        "an approved catalog price. Set handoff.needed=true when a human must step "
        "in. This is not catalog or policy authority. pipeline_key is the LOGICAL "
        "pipeline key the owner configured (e.g. 'consulting'); omit it if unsure. "
        "custom_fields records owner-configured CRM fields as [{key,value}] — fill "
        "a field ONLY when the conversation states its value, never guess."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stage": {
                "type": "string",
                "enum": [
                    "new", "interested", "qualified", "quoted",
                    "negotiating", "won", "lost", "blocked", "follow_up",
                ],
            },
            "deal_value": {"type": "number"},
            "currency": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object"}},
            "customer": {"type": "object"},
            "summary": {"type": "string"},
            "buying_signals": {"type": "array", "items": {"type": "string"}},
            "objections": {"type": "array", "items": {"type": "string"}},
            "next_best_action": {"type": "string"},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "opted_out": {"type": "boolean"},
            "payment_method": {"type": "string"},
            "fulfillment": {"type": "string"},
            "pipeline_key": {"type": "string"},
            "custom_fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "handoff": {
                "type": "object",
                "properties": {
                    "needed": {"type": "boolean"},
                    "kind": {
                        "type": "string",
                        "enum": ["lead", "support", "complaint", "refund", "human_requested"],
                    },
                    "reason": {"type": "string"},
                },
            },
            "idempotency_key": {"type": "string"},
        },
        "required": ["stage"],
    },
}

_CONVERSATION_RECORD_INTELLIGENCE_SCHEMA = {
    "name": "conversation.record_intelligence",
    "description": (
        "Record customer intelligence for analytics and follow-up: lead stage, "
        "buying signals, objections, preferences, owner notes, next action, and "
        "risk flags. This is not catalog, price, stock, payment, or policy authority."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lead_stage": {
                "type": "string",
                "enum": [
                    "unknown",
                    "new",
                    "interested",
                    "qualified",
                    "checkout",
                    "blocked",
                    "won",
                    "lost",
                    "follow_up",
                ],
            },
            "buying_signals": {"type": "array", "items": {"type": "string"}},
            "opted_out": {
                "type": "boolean",
                "description": (
                    "true ONLY when the customer explicitly asks to stop receiving "
                    "messages, unsubscribe, or be left alone. Permanent: the business "
                    "will never message them proactively again."
                ),
            },
            "objections": {"type": "array", "items": {"type": "string"}},
            "preferences": {"type": "object"},
            "next_best_action": {"type": "string"},
            "owner_notes": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
    },
}

_WORK_CREATE_TASK_SCHEMA = {
    "name": "work.create_task",
    "description": (
        "Create a generic business task for an owner or admin. Use it for payment "
        "details, stock checks, delivery preparation, calls, follow-ups, approvals, "
        "setup work, or any human business action that must not be lost."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_kind": {
                "type": "string",
                "enum": ["business", "meeting", "delivery", "stock", "call", "payment", "follow_up"],
            },
            "title": {"type": "string"},
            "reason": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            "conversation_state_snapshot_id": {"type": "integer"},
            "selected_item_refs": {"type": "array", "items": {"type": "string"}},
            "missing_authority": {"type": "array", "items": {"type": "string"}},
            "due_at": {"type": "string"},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
        "required": ["title", "reason"],
    },
}

_OWNER_EDIT_DOC_SCHEMA = {
    "name": "owner.edit_doc",
    "description": (
        "Propose an edit to one section of this agent's AGENT.md (role, behavior, "
        "examples, approval rules, etc.). The edit is NOT applied immediately — it "
        "becomes an approval card the owner confirms. Use when the owner asks to "
        "change how the agent behaves, what it says, or its rules."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section_key": {
                "type": "string",
                "enum": list(_OWNER_DOC_SECTION_KEYS),
            },
            "body": {"type": "string"},
        },
        "required": ["section_key", "body"],
    },
}

_MEDIA_STORE_SCHEMA = {
    "name": "media.store",
    "description": (
        "Save a reusable media asset (intro video, photo, document) to this "
        "workspace's media vault under a short handle, so the seller can send it "
        "later by handle. Provide a direct media URL. NOTE: in production this is "
        "approval-gated (owner confirms) and uploads once to Telegram cloud; the "
        "spike persists the URL directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handle": {"type": "string"},
            "cdn_url": {"type": "string"},
            "media_type": {"type": "string", "enum": list(_MEDIA_TYPES)},
            "mime_type": {"type": "string"},
            "file_name": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": ["handle", "cdn_url", "media_type"],
    },
}

_MEDIA_LIST_SCHEMA = {
    "name": "media.list",
    "description": (
        "List the reusable media assets in this workspace's media vault "
        "(handle, type, caption). Read-only."
    ),
    "parameters": {"type": "object", "properties": {}},
}

_WORK_HANDOFF_SCHEMA = {
    "name": "work.handoff",
    "description": (
        "Hand this conversation to a human — ONE atomic call that records the "
        "task AND notifies the owner together. Triggers: the customer shares a "
        "phone/contact for follow-up -> kind=lead; asks for a human/operator -> "
        "kind=human_requested; complaint, refund, or payment dispute -> "
        "kind=complaint; a post-sale request you cannot resolve -> kind=support. "
        "If an already handed-off customer comes back still waiting or with "
        "fresh buying intent, call this AGAIN (kind=lead, title saying they "
        "returned) — being back means the owner has not reached them yet. "
        "Call at most ONCE per customer turn: when several kinds fit the same "
        "message, pick the single most urgent (complaint > human_requested > "
        "lead > support) — never stack two handoffs. Never call it for a "
        "greeting or social filler alone — wait for the customer's substantive "
        "message; and never repeat a handoff for the same need you already "
        "recorded minutes ago in this conversation. "
        "Call it BEFORE telling the customer a person will follow up — never "
        "promise a follow-up without this call. Always pass customer_name and "
        "customer_phone exactly as the customer shared them in the chat "
        "(empty string only if truly unknown) — the owner card shows them. "
        "Do not use work.create_task or owner.notify separately for "
        "conversation handoffs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["lead", "support", "complaint", "human_requested"]},
            "title": {"type": "string"},
            "detail": {
                "type": "string",
                "description": (
                    "2-4 short sentences: who the customer is, what they want, "
                    "what was already promised — everything the owner needs to "
                    "act without reading the chat."
                ),
            },
            "customer_name": {
                "type": "string",
                "description": "The customer's name as shared in the chat; empty string if unknown.",
            },
            "customer_phone": {
                "type": "string",
                "description": "The customer's phone as shared in the chat; empty string if unknown.",
            },
            "idempotency_key": {"type": "string"},
        },
        "required": ["kind", "title", "detail", "customer_name", "customer_phone"],
    },
}

_OWNER_NOTIFY_SCHEMA = {
    "name": "owner.notify",
    "description": (
        "Create a bot-ready notification payload for the business owner or future "
        "UI. Use this before telling the customer about a handoff, so the required "
        "owner work is actually recorded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_ref": {"type": "string"},
            "order_ref": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "recommended_action": {"type": "string"},
            "selected_item_refs": {"type": "array", "items": {"type": "string"}},
            "shown_price_refs": {"type": "array", "items": {"type": "string"}},
            "missing_authority": {"type": "array", "items": {"type": "string"}},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "idempotency_key": {"type": "string"},
        },
        "required": ["title", "summary", "recommended_action"],
    },
}

_COMMERCE_INTENT_PARAMETERS = {
    "type": "object",
    "properties": {
        "selected_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_ref": {"type": "string"},
                    "variant_ref": {"type": "string"},
                    "offer_ref": {"type": "string"},
                    "quantity": {"type": "number"},
                },
            },
        },
        "shown_prices": {"type": "array", "items": {"type": "object"}},
        "payment_method": {"type": "string"},
        "fulfillment_method": {"type": "string"},
        "status": {"type": "string", "enum": ["pending", "blocked", "ready"]},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "linked_task_refs": {"type": "array", "items": {"type": "string"}},
        "source_refs": {"type": "array", "items": {"type": "string"}},
        "idempotency_key": {"type": "string"},
    },
    "required": ["selected_items"],
}

_COMMERCE_CREATE_ORDER_SCHEMA = {
    "name": "commerce.create_order",
    "description": (
        "Create a pending or blocked order intent when the customer is trying to "
        "buy, register, reserve, or proceed. OQIM validates catalog product, offer, "
        "and price authority. Missing payment, stock, delivery, or owner approval "
        "keeps the order intent pending or blocked rather than falsely finalized."
    ),
    "parameters": _COMMERCE_INTENT_PARAMETERS,
}

_COMMERCE_CREATE_CHECKOUT_INTENT_SCHEMA = {
    "name": "commerce.create_checkout_intent",
    "description": (
        "Compatibility alias for commerce.create_order. Prefer "
        "commerce.create_order for new behavior."
    ),
    "parameters": _COMMERCE_INTENT_PARAMETERS,
}


def register_oqim_tools() -> None:
    from tools.registry import registry

    from app.modules.agent_runtime_v2.hermes.talk_tools import register_talk_tools

    register_talk_tools()
    for name in _LEGACY_RETRIEVAL_TOOL_NAMES:
        registry.deregister(name)

    registry.register(
        name="knowledge_search", toolset="oqim", schema=_KNOWLEDGE_SEARCH_SCHEMA,
        handler=lambda args, **kw: knowledge_search(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="ask", toolset="oqim", schema=_ASK_SCHEMA,
        handler=lambda args, **kw: ask(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_search_chat_memory", toolset="oqim", schema=_KNOWLEDGE_CHAT_MEMORY_SEARCH_SCHEMA,
        handler=lambda args, **kw: knowledge_search_chat_memory(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_search_catalog", toolset="oqim", schema=_KNOWLEDGE_CATALOG_SEARCH_SCHEMA,
        handler=lambda args, **kw: knowledge_search_catalog(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_search_media", toolset="oqim", schema=_KNOWLEDGE_MEDIA_SEARCH_SCHEMA,
        handler=lambda args, **kw: knowledge_search_media(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_get_item", toolset="oqim", schema=_KNOWLEDGE_GET_ITEM_SCHEMA,
        handler=lambda args, **kw: knowledge_get_item(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_explain_sources", toolset="oqim", schema=_KNOWLEDGE_EXPLAIN_SOURCES_SCHEMA,
        handler=lambda args, **kw: knowledge_explain_sources(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_save_script", toolset="oqim", schema=_KNOWLEDGE_SAVE_SCRIPT_SCHEMA,
        handler=lambda args, **kw: knowledge_save_script(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_save_note", toolset="oqim", schema=_KNOWLEDGE_SAVE_NOTE_SCHEMA,
        handler=lambda args, **kw: knowledge_save_note(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_create_source_doc", toolset="oqim", schema=_KNOWLEDGE_SOURCE_DOC_SCHEMA,
        handler=lambda args, **kw: knowledge_create_source_doc(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_attach_to_collection", toolset="oqim", schema=_KNOWLEDGE_ATTACH_TO_COLLECTION_SCHEMA,
        handler=lambda args, **kw: knowledge_attach_to_collection(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_tag_item", toolset="oqim", schema=_KNOWLEDGE_TAG_ITEM_SCHEMA,
        handler=lambda args, **kw: knowledge_tag_item(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_propose_candidate", toolset="oqim", schema=_KNOWLEDGE_CANDIDATE_SCHEMA,
        handler=lambda args, **kw: knowledge_propose_candidate(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_extract_candidates", toolset="oqim", schema=_KNOWLEDGE_EXTRACT_CANDIDATES_SCHEMA,
        handler=lambda args, **kw: knowledge_extract_candidates(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_propose_catalog_update", toolset="oqim", schema=_KNOWLEDGE_PROPOSE_CATALOG_UPDATE_SCHEMA,
        handler=lambda args, **kw: knowledge_propose_catalog_update(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_propose_policy_update", toolset="oqim", schema=_KNOWLEDGE_PROPOSE_POLICY_UPDATE_SCHEMA,
        handler=lambda args, **kw: knowledge_propose_policy_update(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_propose_faq_update", toolset="oqim", schema=_KNOWLEDGE_PROPOSE_FAQ_UPDATE_SCHEMA,
        handler=lambda args, **kw: knowledge_propose_faq_update(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="knowledge_propose_rule", toolset="oqim", schema=_KNOWLEDGE_PROPOSE_RULE_SCHEMA,
        handler=lambda args, **kw: knowledge_propose_rule(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="conversation.set_state", toolset="oqim", schema=_CONVERSATION_SET_STATE_SCHEMA,
        handler=lambda args, **kw: conversation_set_state(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="conversation.record_intelligence", toolset="oqim", schema=_CONVERSATION_RECORD_INTELLIGENCE_SCHEMA,
        handler=lambda args, **kw: conversation_record_intelligence(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="conversation.record", toolset="oqim", schema=_CONVERSATION_RECORD_SCHEMA,
        handler=lambda args, **kw: conversation_record(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="work.create_task", toolset="oqim", schema=_WORK_CREATE_TASK_SCHEMA,
        handler=lambda args, **kw: work_create_task(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="owner.edit_doc", toolset="oqim", schema=_OWNER_EDIT_DOC_SCHEMA,
        handler=lambda args, **kw: owner_edit_doc(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="media.store", toolset="oqim", schema=_MEDIA_STORE_SCHEMA,
        handler=lambda args, **kw: media_store(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="media.list", toolset="oqim", schema=_MEDIA_LIST_SCHEMA,
        handler=lambda args, **kw: media_list(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="work.handoff", toolset="oqim", schema=_WORK_HANDOFF_SCHEMA,
        handler=lambda args, **kw: work_handoff(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="owner.notify", toolset="oqim", schema=_OWNER_NOTIFY_SCHEMA,
        handler=lambda args, **kw: owner_notify(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="commerce.create_order", toolset="oqim", schema=_COMMERCE_CREATE_ORDER_SCHEMA,
        handler=lambda args, **kw: commerce_create_order(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
    registry.register(
        name="commerce.create_checkout_intent", toolset="oqim", schema=_COMMERCE_CREATE_CHECKOUT_INTENT_SCHEMA,
        handler=lambda args, **kw: commerce_create_checkout_intent(args, **kw),
        check_fn=lambda: True, requires_env=[], override=True,
    )
