from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    MemoryFactWriteInput,
    VoiceProjectionRequest,
)
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
)
from app.modules.extraction_runtime.llm_provider import LLMGatewayCandidateProvider
from app.modules.extraction_runtime.persistence import ExtractionCandidatePersistenceService
from app.modules.extraction_runtime.runtime import (
    UniversalExtractionRuntime,
)

logger = get_logger("business_brain.voice_learning")

VOICE_LEARNING_MIN_MESSAGES = 1
VOICE_LEARNING_SOURCE_REF_PREFIX = "conversation_history:seller_voice"
VOICE_LEARNING_PROFILE_REF = "seller_voice.v1"
VOICE_LEARNING_ENTITY_REF = "seller_voice"


@dataclass(slots=True)
class VoiceLearningSnapshot:
    workspace_id: int
    message_count_analyzed: int
    accepted_observations: int
    quality_score: str
    projection: BusinessBrainProjection | None = None
    degraded_reasons: list[str] = field(default_factory=list)

    @property
    def voice_card(self) -> dict[str, Any]:
        traits = self.traits
        if not traits:
            return {}
        primary = traits[-1]
        return {
            "primary_language": primary.get("primary_language")
            or primary.get("language")
            or primary.get("language_mix"),
            "script": primary.get("script") or primary.get("writing_script"),
        }

    @property
    def message_pattern(self) -> str:
        traits = self.traits
        if not traits:
            return "unknown"
        return str(
            traits[-1].get("message_pattern")
            or traits[-1].get("selling_rhythm")
            or traits[-1].get("tone")
            or "learned"
        )

    @property
    def burst_count(self) -> int:
        traits = self.traits
        if not traits:
            return 1
        value = traits[-1].get("burst_count")
        return int(value) if isinstance(value, int | float) and value > 0 else 1

    @property
    def traits(self) -> list[dict[str, Any]]:
        if self.projection is None:
            return []
        state = self.projection.state
        traits = state.get("traits")
        return [dict(item) for item in traits] if isinstance(traits, list) else []


class BusinessVoiceLearningService:
    """Learns seller voice into Business Brain through Universal Extraction."""

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        extraction_runtime: UniversalExtractionRuntime | None = None,
    ) -> None:
        self._repository = repository
        gateway = LLMGateway(repository=repository)
        self._extraction_runtime = extraction_runtime or UniversalExtractionRuntime(
            candidate_provider=LLMGatewayCandidateProvider(gateway=gateway),
            provider_timeout_seconds=30.0,
        )
        self._persistence = ExtractionCandidatePersistenceService(repository=repository)
        self._memory = BusinessBrainMemoryService(repository=repository)

    async def learn_from_history(
        self,
        *,
        workspace_id: int,
        correlation_id: str,
        idempotency_key: str,
        limit: int = 50,
    ) -> VoiceLearningSnapshot:
        turns = await self._load_seller_turns(workspace_id=workspace_id, limit=limit)
        if not turns:
            return VoiceLearningSnapshot(
                workspace_id=workspace_id,
                message_count_analyzed=0,
                accepted_observations=0,
                quality_score="weak",
                degraded_reasons=["no_seller_messages"],
            )

        request = _voice_extraction_request(
            workspace_id=workspace_id,
            turns=turns,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        result = await self._extraction_runtime.extract(request)
        await self._persistence.persist_result(result)

        voice_candidates = [
            candidate
            for candidate in result.accepted_candidates
            if candidate.profile_ref == VOICE_LEARNING_PROFILE_REF
            and candidate.kind == "voice_observation"
        ]
        active_writes = 0
        for candidate in voice_candidates:
            wrote_active = await self._write_voice_candidate(
                workspace_id=workspace_id,
                candidate=candidate,
                message_count=len(turns),
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            if wrote_active:
                active_writes += 1

        projection = None
        if active_writes:
            projection = await self._memory.rebuild_voice_projection(
                VoiceProjectionRequest(workspace_id=workspace_id)
            )
        accepted_count = len(voice_candidates)
        return VoiceLearningSnapshot(
            workspace_id=workspace_id,
            message_count_analyzed=len(turns),
            accepted_observations=accepted_count,
            quality_score=_voice_quality_score(
                message_count=len(turns),
                accepted_observations=accepted_count,
            ),
            projection=projection,
            degraded_reasons=list(result.degraded_reasons),
        )

    async def _load_seller_turns(
        self,
        *,
        workspace_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        result = await self._repository.session.execute(
            select(Message, Customer.contact_type)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Customer, Conversation.customer_id == Customer.id)
            .where(
                Conversation.workspace_id == workspace_id,
                Message.sender_type == SenderType.SELLER.value,
                Message.content != "",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        turns: list[dict[str, Any]] = []
        for message, contact_type in result.all():
            text = " ".join((message.content or "").split()).strip()
            if len(text) < 2:
                continue
            turns.append(
                {
                    "message_id": int(message.id),
                    "conversation_id": int(message.conversation_id),
                    "contact_type": contact_type or "unknown",
                    "text": text,
                    "created_at": _iso(message.created_at),
                }
            )
        return list(reversed(turns))

    async def _write_voice_candidate(
        self,
        *,
        workspace_id: int,
        candidate: ExtractionCandidate,
        message_count: int,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool:
        value = dict(candidate.value)
        value.setdefault("message_count_analyzed", message_count)
        value.setdefault(
            "quality_score",
            _voice_quality_score(
                message_count=message_count,
                accepted_observations=1,
            ),
        )
        value.setdefault("delay_range", {"min_ms": 1500, "max_ms": 3000})
        is_active = candidate.risk_tier == "low"
        await self._memory.write_memory_fact(
            MemoryFactWriteInput(
                workspace_id=workspace_id,
                fact_id=f"voice_fact:{workspace_id}:{candidate.candidate_id}",
                fact_type="voice_fact",
                entity_ref=VOICE_LEARNING_ENTITY_REF,
                value=value,
                source_refs=list(candidate.evidence_refs),
                source="onboarding",
                status="active" if is_active else "proposed",
                approval_state="confirmed" if is_active else "proposed",
                confidence=candidate.confidence,
                risk_tier=candidate.risk_tier,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:{candidate.candidate_id}",
                actor_ref="business_voice_learning",
            )
        )
        return is_active


def _voice_extraction_request(
    *,
    workspace_id: int,
    turns: list[dict[str, Any]],
    correlation_id: str,
    idempotency_key: str,
) -> ExtractionRequest:
    parts = [
        ExtractionPart(
            kind="chat_turn",
            ref=f"message:{turn['message_id']}",
            payload={
                "message_ref": f"message:{turn['message_id']}",
                "conversation_id": turn["conversation_id"],
                "sender_type": "seller",
                "contact_type": turn["contact_type"],
                "text": turn["text"][:4000],
                "created_at": turn["created_at"],
            },
        )
        for turn in turns[:250]
    ]
    return ExtractionRequest(
        scope=ExtractionScope(workspace_id=workspace_id),
        source_kind="source_bundle",
        source_ref=f"{VOICE_LEARNING_SOURCE_REF_PREFIX}:{workspace_id}",
        parts=parts,
        profile_refs=[VOICE_LEARNING_PROFILE_REF],
        target_kinds=["voice_observation", "seller_rule"],
        correlation_id=correlation_id,
        idempotency_key=f"{idempotency_key}:seller-voice",
        max_parallelism=4,
        max_evidence_units=max(1, len(parts)),
        persist_mode="review_candidates",
    )


def _voice_quality_score(*, message_count: int, accepted_observations: int) -> str:
    if message_count >= 80 and accepted_observations >= 3:
        return "strong"
    if message_count >= 10 and accepted_observations >= 1:
        return "adequate"
    return "weak"


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    if value:
        return str(value)
    return None
