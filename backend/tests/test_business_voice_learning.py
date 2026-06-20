from unittest.mock import AsyncMock

import pytest

from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.business_brain.voice_learning import BusinessVoiceLearningService
from app.modules.extraction_runtime.contracts import ExtractionCandidate


pytestmark = pytest.mark.asyncio


async def test_voice_candidate_write_sets_quality_score_with_keyword_arguments():
    service = object.__new__(BusinessVoiceLearningService)
    memory = AsyncMock()
    memory.write_memory_fact = AsyncMock()
    service._memory = memory

    candidate = ExtractionCandidate(
        candidate_id="voice-1",
        workspace_id=1,
        owner="business_brain",
        profile_ref="seller_voice.v1",
        kind="voice_observation",
        entity_ref="seller_voice",
        operation="create",
        value={"tone": "friendly"},
        confidence=0.91,
        risk_tier="low",
        evidence_refs=["message:1"],
        evidence_state="valid",
        requires_review=False,
        reason_code="learned_voice",
    )

    wrote_active = await service._write_voice_candidate(
        workspace_id=1,
        candidate=candidate,
        message_count=12,
        correlation_id="corr-1",
        idempotency_key="voice-run-1",
    )

    assert wrote_active is True
    written = memory.write_memory_fact.await_args.args[0]
    assert isinstance(written, MemoryFactWriteInput)
    assert written.value["quality_score"] == "adequate"
