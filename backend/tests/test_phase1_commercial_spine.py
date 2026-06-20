from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_action import CommercialActionProposalRecord
from app.models.commercial_spine import (
    BusinessBrainFactRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
    CommercialEventRecord,
    LLMGatewayTraceRecord,
)
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.commercial_spine.contracts import (
    BusinessBrainFact,
    BusinessBrainUpdate,
    CommercialActionProposal,
    CommercialDecisionTrace,
    CommercialEvent,
    LLMGatewayRequest,
    LLMGatewayTrace,
)
from app.modules.commercial_spine.llm_gateway import (
    LLMGateway,
    LLMProviderResponse,
    _gateway_contents,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository


class GatewayFixtureOutput(BaseModel):
    answer: str
    action: str | None = None


def _event(
    *,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    event_id: str = "event-phase1-1",
    idempotency_key: str = "event:phase1:1",
    correlation_id: str = "corr-phase1",
) -> CommercialEvent:
    return CommercialEvent(
        event_id=event_id,
        workspace_id=workspace.id,
        source_type="message",
        source_ref=f"message:{conversation.id}:1",
        actor_type="customer",
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload={
            "conversation_id": conversation.id,
            "customer_id": customer.id,
            "text": "Narxi qancha?",
        },
    )


def _fact(
    *,
    workspace: Workspace,
    fact_id: str = "fact-phase1-1",
    idempotency_key: str = "fact:phase1:1",
) -> BusinessBrainFact:
    return BusinessBrainFact(
        fact_id=fact_id,
        workspace_id=workspace.id,
        fact_type="knowledge_fact",
        entity_ref="business:delivery",
        value={"delivery_text": "Yetkazib berish 1-2 kun."},
        confidence=0.94,
        status="confirmed",
        risk_tier="low",
        source_refs=["message:owner:1"],
        idempotency_key=idempotency_key,
    )


async def test_phase1_spine_persists_event_fact_update_projection_idempotently(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    repository = CommercialSpineRepository(db_session)
    event = _event(workspace=workspace, conversation=conversation, customer=customer)
    fact = _fact(workspace=workspace)
    update = BusinessBrainUpdate(
        update_id="update-phase1-1",
        workspace_id=workspace.id,
        target_ref=f"fact:{fact.fact_id}",
        proposed_value=fact.value,
        source="manual",
        approval_state="confirmed",
        risk_tier="low",
        evidence_refs=fact.source_refs,
        idempotency_key="update:phase1:1",
    )

    assert await repository.append_event(event) is True
    assert await repository.append_event(event) is False
    assert await repository.persist_fact(fact) is True
    assert await repository.persist_fact(fact) is False
    assert await repository.persist_update(update) is True
    assert await repository.persist_update(update) is False

    rebuilt = await repository.rebuild_projection_from_facts(
        workspace_id=workspace.id,
        projection_ref="brain:knowledge:delivery",
        projection_type="business_brain",
        entity_ref="business:delivery",
    )

    assert rebuilt.state == {"knowledge_fact": fact.value}
    assert rebuilt.source_refs == [f"fact:{fact.fact_id}", "message:owner:1"]
    assert await _count(db_session, CommercialEventRecord) == 1
    assert await _count(db_session, BusinessBrainFactRecord) == 1
    assert await _count(db_session, BusinessBrainUpdateRecord) == 1
    assert await _count(db_session, BusinessBrainProjectionRecord) == 1


async def test_phase1_spine_dedupes_fact_id_even_when_idempotency_key_changes(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    first = _fact(
        workspace=workspace,
        fact_id="fact-memory-duplicate",
        idempotency_key="fact:duplicate:first",
    )
    replayed_by_another_chunk = _fact(
        workspace=workspace,
        fact_id="fact-memory-duplicate",
        idempotency_key="fact:duplicate:second",
    )

    assert await repository.persist_fact(first) is True
    assert await repository.persist_fact(replayed_by_another_chunk) is False

    facts = await db_session.scalars(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "fact-memory-duplicate",
        )
    )
    assert len(list(facts)) == 1


async def test_phase1_spine_dedupes_update_id_even_when_idempotency_key_changes(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    """Regression: concurrent full-conversation replays regenerate the same
    deterministic update_id (sometimes with a different idempotency_key, always
    racing across sessions). persist_update must dedupe on update_id without
    raising IntegrityError on uq_business_brain_updates_workspace_update."""
    repository = CommercialSpineRepository(db_session)
    base = dict(
        update_id="update-memory-duplicate",
        workspace_id=workspace.id,
        target_ref="fact:update-memory-duplicate",
        proposed_value={"conversation_id": 1},
        source="replay",
        approval_state="confirmed",
        risk_tier="low",
        evidence_refs=["message:1"],
    )
    first = BusinessBrainUpdate(**base, idempotency_key="update:duplicate:first")
    replayed_by_another_chunk = BusinessBrainUpdate(
        **base, idempotency_key="update:duplicate:second"
    )

    assert await repository.persist_update(first) is True
    assert await repository.persist_update(replayed_by_another_chunk) is False

    rows = await db_session.scalars(
        select(BusinessBrainUpdateRecord).where(
            BusinessBrainUpdateRecord.workspace_id == workspace.id,
            BusinessBrainUpdateRecord.update_id == "update-memory-duplicate",
        )
    )
    assert len(list(rows)) == 1


async def test_phase1_proposals_are_idempotent_and_workspace_scoped(
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    repository = CommercialSpineRepository(db_session)
    proposal = CommercialActionProposal(
        proposal_id="proposal-phase1-1",
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        action_type="send_reply",
        lifecycle_state="waiting_approval",
        execution_mode="draft_for_review",
        risk_level="low",
        requires_approval=True,
        priority="medium",
        confidence=0.88,
        reason_code="seller_agent_draft",
        source_refs=["message:1", "trace:phase1"],
        payload={"draft": "Ha, bor."},
        idempotency_key="proposal:phase1:1",
    )

    assert await repository.persist_action_proposal(proposal) is True
    assert await repository.persist_action_proposal(proposal) is False

    same_workspace = await repository.list_action_proposals(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
    )
    other_workspace = await repository.list_action_proposals(
        workspace_id=workspace_b.id,
        conversation_id=conversation.id,
    )

    assert same_workspace == (proposal,)
    assert other_workspace == ()
    assert await _count(db_session, CommercialActionProposalRecord) == 1


async def test_phase1_llm_gateway_traces_success_and_degraded_results(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    ok_provider = AsyncMock(
        return_value=LLMProviderResponse(
            text='{"answer":"ok","action":"continue"}',
            model_used="test-model",
            token_usage={"input_tokens": 5, "output_tokens": 4},
        )
    )
    gateway = LLMGateway(
        repository=CommercialSpineRepository(db_session),
        provider=ok_provider,
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="commercial_spine_debug",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={"customer_text": "Salom"},
        output_schema_name="GatewayFixtureOutput",
        workspace_id=workspace.id,
        correlation_id="corr-gateway-ok",
        source_refs=["message:1"],
        timeout_ms=200,
    )

    ok = await gateway.generate(request, output_model=GatewayFixtureOutput)

    assert ok.status == "ok"
    assert ok.parsed_output == {"answer": "ok", "action": "continue"}
    assert ok.model_used == "test-model"
    assert ok.trace_id

    schema_provider = AsyncMock(return_value=LLMProviderResponse(text='{"action":"missing"}'))
    schema_gateway = LLMGateway(
        repository=CommercialSpineRepository(db_session),
        provider=schema_provider,
    )
    schema_error = await schema_gateway.generate(
        request.model_copy(update={"correlation_id": "corr-gateway-schema"}),
        output_model=GatewayFixtureOutput,
    )

    timeout_provider = AsyncMock(side_effect=TimeoutError())
    timeout_gateway = LLMGateway(
        repository=CommercialSpineRepository(db_session),
        provider=timeout_provider,
    )
    timeout = await timeout_gateway.generate(
        request.model_copy(update={"correlation_id": "corr-gateway-timeout"}),
        output_model=GatewayFixtureOutput,
    )

    assert schema_error.status == "schema_error"
    assert schema_error.validation_errors
    assert timeout.status == "timeout"
    assert timeout.validation_errors == ["provider_timeout"]
    assert await _count(db_session, LLMGatewayTraceRecord) == 3


async def test_phase1_llm_gateway_detached_generation_defers_trace_persistence(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    provider = AsyncMock(
        return_value=LLMProviderResponse(
            text='{"answer":"ok","action":"continue"}',
            model_used="test-model",
        )
    )
    gateway = LLMGateway(repository=repository, provider=provider)
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="gateway_detached_fixture",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={"question": "test"},
        output_schema_name="GatewayFixtureOutput",
        workspace_id=workspace.id,
        correlation_id="corr-gateway-detached",
        source_refs=["message:detached"],
    )

    result, trace = await gateway.generate_detached(
        request,
        output_model=GatewayFixtureOutput,
    )

    assert result.status == "ok"
    assert trace.trace_id == result.trace_id
    assert await _count(db_session, LLMGatewayTraceRecord) == 0

    await repository.persist_llm_trace(trace)

    assert await _count(db_session, LLMGatewayTraceRecord) == 1


async def test_phase1_llm_gateway_builds_multimodal_contents_without_trace_bytes(
    workspace: Workspace,
) -> None:
    request = LLMGatewayRequest(
        route_key="media_rich",
        workflow_name="media_fixture",
        prompt_id="business_brain.media_semantic_learning",
        prompt_version="1.0.0",
        input_payload={"media_ref": "source_media:1"},
        content_parts=[
            {"kind": "text", "text": "Extract facts from this media."},
            {
                "kind": "inline_data",
                "mime_type": "image/jpeg",
                "data_base64": "ZmFrZS1qcGVn",
            },
        ],
        output_schema_name="GatewayFixtureOutput",
        workspace_id=workspace.id,
        correlation_id="corr-gateway-media",
        source_refs=["source_media:1"],
    )

    contents = await _gateway_contents(request)
    raw_request = request.model_dump(mode="json")

    assert raw_request["input_payload"] == {"media_ref": "source_media:1"}
    assert "content_parts" not in raw_request
    assert isinstance(contents, list)
    assert len(contents[0].parts) == 3
    prompt_payload = json.loads(contents[0].parts[0].text)
    assert prompt_payload["media_ref"] == "source_media:1"


async def test_phase1_debug_contract_is_workspace_scoped(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_headers_b: dict[str, str],
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    repository = CommercialSpineRepository(db_session)
    event = _event(
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        correlation_id="corr-debug-phase1",
    )
    await repository.append_event(event)
    await repository.persist_fact(_fact(workspace=workspace))
    await repository.persist_decision_trace(
        CommercialDecisionTrace(
            trace_id="decision-trace-phase1",
            workspace_id=workspace.id,
            correlation_id="corr-debug-phase1",
            changed_fact_refs=["fact:fact-phase1-1"],
            emitted_proposal_refs=[],
            llm_trace_ids=[],
            degraded_reasons=[],
        )
    )
    await repository.persist_llm_trace(
        LLMGatewayTrace(
            trace_id="llm-trace-debug-phase1",
            workspace_id=workspace.id,
            correlation_id="corr-debug-phase1",
            route_key="structured_fast",
            workflow_name="commercial_spine_debug",
            prompt_id="business_brain.source_learning",
            prompt_version="1.0.0",
            source_refs=["message:1"],
            status="ok",
            model_used="test-model",
            token_usage={"input_tokens": 5, "output_tokens": 4},
            latency_ms=42,
            raw_request={"text": "Salom"},
            raw_response={"answer": "ok"},
        )
    )
    await db_session.flush()

    own = await client.get(
        "/api/commercial-spine/debug/corr-debug-phase1",
        headers=auth_headers,
    )
    other = await client.get(
        "/api/commercial-spine/debug/corr-debug-phase1",
        headers=auth_headers_b,
    )

    assert own.status_code == 200
    payload = own.json()
    assert payload["workspace_id"] == workspace.id
    assert payload["correlation_id"] == "corr-debug-phase1"
    assert payload["events"][0]["event_id"] == event.event_id
    assert payload["decision_traces"][0]["trace_id"] == "decision-trace-phase1"
    assert payload["llm_gateway_traces"][0]["trace_id"] == "llm-trace-debug-phase1"
    assert payload["llm_gateway_traces"][0]["raw_response"] == {"answer": "ok"}
    assert other.status_code == 200
    assert other.json()["events"] == []
    assert other.json()["decision_traces"] == []
    assert other.json()["llm_gateway_traces"] == []

def test_phase1_commercial_spine_has_no_direct_provider_or_semantic_regex() -> None:
    root = Path(__file__).resolve().parents[1] / "app/modules/commercial_spine"
    offenders: list[str] = []
    banned_tokens = (
        "genai.Client(",
        ".models.generate_content(",
        "client.aio.models.generate_content(",
        "re.compile(",
        "re.search(",
        "keyword",
        "heuristic",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in banned_tokens):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


async def _count(db_session: AsyncSession, model: type[Any]) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(model)) or 0)
