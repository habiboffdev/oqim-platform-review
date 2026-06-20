from __future__ import annotations

from collections import defaultdict
from time import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.hermes_session import HermesSessionMessageRecord, HermesSessionRecord


class InMemoryHermesSessionDB:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def create_session(self, session_id: str, source: str, **kwargs: Any) -> str:
        self.sessions.setdefault(
            session_id,
            {
                "id": session_id,
                "source": source,
                "started_at": time(),
                "message_count": 0,
                **kwargs,
            },
        )
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str | None = None,
        tool_name: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        finish_reason: str | None = None,
        **kwargs: Any,
    ) -> int:
        row = {
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "finish_reason": finish_reason,
            **kwargs,
        }
        self.messages[session_id].append(row)
        if session_id in self.sessions:
            self.sessions[session_id]["message_count"] = len(self.messages[session_id])
        return len(self.messages[session_id])

    def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
        self.sessions.setdefault(session_id, {"id": session_id, "source": "oqim"})[
            "system_prompt"
        ] = system_prompt

    def update_token_counts(self, session_id: str, **kwargs: Any) -> None:
        self.sessions.setdefault(session_id, {"id": session_id, "source": "oqim"}).update(
            kwargs
        )

    def get_session_title(self, session_id: str) -> str | None:
        session = self.sessions.get(session_id) or {}
        title = session.get("title")
        return str(title) if title else None

    def set_session_title(self, session_id: str, title: str) -> bool:
        self.sessions.setdefault(session_id, {"id": session_id, "source": "oqim"})[
            "title"
        ] = title
        return True

    def end_session(self, session_id: str, end_reason: str) -> None:
        self.sessions.setdefault(session_id, {"id": session_id, "source": "oqim"})[
            "end_reason"
        ] = end_reason


class OqimHermesSessionDB(InMemoryHermesSessionDB):
    """Hermes-compatible sync session DB with async OQIM persistence boundaries."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        workspace_id: int,
        agent_session_id: int,
    ) -> None:
        super().__init__()
        self._db = db
        self.workspace_id = int(workspace_id)
        self.agent_session_id = int(agent_session_id)
        self._record_ids_by_session_id: dict[str, int] = {}
        self._persisted_counts: dict[str, int] = {}

    @classmethod
    async def load(
        cls,
        db: AsyncSession,
        *,
        workspace_id: int,
        agent_session_id: int,
    ) -> OqimHermesSessionDB:
        store = cls(
            db,
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
        )
        rows = list(
            (
                await db.execute(
                    select(HermesSessionRecord).where(
                        HermesSessionRecord.workspace_id == workspace_id,
                        HermesSessionRecord.agent_session_id == agent_session_id,
                    )
                )
            ).scalars().all()
        )
        for row in rows:
            session_id = row.hermes_session_id
            session_payload = {
                "id": session_id,
                "source": row.source,
                "title": row.title,
                "system_prompt": row.system_prompt,
                "message_count": int(row.message_count or 0),
                "token_counts": row.token_counts or {},
                **(row.metadata_json or {}),
            }
            if row.ended_reason:
                session_payload["end_reason"] = row.ended_reason
            store.sessions[session_id] = session_payload
            store._record_ids_by_session_id[session_id] = int(row.id)
            store._persisted_counts[session_id] = int(row.message_count or 0)

            messages = list(
                (
                    await db.execute(
                        select(HermesSessionMessageRecord)
                        .where(HermesSessionMessageRecord.hermes_session_id == row.id)
                        .order_by(HermesSessionMessageRecord.sequence.asc())
                    )
                ).scalars().all()
            )
            store.messages[session_id] = [
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_name": message.tool_name,
                    "tool_calls": message.tool_calls,
                    "tool_call_id": message.tool_call_id,
                    "finish_reason": message.finish_reason,
                    **(message.metadata_json or {}),
                }
                for message in messages
            ]
            store.sessions[session_id]["message_count"] = len(store.messages[session_id])
            store._persisted_counts[session_id] = len(store.messages[session_id])
        return store

    async def flush(self) -> None:
        for session_id, payload in self.sessions.items():
            record = await self._load_or_create_record(session_id, payload)
            messages = list(self.messages.get(session_id, []))
            persisted_count = int(self._persisted_counts.get(session_id, 0))
            for index, message in enumerate(messages[persisted_count:], start=persisted_count + 1):
                self._db.add(
                    HermesSessionMessageRecord(
                        hermes_session_id=record.id,
                        workspace_id=self.workspace_id,
                        agent_session_id=self.agent_session_id,
                        sequence=index,
                        role=str(message.get("role") or ""),
                        content=message.get("content"),
                        tool_name=message.get("tool_name"),
                        tool_calls=message.get("tool_calls"),
                        tool_call_id=message.get("tool_call_id"),
                        finish_reason=message.get("finish_reason"),
                        metadata_json=_message_metadata(message),
                        created_at=utc_now(),
                    )
                )
            record.message_count = len(messages)
            record.updated_at = utc_now()
            self._persisted_counts[session_id] = len(messages)
        await self._db.flush()

    async def _load_or_create_record(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> HermesSessionRecord:
        record_id = self._record_ids_by_session_id.get(session_id)
        record = await self._db.get(HermesSessionRecord, record_id) if record_id else None
        if record is None:
            record = await self._db.scalar(
                select(HermesSessionRecord).where(
                    HermesSessionRecord.hermes_session_id == session_id
                )
            )
        if record is None:
            record = HermesSessionRecord(
                workspace_id=self.workspace_id,
                agent_session_id=self.agent_session_id,
                hermes_session_id=session_id,
                created_at=utc_now(),
            )
            self._db.add(record)
            await self._db.flush()
        self._record_ids_by_session_id[session_id] = int(record.id)
        record.workspace_id = self.workspace_id
        record.agent_session_id = self.agent_session_id
        record.source = str(payload.get("source") or "oqim")
        record.title = str(payload["title"]) if payload.get("title") else None
        record.system_prompt = str(payload.get("system_prompt") or "")
        record.token_counts = dict(payload.get("token_counts") or {})
        record.metadata_json = _session_metadata(payload)
        record.ended_reason = (
            str(payload["end_reason"]) if payload.get("end_reason") else None
        )
        record.message_count = len(self.messages.get(session_id, []))
        record.updated_at = utc_now()
        return record


_SESSION_STRUCTURAL_KEYS = {
    "id",
    "source",
    "title",
    "system_prompt",
    "message_count",
    "token_counts",
    "end_reason",
}


def _session_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _SESSION_STRUCTURAL_KEYS
    }


_MESSAGE_STRUCTURAL_KEYS = {
    "role",
    "content",
    "tool_name",
    "tool_calls",
    "tool_call_id",
    "finish_reason",
}


def _message_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _MESSAGE_STRUCTURAL_KEYS
    }
