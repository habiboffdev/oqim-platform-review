"""Owner-turn entrypoint — the customer-free sibling of dispatch_agent_turn.

The owner talks to the setup agent over the owner channel. There is no Customer
and no Conversation: the AgentSession is keyed by owner_chat_id (conversation_id
NULL, Option B migration), and the run executes the *same* Generic Agent Runtime
in "setup" execution mode. The agent's business-mutating tools emit approval
proposals (owner.edit_doc, etc.) that flow through the Action Runtime → owner-bot
card → approve → execute → audit seam; this entrypoint only runs the turn and
delivers the reply text to the owner's chat (spike #439).

Deliberately NOT used here (they are customer-turn machinery): ConversationTurnSession,
TurnLifecycle, finalize_turn.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.agent_runtime_v2.dispatcher import (
    _agent_session_payload,
    _generic_runtime_payload,
)
from app.modules.agent_runtime_v2.hermes.hermes_home_context import (
    use_hermes_home,
    workspace_hermes_home,
)
from app.modules.agent_runtime_v2.hermes.session_store import OqimHermesSessionDB
from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeService
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.agent_talking.contracts import TalkActionKind
from app.modules.commercial_spine.contracts import utc_now as spine_utc_now
from app.modules.hermes_runtime.contracts import (
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    HermesRunPatch,
)
from app.modules.hermes_runtime.service import HermesRunService

logger = logging.getLogger(__name__)

_REPLY_KINDS = (TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG)


@dataclass
class OwnerTurnContext:
    """The minimal inputs for one owner turn (no Customer/Conversation)."""

    workspace_id: int
    agent_id: int
    owner_chat_id: int
    message_text: str


async def dispatch_owner_turn(
    *,
    db: AsyncSession,
    workspace_id: int,
    agent_id: int,
    owner_chat_id: int,
    message_text: str,
    delivery: Any,
) -> bool:
    """Run one owner turn through the Generic Agent Runtime and deliver the reply.

    Returns True when the turn ran (or was a dedupe no-op), False when the agent
    is missing / not in this workspace.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None or int(agent.workspace_id) != int(workspace_id):
        return False

    agent_session = await AgentSessionService(db).get_or_create(
        workspace_id=workspace_id,
        conversation_id=None,
        customer_id=None,
        agent_id=agent_id,
        channel="owner",
        owner_chat_id=owner_chat_id,
    )
    hermes_session_db = await OqimHermesSessionDB.load(
        db, workspace_id=workspace_id, agent_session_id=agent_session.id
    )

    run_service = HermesRunService(db)
    # Each owner message is its own turn -> its own run. The owner-bot poller
    # delivers each update exactly once (the offset advances before handling), so
    # there is no retry to dedupe; a fresh nonce keeps every message distinct.
    # (An event_count-derived key was wrong here: owner turns never append events,
    # so the counter stayed 0 and every message after the first deduped away.)
    correlation = (
        f"owner-turn:{workspace_id}:{owner_chat_id}:{agent_session.id}:{uuid.uuid4().hex}"
    )
    run = await run_service.start_or_dedupe(
        HermesRunInput(
            workspace_id=workspace_id,
            agent_id=agent_id,
            agent_kind=str(agent.agent_type or "setup"),
            lane=HermesRunLane.FAST_INTERACTIVE,
            run_mode=HermesRunMode.PERSONAL,
            trigger_type="owner_command",
            trigger_id=correlation,
            correlation_id=correlation,
            source_refs=[
                f"owner_chat:{owner_chat_id}",
                f"agent_session:{agent_session.id}",
            ],
            input_summary=(message_text or "")[:2000],
        )
    )
    if run.deduped:
        return True

    runtime = AgentRuntimeService(db)
    context = await runtime.gather_turn_context(
        workspace_id=workspace_id,
        agent_id=agent_id,
        customer_message=message_text,
        hermes_run_id=run.run_id,
        agent_session_id=agent_session.id,
        hermes_session_id=agent_session.hermes_session_id,
        session_db=hermes_session_db,
    )
    # Release the gather transaction before the multi-second Hermes loop.
    await db.commit()

    try:
        # Run-model X (spike #439): the owner/setup plane is Hermes-native with a
        # per-workspace HERMES_HOME (its own file-drop SKILL.md skills + config.yaml
        # for MCP). The contextvar is set across the engine run, which dispatches
        # via asyncio.to_thread (the context is copied into the worker, so Hermes's
        # runtime skill/config loading resolves to THIS workspace). A non-existent
        # home is safe — Hermes simply finds no skills. Seller plane never sets this.
        with use_hermes_home(workspace_hermes_home(workspace_id)):
            outcome = await runtime.run_from_context(context)
        await hermes_session_db.flush()
    except Exception as exc:
        await hermes_session_db.flush()
        await run_service.fail(
            run.run_id,
            error_code="owner_runtime_failed",
            error_message=str(exc)[:2000],
        )
        await db.commit()
        raise

    # The setup agent has no talk tools, so the reply is outcome.reply_text; still
    # honor a talk_bundle if a future owner profile grants talk tools.
    texts: list[str] = []
    if outcome.talk_bundle is not None:
        texts = [
            action.text
            for action in outcome.talk_bundle.actions
            if action.kind in _REPLY_KINDS and (action.text or "").strip()
        ]
    if not texts and (outcome.reply_text or "").strip():
        texts = [outcome.reply_text.strip()]

    for text in texts:
        await delivery.deliver_message(owner_chat_id, text, workspace_id=workspace_id)

    await run_service.complete(
        run.run_id,
        HermesRunPatch(
            completed_at=spine_utc_now(),
            confidence=outcome.confidence,
            output_action="owner_reply",
            details={
                "generic_agent_runtime": _generic_runtime_payload(
                    outcome=outcome, output_action="owner_reply"
                ),
                "agent_session": _agent_session_payload(agent_session),
                "runtime_telemetry": outcome.telemetry or {},
            },
        ),
    )
    await db.commit()
    return True
