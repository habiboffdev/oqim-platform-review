from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnLease:
    turn_session_id: int
    workspace_id: int
    conversation_id: int
    agent_id: int
    latest_customer_message_id: int
    turn_revision: int
    generation: int
