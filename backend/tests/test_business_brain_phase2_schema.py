from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainUpdateRecord
from app.models.workspace import Workspace
from app.modules.business_brain.contracts import BusinessBrainFactUpdateInput
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.contracts import BusinessBrainFact
from app.modules.commercial_spine.repository import CommercialSpineRepository


def _input(
    *,
    workspace: Workspace,
    update_id: str,
    fact_id: str,
    source: str,
    approval_state: str,
    status: str,
    fact_type: str = "delivery_policy",
    entity_ref: str = "business:delivery",
    value: dict[str, Any] | None = None,
    source_refs: list[str] | None = None,
    idempotency_key: str | None = None,
) -> BusinessBrainFactUpdateInput:
    return BusinessBrainFactUpdateInput(
        update_id=update_id,
        fact_id=fact_id,
        workspace_id=workspace.id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value or {"text": "Yetkazib berish 1-2 kun."},
        confidence=0.91,
        status=status,
        risk_tier="low",
        source=source,
        approval_state=approval_state,
        source_refs=source_refs if source_refs is not None else ["owner_message:1"],
        idempotency_key=idempotency_key or f"{update_id}:idem",
    )


def test_business_brain_fact_status_contract_accepts_phase2_memory_states(
    workspace: Workspace,
) -> None:
    active = BusinessBrainFact(
        fact_id="fact-active-phase2",
        workspace_id=workspace.id,
        fact_type="business_rule",
        entity_ref="business:delivery",
        value={"text": "Yetkazishdan oldin telefon qiling."},
        confidence=0.95,
        status="active",
        risk_tier="low",
        source_refs=["owner_message:1"],
        idempotency_key="fact-active-phase2",
    )
    conflicted = active.model_copy(
        update={
            "fact_id": "fact-conflicted-phase2",
            "status": "conflicted",
            "source_refs": ["owner_message:1", "owner_message:2"],
            "idempotency_key": "fact-conflicted-phase2",
        }
    )

    assert active.status == "active"
    assert conflicted.status == "conflicted"


def test_business_brain_update_contract_rejects_sourceless_truth(
    workspace: Workspace,
) -> None:
    with pytest.raises(ValidationError):
        _input(
            workspace=workspace,
            update_id="update-sourceless-phase2",
            fact_id="fact-sourceless-phase2",
            source="manual",
            approval_state="confirmed",
            status="active",
            source_refs=[],
        )


async def test_business_brain_write_service_uses_one_contract_for_manual_and_ai(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    manual = _input(
        workspace=workspace,
        update_id="update-manual-phase2",
        fact_id="fact-manual-phase2",
        source="manual",
        approval_state="confirmed",
        status="active",
    )
    ai = _input(
        workspace=workspace,
        update_id="update-ai-phase2",
        fact_id="fact-ai-phase2",
        source="ai_proposal",
        approval_state="proposed",
        status="proposed",
        value={"text": "AI proposed delivery note."},
        source_refs=["llm_trace:phase2", "owner_message:2"],
    )

    manual_result = await service.apply(manual)
    ai_result = await service.apply(ai)

    assert manual_result.fact.status == "active"
    assert ai_result.fact.status == "proposed"
    assert manual_result.update.source == "manual"
    assert ai_result.update.source == "ai_proposal"
    assert manual_result.update.schema_version == ai_result.update.schema_version
    assert await _count(db_session, BusinessBrainFactRecord) == 2
    assert await _count(db_session, BusinessBrainUpdateRecord) == 2


async def test_business_brain_projection_rebuild_uses_active_fact_records(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    active = _input(
        workspace=workspace,
        update_id="update-active-replay-phase2",
        fact_id="fact-active-replay-phase2",
        source="manual",
        approval_state="confirmed",
        status="active",
        value={"text": "Tasdiqlangan yetkazib berish siyosati."},
    )
    proposed = _input(
        workspace=workspace,
        update_id="update-proposed-replay-phase2",
        fact_id="fact-proposed-replay-phase2",
        source="ai_proposal",
        approval_state="proposed",
        status="proposed",
        value={"text": "Tasdiqlanmagan AI taklifi."},
        source_refs=["llm_trace:phase2", "owner_message:2"],
    )

    await service.apply(active)
    await service.apply(proposed)
    rebuilt = await service.rebuild_projection(
        workspace_id=workspace.id,
        projection_ref="business_brain:business:delivery",
        projection_type="business_brain",
        entity_ref="business:delivery",
    )

    assert rebuilt.state == {
        "delivery_policy": {"text": "Tasdiqlangan yetkazib berish siyosati."}
    }
    assert rebuilt.source_refs == [
        "fact:fact-active-replay-phase2",
        "owner_message:1",
    ]


async def _count(db_session: AsyncSession, model: type[Any]) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(model)) or 0)
