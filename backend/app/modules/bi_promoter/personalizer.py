"""One-shot opener personalization. NOT a full agent turn — an outbound opener
has no inbound message to answer and needs no tools. Single managed-prompt call.
"""
from __future__ import annotations

from google.genai import types

from app.brain.llm import generate_with_fallback
from app.brain.llm_policy import FLASH_CHAIN
from app.modules.agent_talking.output_normalize import normalize_outgoing_text

_PROMPT = """You are the same warm Uzbek seller who runs this business's chats.
Write ONE short, friendly Telegram opener (max 2 sentences, Uzbek, no em-dashes)
to re-engage a known contact. Base it on the owner's message and the contact's
CRM history. Do not invent facts. Greet by name if given.

OWNER MESSAGE:
{base}

CONTACT NAME: {name}
CRM CONTEXT: {ctx}

Opener:"""


async def personalize_opener(
    *, workspace_id: int, base_message: str, contact_name: str, crm_context: str,
) -> str:
    contents = _PROMPT.format(base=base_message, name=contact_name or "(unknown)",
                              ctx=crm_context or "(none)")
    response = await generate_with_fallback(
        chain=FLASH_CHAIN, contents=contents,
        config=types.GenerateContentConfig(temperature=1.0),
        workspace_id=workspace_id, operation="promoter_opener")
    text = (getattr(response, "text", "") or "").strip()
    # Route through the house output boundary (deterministic no-em-dash form),
    # same as every other customer-facing text — a prompt instruction can't
    # guarantee it. This is the first proactive message, so it matters most.
    return normalize_outgoing_text(text or base_message)
