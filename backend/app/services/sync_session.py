"""Authoritative frontend sync-session contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.services.conversation_state import (
    get_customer_conversation_state,
    project_dialog_last_message_text,
    project_dialog_unread_count,
)


ProjectionName = Literal[
    "messages",
    "conversation_state",
    "seller_agent_replies",
    "media",
    "read_state",
    "conversations",
    "dashboard",
]
ProjectionMode = Literal["delta", "reset"]
SyncAction = Literal[
    "noop",
    "refresh_scoped_runtime_delta",
    "refresh_scoped_runtime",
    "invalidate_all",
]

BOUNDED_MESSAGE_DELTA_LIMIT = 200


@dataclass(frozen=True, slots=True)
class SyncProjection:
    name: ProjectionName
    mode: ProjectionMode
    conversation_id: int | None = None
    after_conversation_seq: int | None = None
    latest_conversation_seq: int | None = None
    latest_conversation_revision: int | None = None

    def to_dict(self) -> dict[str, int | str]:
        payload: dict[str, int | str] = {
            "name": self.name,
            "mode": self.mode,
        }
        if self.conversation_id is not None:
            payload["conversation_id"] = self.conversation_id
        if self.after_conversation_seq is not None:
            payload["after_conversation_seq"] = self.after_conversation_seq
        if self.latest_conversation_seq is not None:
            payload["latest_conversation_seq"] = self.latest_conversation_seq
        if self.latest_conversation_revision is not None:
            payload["latest_conversation_revision"] = self.latest_conversation_revision
        return payload


@dataclass(frozen=True, slots=True)
class SyncSessionResponse:
    action: SyncAction
    server_sequence: int
    client_sequence: int
    conversation_id: int | None = None
    after_conversation_seq: int | None = None
    latest_conversation_seq: int | None = None
    latest_conversation_revision: int | None = None
    conversation_state: dict[str, int | str | None] | None = None
    projections: tuple[SyncProjection, ...] = ()

    @property
    def kind(self) -> Literal["noop", "delta", "reset_required"]:
        if self.action == "noop":
            return "noop"
        if self.action == "refresh_scoped_runtime_delta":
            return "delta"
        return "reset_required"

    def to_websocket_data(self) -> dict:
        data = {
            "kind": self.kind,
            "action": self.action,
            "server_sequence": self.server_sequence,
            "client_sequence": self.client_sequence,
            "projections": [projection.to_dict() for projection in self.projections],
        }
        if self.conversation_id is not None:
            data["conversation_id"] = self.conversation_id
        if self.after_conversation_seq is not None:
            data["after_conversation_seq"] = self.after_conversation_seq
        if self.latest_conversation_seq is not None:
            data["latest_conversation_seq"] = self.latest_conversation_seq
        if self.latest_conversation_revision is not None:
            data["latest_conversation_revision"] = self.latest_conversation_revision
        if self.conversation_state is not None:
            data["conversation_state"] = self.conversation_state
        return data


async def build_sync_session(
    *,
    session: AsyncSession,
    workspace_id: int,
    server_sequence: int,
    client_sequence: int,
    active_conversation_id: int | None = None,
    last_seen_conversation_seq: int | None = None,
    last_seen_conversation_revision: int | None = None,
) -> SyncSessionResponse:
    if server_sequence <= client_sequence:
        return SyncSessionResponse(
            action="noop",
            server_sequence=server_sequence,
            client_sequence=client_sequence,
        )

    if active_conversation_id is None:
        return SyncSessionResponse(
            action="invalidate_all",
            server_sequence=server_sequence,
            client_sequence=client_sequence,
            projections=(
                SyncProjection(name="conversations", mode="reset"),
                SyncProjection(name="seller_agent_replies", mode="reset"),
                SyncProjection(name="read_state", mode="reset"),
                SyncProjection(name="dashboard", mode="reset"),
            ),
        )

    if last_seen_conversation_seq is None:
        return SyncSessionResponse(
            action="refresh_scoped_runtime",
            server_sequence=server_sequence,
            client_sequence=client_sequence,
            conversation_id=active_conversation_id,
            projections=(
                SyncProjection(name="messages", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="media", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="conversation_state", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="seller_agent_replies", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="read_state", mode="reset", conversation_id=active_conversation_id),
            ),
        )

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == active_conversation_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    if conversation is None:
        return SyncSessionResponse(
            action="invalidate_all",
            server_sequence=server_sequence,
            client_sequence=client_sequence,
            projections=(
                SyncProjection(name="conversations", mode="reset"),
                SyncProjection(name="seller_agent_replies", mode="reset"),
                SyncProjection(name="read_state", mode="reset"),
                SyncProjection(name="dashboard", mode="reset"),
            ),
        )

    latest_seq = int(conversation.message_sequence or 0)
    latest_revision = int(conversation.message_revision or 0)
    can_delta_messages = _can_delta_messages(
        latest_seq=latest_seq,
        latest_revision=latest_revision,
        last_seen_seq=last_seen_conversation_seq,
        last_seen_revision=last_seen_conversation_revision,
    )

    if can_delta_messages and last_seen_conversation_seq is not None:
        return SyncSessionResponse(
            action="refresh_scoped_runtime_delta",
            server_sequence=server_sequence,
            client_sequence=client_sequence,
            conversation_id=active_conversation_id,
            after_conversation_seq=last_seen_conversation_seq,
            latest_conversation_seq=latest_seq,
            latest_conversation_revision=latest_revision,
            conversation_state=_build_conversation_state_projection(conversation),
            projections=(
                SyncProjection(
                    name="messages",
                    mode="delta",
                    conversation_id=active_conversation_id,
                    after_conversation_seq=last_seen_conversation_seq,
                    latest_conversation_seq=latest_seq,
                    latest_conversation_revision=latest_revision,
                ),
                SyncProjection(
                    name="media",
                    mode="delta",
                    conversation_id=active_conversation_id,
                    after_conversation_seq=last_seen_conversation_seq,
                    latest_conversation_seq=latest_seq,
                    latest_conversation_revision=latest_revision,
                ),
                SyncProjection(name="conversation_state", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="seller_agent_replies", mode="reset", conversation_id=active_conversation_id),
                SyncProjection(name="read_state", mode="reset", conversation_id=active_conversation_id),
            ),
        )

    return SyncSessionResponse(
        action="refresh_scoped_runtime",
        server_sequence=server_sequence,
        client_sequence=client_sequence,
        conversation_id=active_conversation_id,
        latest_conversation_seq=latest_seq,
        latest_conversation_revision=latest_revision,
        conversation_state=_build_conversation_state_projection(conversation),
        projections=(
            SyncProjection(name="messages", mode="reset", conversation_id=active_conversation_id),
            SyncProjection(name="media", mode="reset", conversation_id=active_conversation_id),
            SyncProjection(name="conversation_state", mode="reset", conversation_id=active_conversation_id),
            SyncProjection(name="seller_agent_replies", mode="reset", conversation_id=active_conversation_id),
            SyncProjection(name="read_state", mode="reset", conversation_id=active_conversation_id),
        ),
    )


def _can_delta_messages(
    *,
    latest_seq: int,
    latest_revision: int,
    last_seen_seq: int | None,
    last_seen_revision: int | None,
) -> bool:
    if last_seen_seq is None or last_seen_seq < 0:
        return False
    if latest_seq <= last_seen_seq:
        return False
    seq_gap = latest_seq - last_seen_seq
    if seq_gap > BOUNDED_MESSAGE_DELTA_LIMIT:
        return False
    if last_seen_revision is None or last_seen_revision < 0:
        return True
    revision_gap = latest_revision - last_seen_revision
    return revision_gap == seq_gap


def _build_conversation_state_projection(conversation: Conversation) -> dict[str, int | str | None]:
    return {
        "last_message_text": project_dialog_last_message_text(
            conversation,
            local_text=None,
            local_at=None,
        ),
        "last_message_at": _project_last_message_at(conversation),
        "unread_count": project_dialog_unread_count(conversation) or 0,
        "latest_conversation_seq": int(conversation.message_sequence or 0),
        "latest_conversation_revision": int(conversation.message_revision or 0),
    }


def _project_last_message_at(conversation: Conversation) -> str | None:
    local_at = _ensure_aware_utc(conversation.last_message_at)
    state = get_customer_conversation_state(conversation)
    dialog_state = state.sync.dialog if state.sync else None
    projected_at = _parse_iso_datetime(dialog_state.last_message_date if dialog_state else None)
    if projected_at is not None and (local_at is None or projected_at >= local_at):
        return projected_at.isoformat()
    if local_at is not None:
        return local_at.isoformat()
    return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware_utc(parsed)


def _ensure_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
