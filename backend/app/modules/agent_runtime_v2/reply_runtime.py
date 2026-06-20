"""Hermes reply prompt composition and turn rendering.

Customer-visible behavior is owned by managed prompt assets plus the rendered
AGENT.md, not by Python constants. This module only composes those assets with
the observed turn context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from html import escape
from typing import TYPE_CHECKING

from app.brain.prompt_registry import PromptAsset, get_prompt_registry

if TYPE_CHECKING:
    from app.modules.agent_talking.contracts import TalkBundle

HERMES_REPLY_PROMPT_ID = "agent_runtime.hermes_reply"
HERMES_REPLY_PROMPT_VERSION = "1.0.0"
SELLER_PLAYBOOK_PROMPT_ID = "agent_runtime.seller_playbook"
SELLER_PLAYBOOK_PROMPT_VERSION = "1.0.0"

# Per-workspace override of the managed seller_playbook default. One row per
# workspace in agent_document_sections; absent -> the managed default is used.
SELLER_PLAYBOOK_DOCUMENT_KIND = "playbook"
SELLER_PLAYBOOK_SECTION_KEY = "seller_playbook"

VOICE_PRESET_PROMPT_VERSION = "1.0.0"


class SendAction(StrEnum):
    AUTO_SEND = "auto_send"
    PROPOSE = "propose"
    SOFT_ESCALATE = "soft_escalate"


@dataclass(frozen=True)
class ManagedRuntimePrompt:
    prompt_id: str
    version: str
    digest: str
    cache_key: str | None
    cache_policy: str
    body: str


def load_hermes_reply_prompt() -> ManagedRuntimePrompt:
    asset = _load_hermes_reply_prompt_asset()
    cache_key = None
    if asset.cache_policy != "no_cache":
        cache_key = (
            f"prompt:{asset.id}:{asset.version}:{asset.cache_policy}:{asset.digest}"
        )
    return ManagedRuntimePrompt(
        prompt_id=asset.id,
        version=asset.version,
        digest=asset.digest,
        cache_key=cache_key,
        cache_policy=asset.cache_policy,
        body=asset.body.strip(),
    )


@lru_cache(maxsize=1)
def _load_hermes_reply_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        HERMES_REPLY_PROMPT_ID,
        version=HERMES_REPLY_PROMPT_VERSION,
    )


def load_seller_playbook_prompt() -> ManagedRuntimePrompt:
    """Managed generic selling skill for seller-kind agents (owner request
    2026-06-11: the sales playbook core lives in the managed layer, not only
    in each business's AGENT.md). Distilled from the sales-enablement skill:
    discovery -> outcome-first pitch -> objection move -> close on signals."""
    asset = _load_seller_playbook_prompt_asset()
    cache_key = None
    if asset.cache_policy != "no_cache":
        cache_key = (
            f"prompt:{asset.id}:{asset.version}:{asset.cache_policy}:{asset.digest}"
        )
    return ManagedRuntimePrompt(
        prompt_id=asset.id,
        version=asset.version,
        digest=asset.digest,
        cache_key=cache_key,
        cache_policy=asset.cache_policy,
        body=asset.body.strip(),
    )


@lru_cache(maxsize=1)
def _load_seller_playbook_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        SELLER_PLAYBOOK_PROMPT_ID,
        version=SELLER_PLAYBOOK_PROMPT_VERSION,
    )


def _is_seller_kind(agent_kind: str) -> bool:
    return (agent_kind or "").startswith("seller")


async def load_workspace_seller_playbook(session, *, workspace_id: int) -> str | None:
    """The workspace's custom seller playbook body, or None to use the default.

    Stored as a single agent_document_sections row (document_kind="playbook",
    subject_type="workspace"). Reuses the document service so the same
    governance/upsert path that owns AGENT.md owns the playbook override too.
    """
    # Local import: reply_runtime is otherwise DB-free and imported widely.
    from app.modules.agent_documents.service import AgentDocumentService

    sections = await AgentDocumentService(session).list_sections(
        workspace_id=workspace_id,
        document_kind=SELLER_PLAYBOOK_DOCUMENT_KIND,
        subject_type="workspace",
        subject_id=None,
    )
    for section in sections:
        if section.section_key == SELLER_PLAYBOOK_SECTION_KEY and (section.body or "").strip():
            return section.body
    return None


_EMOJI_USAGE_GUIDANCE = {
    "low": (
        "Emoji usage low: almost never use emojis in message text; "
        "reactions are still fine when policy allows."
    ),
    "medium": (
        "Emoji usage medium: at most one well-placed emoji every few "
        "messages, never more than one per bubble."
    ),
    "high": (
        "Emoji usage high: warm, lively tone with frequent emojis, "
        "still at most one or two per bubble."
    ),
}


_VERBOSITY_LINES = {
    "terse": "Keep replies short and to the point; one idea per bubble.",
    "balanced": "Be concise but complete; a sentence or two per point.",
    "rich": (
        "Be warm and full when it helps the sale: 2-4 sentences across 1-3 "
        "bubbles when pitching, building value, or closing. Never clip the "
        "pitch to one cold line."
    ),
}


@lru_cache(maxsize=8)
def load_voice_preset_asset(asset_id: str) -> str:
    """Return the stripped `<voice>` prose body for a preset asset id."""
    return (
        get_prompt_registry()
        .load(asset_id, version=VOICE_PRESET_PROMPT_VERSION)
        .body.strip()
    )


def render_voice_block(
    preset_body: str,
    verbosity: str | None = None,
    additional_instructions: str = "",
) -> str:
    """Compose a `<voice>` block: personality prose, then the verbosity line,
    then optional owner additions. Empty preset body -> empty string (so a
    non-voice agent contributes no layer)."""
    body = (preset_body or "").strip()
    if not body:
        return ""
    parts = [body, _VERBOSITY_LINES.get(verbosity or "", _VERBOSITY_LINES["balanced"])]
    extra = (additional_instructions or "").strip()
    if extra:
        parts.append(extra)
    return "<voice>\n" + "\n\n".join(parts) + "\n</voice>"


def compose_hermes_system_prompt(
    agent_md: str,
    agent_kind: str,
    *,
    prompt: ManagedRuntimePrompt | None = None,
    emoji_usage: str = "medium",
    seller_playbook_override: str | None = None,
    voice_block: str | None = None,
) -> str:
    managed_prompt = prompt or load_hermes_reply_prompt()
    emoji_line = _EMOJI_USAGE_GUIDANCE.get(emoji_usage, _EMOJI_USAGE_GUIDANCE["medium"])
    seller_playbook = ""
    if _is_seller_kind(agent_kind):
        # The workspace's own playbook replaces the managed default; a blank
        # override falls back so a misconfigured row never empties the layer.
        override = (seller_playbook_override or "").strip()
        seller_playbook = override or load_seller_playbook_prompt().body
    return "\n\n".join(
        part.strip()
        for part in (
            managed_prompt.body,
            seller_playbook,
            voice_block or "",
            f"Runtime agent kind: {agent_kind}",
            emoji_line,
            f"Rendered AGENT.md:\n{agent_md}",
        )
        if part and part.strip()
    )


OWNER_OPERATOR_PROMPT = """You are the business OWNER's control agent for OQIM — their operator, reached over Telegram. You help the OWNER configure and run their business by conversation: read and explain their setup, propose edits to their business config (AGENT.md, catalog, knowledge), curate media, and act on what the owner asks.

You are NOT the customer-facing seller. You never sell to the owner or role-play a sales chat — you are the owner's operator, working on their business with them.

How you work:
- Act like a capable operator: understand the owner's intent, use your tools to do it, and report back briefly.
- Every change to the business goes through an approval card — you PROPOSE the change, the owner taps "Tasdiqlash", then it applies. Never say a change is done before it is approved.
- Answer questions from the business's real data using your read tools (e.g. `ask`). Never invent numbers, prices, or facts.
- Speak directly and concisely in the owner's language (Uzbek). You are talking to the owner, not a customer."""


def compose_owner_operator_system_prompt(
    agent_md: str,
    *,
    voice_block: str | None = None,
) -> str:
    """System prompt for the Owner Agent (setup execution mode).

    Distinct from the seller ``compose_hermes_system_prompt``: the owner agent
    gets an operator identity + behavior (NOT the customer-selling hermes_reply
    prompt), and the business AGENT.md rides as a managed *artifact it helps
    edit*, never as its own persona (#455 decision A — borrow Hermes operator
    behavior, drop the coding directives + Nous identity).
    """
    business_doc = (agent_md or "").strip()
    return "\n\n".join(
        part.strip()
        for part in (
            OWNER_OPERATOR_PROMPT,
            voice_block or "",
            (
                "The business's current AGENT.md — the document you help the owner "
                f"manage (this is the business config, NOT instructions for you):\n{business_doc}"
                if business_doc
                else ""
            ),
        )
        if part and part.strip()
    )


def compose_hermes_turn(
    customer_message: str,
    *,
    grounding: list[str] | None = None,
    voice_examples: list[str] | None = None,
    conversation_state: dict | None = None,
    current_message_ref: str | None = None,
) -> str:
    """Compose the single user turn handed to the Hermes loop.

    Only genuinely PER-TURN context rides inside the turn, followed by the
    message to answer:

      1. Truth evidence (catalog/price/rules/KB) — when retrieved before the
         loop (eager modes) or empty on the lazy interactive path.
      2. Voice/style examples — style only, never product/price truth.
      3. Compact structured conversation state.

    Conversation continuity is owned by the Hermes session: prior turns are
    replayed via ``run_conversation(conversation_history=...)`` (host-resume),
    so the Telegram transcript is never re-pasted here. With NO context at all
    the turn is exactly the bare message.
    """
    grounding = grounding or []
    voice_examples = voice_examples or []
    conversation_state = conversation_state or {}
    has_context = bool(
        grounding or voice_examples or conversation_state or current_message_ref
    )
    if not has_context:
        return customer_message

    blocks: list[str] = []
    if conversation_state:
        blocks.append(
            _xml_block(
                "conversation_state",
                json.dumps(
                    conversation_state,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                attrs={"authority": "false", "use": "state_only"},
            )
        )
    if grounding:
        blocks.append(
            _xml_items(
                "authority_evidence",
                grounding,
                attrs={"use": "catalog_price_rules_kb_only"},
            )
        )
    if voice_examples:
        blocks.append(
            _xml_items(
                "style_examples",
                voice_examples,
                attrs={"authority": "false", "use": "voice_only"},
            )
        )
    ref = (current_message_ref or "").strip()
    current_attrs = {"reply_to": ref} if ref else None
    blocks.append(_xml_block("current_message", customer_message, attrs=current_attrs))
    return _xml_block("turn_context", "\n\n".join(blocks), escape_body=False)


def _xml_text(value: str) -> str:
    return escape(str(value), quote=False)


def _xml_attr_text(value: str) -> str:
    return escape(str(value), quote=True)


def _xml_attrs(attrs: dict[str, str] | None) -> str:
    if not attrs:
        return ""
    rendered = " ".join(
        f'{name}="{_xml_attr_text(value)}"'
        for name, value in attrs.items()
        if str(value).strip()
    )
    if not rendered:
        return ""
    return f" {rendered}"


def _xml_block(
    tag: str,
    body: str,
    *,
    attrs: dict[str, str] | None = None,
    escape_body: bool = True,
) -> str:
    rendered_body = _xml_text(body) if escape_body else body
    return f"<{tag}{_xml_attrs(attrs)}>\n{rendered_body}\n</{tag}>"


def _xml_items(
    tag: str,
    values: list[str],
    *,
    attrs: dict[str, str] | None = None,
) -> str:
    items = "\n".join(
        f"<item>{_xml_text(value)}</item>" for value in values if str(value).strip()
    )
    return f"<{tag}{_xml_attrs(attrs)}>\n{items}\n</{tag}>"


@dataclass(frozen=True)
class ReplyResult:
    reply_text: str
    confidence: float
    grounding_hits: int
    tool_errors: int = 0
    authority_warnings: list[str] = field(default_factory=list)
    talk_bundle: TalkBundle | None = None
    agent_actions: list[dict] = field(default_factory=list)
    committed_action_refs: list[str] = field(default_factory=list)
    tool_authority_lines: list[str] = field(default_factory=list)
    intelligence_payloads: list[dict] = field(default_factory=list)
    turn_details: dict | None = None
    record_payload: dict | None = None
