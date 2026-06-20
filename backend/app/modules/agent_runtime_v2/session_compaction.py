"""On-demand session compaction (`oqim ai compact`).

Drives Hermes's NATIVE manual-compaction recipe (`AIAgent._compress_context`, the
same path as Hermes's built-in `/compress` command) on a stored agent_session,
then non-destructively repoints `agent_session.hermes_session_id` to the new
compacted child session. The original session rows are preserved. See
docs/superpowers/specs/2026-06-12-on-demand-session-compaction-design.md.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_session import AgentSession
from app.modules.agent_runtime_v2.context_config import resolve_context_window
from app.modules.agent_runtime_v2.hermes._bootstrap import ensure_hermes_runtime
from app.modules.agent_runtime_v2.hermes.openai_shim import (
    OQIM_SHIM_BASE_URL,
    install_shim_once,
)
from app.modules.agent_runtime_v2.hermes.oqim_tools import register_oqim_tools
from app.modules.agent_runtime_v2.hermes.session_store import OqimHermesSessionDB
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches


@dataclass(frozen=True)
class CompactionResult:
    noop: bool
    applied: bool
    before_messages: int
    after_messages: int
    before_tokens: int
    after_tokens: int
    old_session_id: str
    new_session_id: str | None
    headline: str
    token_line: str
    note: str | None


class SessionCompactionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compact(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        apply: bool,
        focus: str | None = None,
    ) -> CompactionResult:
        ensure_hermes_runtime()
        apply_vendor_patches()
        register_oqim_tools()
        install_shim_once()
        from agent.manual_compression_feedback import summarize_manual_compression
        from agent.model_metadata import estimate_request_tokens_rough
        from run_agent import AIAgent

        from app.modules.agent_runtime_context.service import _agent_kind

        agent_session = (
            await self.db.execute(
                select(AgentSession).where(
                    AgentSession.workspace_id == workspace_id,
                    AgentSession.agent_id == agent_id,
                    AgentSession.conversation_id == conversation_id,
                )
            )
        ).scalar_one_or_none()
        if agent_session is None:
            raise LookupError(
                f"no agent_session for workspace={workspace_id} agent={agent_id} "
                f"conversation={conversation_id}"
            )

        agent_row = await self.db.get(Agent, agent_id)
        window = resolve_context_window(
            (agent_row.channel_config if agent_row else None) or {}
        )

        store = await OqimHermesSessionDB.load(
            self.db, workspace_id=workspace_id, agent_session_id=agent_session.id
        )
        old_id = agent_session.hermes_session_id
        messages = list(store.messages.get(old_id, []))

        ctx = ToolContext(
            workspace_id=workspace_id, agent_id=agent_id, conversation_id=conversation_id,
            grounding=[], history=[], chain_name="FLASH_CHAIN",
            agent_kind=(_agent_kind(agent_row) if agent_row is not None else "custom_agent"),
            loop=asyncio.get_running_loop(), context_window=window,
        )

        def _run() -> dict:
            agent = AIAgent(
                base_url=OQIM_SHIM_BASE_URL, api_key="oqim-shim", provider="openai",
                api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
                ephemeral_system_prompt="# OQIM", skip_context_files=True,
                skip_memory=True, save_trajectories=False, quiet_mode=True,
                max_iterations=4, session_id=old_id, session_db=store,
            )
            agent._disable_streaming = True
            sys_prompt = getattr(agent, "_cached_system_prompt", "") or ""
            tools = getattr(agent, "tools", None) or None
            before_tokens = estimate_request_tokens_rough(
                messages, system_prompt=sys_prompt, tools=tools
            )
            with use_tool_context(ctx):
                compressed, _ = agent._compress_context(
                    messages, None, approx_tokens=before_tokens, focus_topic=focus
                )
            after_tokens = estimate_request_tokens_rough(
                compressed, system_prompt=sys_prompt, tools=tools
            )
            summary = summarize_manual_compression(
                messages, compressed, before_tokens, after_tokens
            )
            return {
                "agent": agent, "compressed": compressed,
                "before_tokens": before_tokens, "after_tokens": after_tokens,
                "summary": summary,
            }

        out = await asyncio.to_thread(_run)
        summary = out["summary"]
        agent = out["agent"]
        new_id = agent.session_id

        def _build(applied: bool) -> CompactionResult:
            return CompactionResult(
                noop=bool(summary["noop"]),
                applied=applied,
                before_messages=len(messages),
                after_messages=len(out["compressed"]),
                before_tokens=out["before_tokens"],
                after_tokens=out["after_tokens"],
                old_session_id=old_id,
                new_session_id=(new_id if applied else None),
                headline=summary["headline"],
                token_line=summary["token_line"],
                note=summary.get("note"),
            )

        # No-op or dry-run: nothing is written to the DB (the session rotation is
        # in-memory on the store until store.flush()), so there is nothing to roll
        # back — just return the preview. Rolling back here would wrongly discard
        # the caller's own uncommitted work.
        if summary["noop"] or not apply:
            return _build(applied=False)

        # Persist: write the compacted messages into the new child session, then
        # repoint the agent_session forward. INSERT-only; original rows untouched.
        agent._flush_messages_to_session_db(out["compressed"], None)
        await store.flush()
        agent_session.hermes_session_id = new_id
        await self.db.commit()
        return _build(applied=True)
