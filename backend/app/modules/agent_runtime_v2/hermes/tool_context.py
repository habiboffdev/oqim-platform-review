"""Thread-local grounding stash for the synchronous Hermes loop.

The packaged Hermes agent loop is synchronous and we run it via
``asyncio.to_thread``. Tools executing inside that worker thread cannot touch
the async DB, so the async adapter pre-fetches grounding (catalog/price/rules
facts) and transcript history, then stashes them in this contextvar before
handing control to the loop. ``asyncio.to_thread`` copies the calling context
into the worker thread, so the read-only spine tools can read the stash there.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.agent_talking.contracts import TalkBundle


@dataclass
class ToolContext:
    workspace_id: int
    agent_id: int
    conversation_id: int | None
    grounding: list[str]
    history: list[str]
    # Agent kind (the `_agent_kind` taxonomy: seller_agent / custom_agent /
    # setup_agent / ...). Carried so the compaction compressor can pick a
    # kind-scoped summary template. Engine + SessionCompactionService set the
    # real kind; an absent ToolContext resolves to None -> Hermes default.
    agent_kind: str = "custom_agent"
    agent_session_id: int | None = None
    hermes_run_id: str | None = None
    voice_examples: list[str] = field(default_factory=list)
    authority_warnings: list[str] = field(default_factory=list)
    chain_name: str = "FLASH_CHAIN"
    loop: object | None = None   # the running event loop; set by the adapter for the shim bridge
    tool_errors: list[str] = field(default_factory=list)
    search_count: int = 0                                   # brain searches issued this run (cap guard)
    searched_queries: set[str] = field(default_factory=set) # normalized queries already searched (dedup guard)
    talk_bundle: TalkBundle | None = None
    allowed_tool_names: frozenset[str] | None = None
    # Force a tool call (Gemini mode=ANY) pinned to allowed_tool_names even when
    # no talk tool is present — the forced commercial-finalization pass grants
    # only conversation.set_state and must call it. Defaults False so every
    # existing reply turn keeps the unchanged talk-forcing behavior.
    force_tool_call: bool = False
    max_catalog_searches: int | None = None
    catalog_search_count: int = 0
    catalog_enable_semantic: bool | None = None
    catalog_enable_rerank: bool | None = None
    prompt_cache: dict[str, object] | None = None
    business_action_refs: list[str] = field(default_factory=list)
    tool_authority_lines: list[str] = field(default_factory=list)
    intelligence_payloads: list[dict] = field(default_factory=list)
    record_payload: dict | None = None
    # Per-run Hermes context window in tokens (gemini's true window by default).
    # Set by the engine from profile.hermes_settings.context_length and read by
    # the context-length vendor patch at AIAgent construction. See context_config.py.
    context_window: int = 1_048_576
    # Side-channel for native multimodal perception: the CURRENT turn's media
    # (TurnMediaPart list) staged by the dispatcher. The OpenAI shim reads this
    # and hands it to generate_with_tools, which attaches the bytes to the live
    # Gemini turn. Empty for text-only turns -> zero overhead. Never replayed.
    current_turn_media: list = field(default_factory=list)
    # Bare live-call rendering of this turn's media (e.g. "[Voice message]"),
    # forwarded to generate_with_tools as live_media_text for the boundary swap:
    # the stored session keeps the labeled transcript; only the per-call Gemini
    # content is swapped so the model perceives the audio/image, not text.
    live_media_text: str | None = None

current_tool_context: ContextVar[ToolContext | None] = ContextVar(
    "current_tool_context", default=None)

@contextlib.contextmanager
def use_tool_context(ctx: ToolContext):
    token = current_tool_context.set(ctx)
    try:
        yield ctx
    finally:
        current_tool_context.reset(token)
