from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hermes_runtime_policy import HermesAutopilotCircuitBreaker


@dataclass(frozen=True)
class AutopilotCircuitBreakerDecision:
    allowed: bool
    forced_action: str | None = None
    reason: str | None = None
    scope: str | None = None

    @property
    def reason_code(self) -> str | None:
        if self.reason is None or self.scope is None:
            return None
        return f"autopilot_circuit_breaker:{self.scope}:{self.reason}"


class AutopilotCircuitBreakerService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def check(
        self,
        *,
        workspace_id: int,
        agent_id: int | None = None,
    ) -> AutopilotCircuitBreakerDecision:
        rows = await self._active_breakers(workspace_id=workspace_id, agent_id=agent_id)
        for scope_type, scope_id in (
            ("agent", agent_id),
            ("workspace", workspace_id),
            ("global", None),
        ):
            if scope_id is None and scope_type != "global":
                continue
            row = rows.get((scope_type, scope_id))
            if row is not None:
                return AutopilotCircuitBreakerDecision(
                    allowed=False,
                    forced_action="propose",
                    reason=row.reason,
                    scope=scope_type,
                )
        return AutopilotCircuitBreakerDecision(allowed=True)

    async def _active_breakers(
        self,
        *,
        workspace_id: int,
        agent_id: int | None,
    ) -> dict[tuple[str, int | None], HermesAutopilotCircuitBreaker]:
        conditions = [
            (HermesAutopilotCircuitBreaker.scope_type == "global")
            & (HermesAutopilotCircuitBreaker.scope_id.is_(None)),
            (HermesAutopilotCircuitBreaker.scope_type == "workspace")
            & (HermesAutopilotCircuitBreaker.scope_id == workspace_id),
        ]
        if agent_id is not None:
            conditions.append(
                (HermesAutopilotCircuitBreaker.scope_type == "agent")
                & (HermesAutopilotCircuitBreaker.scope_id == agent_id)
            )

        query = select(HermesAutopilotCircuitBreaker).where(
            HermesAutopilotCircuitBreaker.active.is_(True),
            or_(*conditions),
        )
        result = await self._db.execute(query)
        return {(row.scope_type, row.scope_id): row for row in result.scalars()}
