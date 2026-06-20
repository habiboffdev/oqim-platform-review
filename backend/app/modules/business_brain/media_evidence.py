from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.modules.business_brain.contracts import BusinessBrainFactUpdateInput
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository

MEDIA_EVIDENCE_VERSION = "media_evidence.v1"


async def persist_media_evidence_fact(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    conversation: Conversation,
    message: Message,
    runtime: MediaRuntime,
    media_evidence: dict[str, Any],
    occurred_at: datetime,
    correlation_id: str,
) -> bool:
    if media_evidence.get("schema_version") != MEDIA_EVIDENCE_VERSION:
        return False
    if conversation.customer_id is None:
        return False

    stable_id = _stable_digest(
        f"{workspace_id}:{conversation.id}:{message.id}:{runtime.media_ref}"
    )
    source_refs = [
        f"message:{message.id}:media",
        f"media:{runtime.media_ref}",
    ]
    value = {
        "schema_version": "business_brain_media_evidence.v1",
        "workspace_id": workspace_id,
        "conversation_ref": f"conversation:{conversation.id}",
        "customer_ref": f"customer:{conversation.customer_id}",
        "message_ref": f"message:{message.id}",
        "media_ref": runtime.media_ref,
        "channel": runtime.channel,
        "media_type": runtime.media_type,
        "mime_type": runtime.mime_type,
        "normalized_text": runtime.normalized_text,
        "evidence": dict(media_evidence),
    }
    confidence = _confidence(media_evidence.get("confidence"))
    write = BusinessBrainWriteService(repository=repository)
    result = await write.apply(
        BusinessBrainFactUpdateInput(
            update_id=f"media_evidence_update:{stable_id}",
            fact_id=f"media_evidence:{stable_id}",
            workspace_id=workspace_id,
            fact_type="media_evidence",
            entity_ref=f"media:{runtime.media_ref}",
            value=value,
            confidence=confidence,
            status="active",
            risk_tier="low",
            source="replay",
            approval_state="confirmed",
            source_refs=source_refs,
            idempotency_key=f"business_brain:media_evidence:{stable_id}",
            valid_from=occurred_at,
            applied_at=occurred_at,
            actor_type="system",
            actor_ref="event_spine",
            correlation_id=correlation_id,
        )
    )
    await repository.upsert_projection(
        BusinessBrainProjection(
            projection_ref=f"business_brain:media_evidence:{stable_id}",
            workspace_id=workspace_id,
            projection_type="media_evidence",
            entity_ref=f"media:{runtime.media_ref}",
            state={
                "fact_id": result.fact.fact_id,
                "message_ref": f"message:{message.id}",
                "conversation_ref": f"conversation:{conversation.id}",
                "customer_ref": f"customer:{conversation.customer_id}",
                "media_ref": runtime.media_ref,
                "media_type": runtime.media_type,
                "confidence": confidence,
                "evidence": dict(media_evidence),
            },
            source_refs=[f"fact:{result.fact.fact_id}", *source_refs],
        )
    )
    return True


def _confidence(value: Any) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 1.0))
    return 0.5


def _stable_digest(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
