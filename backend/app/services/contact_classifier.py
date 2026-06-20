"""Contact classifier — Flash-Lite classifies Telegram contacts.

Reads conversation threads and determines whether each contact is a
customer, supplier, personal contact, work contact, or group.
Context-aware: direction of price questions determines customer vs supplier.

Batching strategy:
- `_prefilter_contacts()` handles obvious cases (groups, bots, empty) without LLM.
- `classify_contacts_batch_v2()` batches remaining contacts 20-per-call, slashing
  token usage from ~270k-520k → ~6k per onboarding run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_LITE_CHAIN
from app.brain.prompt_registry import get_prompt_registry
from app.core.logging import get_logger

logger = get_logger("services.contact_classifier")


class ClassificationError(Exception):
    """Raised when LLM classification fails."""
    pass


_PROMPTS = get_prompt_registry()

CONTACT_CLASSIFICATION_SYSTEM_ASSET = _PROMPTS.load(
    "contact_classifier.single_system",
    version="1.0.0",
)
CONTACT_CLASSIFICATION_USER_ASSET = _PROMPTS.load(
    "contact_classifier.single_user",
    version="1.0.0",
)
CONTACT_CLASSIFICATION_SYSTEM = CONTACT_CLASSIFICATION_SYSTEM_ASSET.body.strip()
CONTACT_CLASSIFICATION_USER = CONTACT_CLASSIFICATION_USER_ASSET.body.strip()


@dataclass
class ContactClassification:
    contact_type: str
    confidence: float
    reasoning: str


def _format_messages_for_classification(messages: list[dict]) -> str:
    """Format message dicts into readable text for the LLM."""
    lines = []
    for msg in messages[:50]:  # Limit to 50 most relevant messages
        sender = "SELLER" if msg.get("is_outgoing") else "CONTACT"
        text = msg.get("text", "")
        if not text:
            media = msg.get("media_type", "")
            text = f"[{media}]" if media else "[empty]"
        lines.append(f"[{sender}]: {text}")
    return "\n".join(lines)


async def classify_contact(
    messages: list[dict],
    display_name: str = "Unknown",
    is_group: bool = False,
) -> ContactClassification:
    """Classify a contact using Flash-Lite based on conversation messages.

    Args:
        messages: List of message dicts with keys: text, is_outgoing, media_type
        display_name: Contact's display name
        is_group: Whether this is a group chat

    Returns:
        ContactClassification with contact_type, confidence, reasoning
    """
    if not messages:
        return ContactClassification(
            contact_type="personal",
            confidence=0.1,
            reasoning="No messages to analyze, defaulting to personal (safe default)",
        )

    messages_text = _format_messages_for_classification(messages)
    prompt = CONTACT_CLASSIFICATION_USER.format(
        display_name=display_name,
        is_group=str(is_group).lower(),
        messages_text=messages_text,
    )

    try:
        result = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system=CONTACT_CLASSIFICATION_SYSTEM,
            prompt=prompt,
            operation="contact_classification",
        )

        contact_type = result.get("contact_type", "personal")
        # Validate contact_type
        valid_types = {"customer", "supplier", "personal", "work", "group"}
        if contact_type not in valid_types:
            contact_type = "personal"

        return ContactClassification(
            contact_type=contact_type,
            confidence=float(result.get("confidence", 0.5)),
            reasoning=result.get("reasoning", ""),
        )
    except Exception as exc:
        logger.exception("Flash-Lite classification failed for %s", display_name)
        raise ClassificationError(
            f"Classification failed for {display_name}"
        ) from exc


# ── Batched classification (v2) ────────────────────────────────────────────


_BATCH_SIZE = 20

BATCH_CLASSIFICATION_SYSTEM_ASSET = _PROMPTS.load(
    "contact_classifier.batch_system",
    version="1.0.0",
)
BATCH_CLASSIFICATION_USER_ASSET = _PROMPTS.load(
    "contact_classifier.batch_user",
    version="1.0.0",
)
BATCH_CLASSIFICATION_SYSTEM = BATCH_CLASSIFICATION_SYSTEM_ASSET.body.strip()
BATCH_CLASSIFICATION_USER = BATCH_CLASSIFICATION_USER_ASSET.body.strip()


def _prefilter_contacts(
    contacts: list[dict],
) -> tuple[list[tuple[int, ContactClassification]], list[tuple[int, dict]]]:
    """Rule-based pre-filter: handle obvious cases without an LLM call.

    Returns:
        pre_classified: list of (original_index, ContactClassification) for contacts
                        that were handled by rules.
        needs_llm: list of (original_index, contact_dict) for contacts that require
                   LLM classification.
    """
    pre_classified: list[tuple[int, ContactClassification]] = []
    needs_llm: list[tuple[int, dict]] = []

    for idx, contact in enumerate(contacts):
        # Rule 1: group chat
        if contact.get("is_group", False):
            pre_classified.append((
                idx,
                ContactClassification(
                    contact_type="group",
                    confidence=0.95,
                    reasoning="Group chat",
                ),
            ))
        # Rule 2: bot account
        elif contact.get("is_bot", False):
            pre_classified.append((
                idx,
                ContactClassification(
                    contact_type="work",
                    confidence=0.95,
                    reasoning="Bot account",
                ),
            ))
        # Rule 3: no messages
        elif not contact.get("messages"):
            pre_classified.append((
                idx,
                ContactClassification(
                    contact_type="unknown",
                    confidence=0.1,
                    reasoning="No messages",
                ),
            ))
        else:
            needs_llm.append((idx, contact))

    return pre_classified, needs_llm


def _format_contact_block(batch_index: int, contact: dict) -> str:
    """Format a single contact for inclusion in a batch prompt."""
    name = contact.get("display_name", "Unknown")
    messages = contact.get("messages", [])[-5:]  # Only last 5 messages per contact

    lines = [f"--- Contact {batch_index}: {name!r}"]
    if messages:
        lines.append("Recent messages:")
        for msg in messages:
            sender = "SELLER" if msg.get("is_outgoing") else "CONTACT"
            text = msg.get("text", "")
            if not text:
                media = msg.get("media_type", "")
                text = f"[{media}]" if media else "[empty]"
            lines.append(f"  [{sender}]: {text}")
    else:
        lines.append("  (no messages)")
    lines.append("---")
    return "\n".join(lines)


async def _classify_batch(
    indexed_contacts: list[tuple[int, dict]],
) -> list[tuple[int, ContactClassification]]:
    """Send one LLM call for a batch of contacts (≤20).

    Returns list of (original_index, ContactClassification).
    """
    contacts_block = "\n".join(
        _format_contact_block(batch_i, contact)
        for batch_i, (_, contact) in enumerate(indexed_contacts)
    )
    prompt = BATCH_CLASSIFICATION_USER.format(
        contacts_block=contacts_block,
        count=len(indexed_contacts),
    )

    valid_types = {"customer", "supplier", "personal", "work", "group"}

    try:
        parsed = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system=BATCH_CLASSIFICATION_SYSTEM,
            prompt=prompt,
            operation="batch_contact_classification",
        )
        # LLM may return: array, {"contacts": [...]}, or a single object — handle all
        if isinstance(parsed, list):
            raw_results: list[dict] = parsed
        elif isinstance(parsed, dict):
            if "contacts" in parsed:
                raw_results = parsed["contacts"]
            elif "results" in parsed:
                raw_results = parsed["results"]
            elif "contact_type" in parsed:
                # Single object instead of array (common with 1-contact batches)
                raw_results = [parsed]
            else:
                raw_results = []
        else:
            raw_results = []
        logger.info(
            "Batch contact classification parsed: contacts=%d raw_results=%d",
            len(indexed_contacts),
            len(raw_results),
        )

        # Build a map from batch_index -> result dict for safe lookup
        result_map: dict[int, dict] = {}
        if isinstance(raw_results, list):
            for i, item in enumerate(raw_results):
                if isinstance(item, dict):
                    # Try "index" field first, fall back to position order
                    batch_i = item.get("index", i)
                    if isinstance(batch_i, int) and 0 <= batch_i < len(indexed_contacts):
                        result_map[batch_i] = item

    except Exception:
        logger.exception("Batch LLM classification failed; falling back to unknown for batch")
        result_map = {}

    out: list[tuple[int, ContactClassification]] = []
    for batch_i, (orig_idx, contact) in enumerate(indexed_contacts):
        item = result_map.get(batch_i, {})
        contact_type = item.get("contact_type", "unknown")
        if contact_type not in valid_types:
            contact_type = "unknown"
        out.append((
            orig_idx,
            ContactClassification(
                contact_type=contact_type,
                confidence=float(item.get("confidence", 0.3)),
                reasoning=item.get("reasoning", "Batch classification fallback"),
            ),
        ))
    return out


async def classify_contacts_batch_v2(
    contacts: list[dict],
) -> list[ContactClassification]:
    """Classify contacts using batched LLM calls (20 contacts per call).

    Each contact dict must have:
    - display_name: str
    - is_group: bool
    - messages: list[dict]  — only last 5 are sent to LLM
    - is_bot: bool (optional)

    Strategy:
    1. Rule-based pre-filter handles groups, bots, and empty contacts instantly.
    2. Remaining contacts are batched 20 per LLM call.
    3. Results are merged back in original order.

    Token savings vs classify_contacts_batch():
    - Drops from ~270k-520k tokens → ~6k tokens for a typical 100-contact import.
    """
    if not contacts:
        return []

    pre_classified, needs_llm = _prefilter_contacts(contacts)

    _classify_sem = asyncio.Semaphore(3)

    async def _classify_one(
        batch: list[tuple[int, dict]], batch_start: int
    ) -> list[tuple[int, ContactClassification]]:
        async with _classify_sem:
            logger.debug(
                "Batch classifying contacts %d-%d of %d needing LLM",
                batch_start,
                batch_start + len(batch),
                len(needs_llm),
            )
            return await _classify_batch(batch)

    batches = [
        needs_llm[i : i + _BATCH_SIZE]
        for i in range(0, len(needs_llm), _BATCH_SIZE)
    ]
    batch_results_nested = await asyncio.gather(
        *[_classify_one(b, i * _BATCH_SIZE) for i, b in enumerate(batches)]
    )

    llm_results: list[tuple[int, ContactClassification]] = []
    for br in batch_results_nested:
        llm_results.extend(br)

    # Merge all results back into original order
    combined: dict[int, ContactClassification] = {}
    for orig_idx, classification in pre_classified:
        combined[orig_idx] = classification
    for orig_idx, classification in llm_results:
        combined[orig_idx] = classification

    return [combined[i] for i in range(len(contacts))]
