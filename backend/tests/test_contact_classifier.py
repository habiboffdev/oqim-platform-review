"""Tests for contact classifier — batched v2 with rule-based pre-filter.

Covers:
- _prefilter_contacts: groups, bots, empty contacts handled without LLM
- classify_contacts_batch_v2: batches 20-per-call, maintains original order
- LLM fallback: malformed JSON falls back to "unknown"
- Order preservation with mixed pre-filtered and LLM-classified contacts
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.contact_classifier import (
    _prefilter_contacts,
    classify_contacts_batch_v2,
)


def _make_llm_response(text: str) -> dict:
    """Return parsed dict (generate_structured_json returns dict, not LLMResponse)."""
    import json
    return json.loads(text)


# ── _prefilter_contacts ────────────────────────────────────────────────────


def test_prefilter_group_chat():
    """Groups are classified by rule, not LLM."""
    contacts = [{"display_name": "Group Chat", "is_group": True, "messages": [{"text": "hi", "is_outgoing": False}]}]
    pre_classified, needs_llm = _prefilter_contacts(contacts)

    assert len(pre_classified) == 1
    assert len(needs_llm) == 0
    orig_idx, result = pre_classified[0]
    assert orig_idx == 0
    assert result.contact_type == "group"
    assert result.confidence >= 0.9


def test_prefilter_bot_account():
    """Bot accounts are classified by rule, not LLM."""
    contacts = [{"display_name": "Sms Bot", "is_group": False, "is_bot": True, "messages": []}]
    pre_classified, needs_llm = _prefilter_contacts(contacts)

    assert len(pre_classified) == 1
    assert len(needs_llm) == 0
    orig_idx, result = pre_classified[0]
    assert result.contact_type == "work"
    assert result.confidence >= 0.9


def test_prefilter_empty_messages():
    """Contacts with no messages are classified as unknown by rule, not LLM."""
    contacts = [{"display_name": "Mystery Person", "is_group": False, "messages": []}]
    pre_classified, needs_llm = _prefilter_contacts(contacts)

    assert len(pre_classified) == 1
    assert len(needs_llm) == 0
    orig_idx, result = pre_classified[0]
    assert result.contact_type == "unknown"
    assert result.confidence <= 0.2


def test_prefilter_contact_with_messages_needs_llm():
    """Contacts with messages pass through to LLM."""
    contacts = [{"display_name": "Alisher", "is_group": False, "messages": [{"text": "salom", "is_outgoing": False}]}]
    pre_classified, needs_llm = _prefilter_contacts(contacts)

    assert len(pre_classified) == 0
    assert len(needs_llm) == 1
    orig_idx, contact = needs_llm[0]
    assert orig_idx == 0
    assert contact["display_name"] == "Alisher"


def test_prefilter_mixed_contacts():
    """Mixed contacts: groups + bots pre-filtered, normal contacts go to LLM."""
    contacts = [
        {"display_name": "Alisher", "is_group": False, "messages": [{"text": "iPhone bormi?", "is_outgoing": False}]},
        {"display_name": "Family Group", "is_group": True, "messages": []},
        {"display_name": "SpamBot", "is_group": False, "is_bot": True, "messages": []},
        {"display_name": "Ota", "is_group": False, "messages": [{"text": "salom", "is_outgoing": False}]},
        {"display_name": "Ghost", "is_group": False, "messages": []},
    ]
    pre_classified, needs_llm = _prefilter_contacts(contacts)

    # Family Group (idx 1), SpamBot (idx 2), Ghost (idx 4) are pre-filtered
    pre_indices = {idx for idx, _ in pre_classified}
    assert pre_indices == {1, 2, 4}

    # Alisher (idx 0) and Ota (idx 3) need LLM
    llm_indices = {idx for idx, _ in needs_llm}
    assert llm_indices == {0, 3}


# ── classify_contacts_batch_v2 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_v2_two_contacts_one_llm_call():
    """Two contacts with messages should result in exactly 1 LLM call."""
    contacts = [
        {"display_name": "Alisher", "is_group": False, "messages": [{"text": "iPhone bormi?", "is_outgoing": False}]},
        {"display_name": "Bobur", "is_group": False, "messages": [{"text": "Salom do'stim", "is_outgoing": False}]},
    ]
    llm_response = _make_llm_response(json.dumps([
        {"index": 0, "contact_type": "customer", "confidence": 0.9, "reasoning": "Asks about product"},
        {"index": 1, "contact_type": "personal", "confidence": 0.8, "reasoning": "Personal greeting"},
    ]))

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(return_value=llm_response),
    ) as mock_llm:
        results = await classify_contacts_batch_v2(contacts)

    mock_llm.assert_called_once()  # NOT called twice
    assert len(results) == 2
    assert results[0].contact_type == "customer"
    assert results[1].contact_type == "personal"


@pytest.mark.asyncio
async def test_batch_v2_groups_dont_trigger_llm():
    """Group contacts are pre-filtered — no LLM call needed."""
    contacts = [
        {"display_name": "Savdo Guruh", "is_group": True, "messages": []},
        {"display_name": "Yetkazib berish", "is_group": True, "messages": [{"text": "ok", "is_outgoing": False}]},
    ]

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(),
    ) as mock_llm:
        results = await classify_contacts_batch_v2(contacts)

    mock_llm.assert_not_called()
    assert len(results) == 2
    assert all(r.contact_type == "group" for r in results)


@pytest.mark.asyncio
async def test_batch_v2_empty_contacts_dont_trigger_llm():
    """Empty contact lists pre-filtered — no LLM call."""
    contacts = [
        {"display_name": "Ghost1", "is_group": False, "messages": []},
        {"display_name": "Ghost2", "is_group": False, "messages": []},
    ]

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(),
    ) as mock_llm:
        results = await classify_contacts_batch_v2(contacts)

    mock_llm.assert_not_called()
    assert len(results) == 2
    assert all(r.contact_type == "unknown" for r in results)


@pytest.mark.asyncio
async def test_batch_v2_preserves_original_order():
    """Results must appear in the same order as the input contacts."""
    contacts = [
        {"display_name": "Contact0", "is_group": False, "messages": [{"text": "msg", "is_outgoing": False}]},
        {"display_name": "Group1", "is_group": True, "messages": []},
        {"display_name": "Contact2", "is_group": False, "messages": [{"text": "narx?", "is_outgoing": False}]},
        {"display_name": "Empty3", "is_group": False, "messages": []},
        {"display_name": "Contact4", "is_group": False, "messages": [{"text": "salom", "is_outgoing": False}]},
    ]
    # LLM handles indices 0, 2, 4 (the ones with messages)
    llm_response = _make_llm_response(json.dumps([
        {"index": 0, "contact_type": "personal", "confidence": 0.7, "reasoning": "Friendly chat"},
        {"index": 1, "contact_type": "customer", "confidence": 0.9, "reasoning": "Price inquiry"},
        {"index": 2, "contact_type": "work", "confidence": 0.6, "reasoning": "Work"},
    ]))

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(return_value=llm_response),
    ):
        results = await classify_contacts_batch_v2(contacts)

    assert len(results) == 5
    # Index 1 is a group (pre-filtered)
    assert results[1].contact_type == "group"
    # Index 3 is empty (pre-filtered as unknown)
    assert results[3].contact_type == "unknown"
    # LLM-classified ones
    assert results[0].contact_type == "personal"
    assert results[2].contact_type == "customer"
    assert results[4].contact_type == "work"


@pytest.mark.asyncio
async def test_batch_v2_empty_input():
    """Empty contact list returns empty result without any LLM call."""
    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(),
    ) as mock_llm:
        results = await classify_contacts_batch_v2([])

    mock_llm.assert_not_called()
    assert results == []


@pytest.mark.asyncio
async def test_batch_v2_malformed_json_falls_back_to_unknown():
    """When LLM returns unparseable JSON, contacts fall back to 'unknown'."""
    contacts = [
        {"display_name": "Alisher", "is_group": False, "messages": [{"text": "hi", "is_outgoing": False}]},
    ]
    bad_response = {}  # generate_structured_json returns {} for malformed JSON

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(return_value=bad_response),
    ):
        results = await classify_contacts_batch_v2(contacts)

    assert len(results) == 1
    assert results[0].contact_type == "unknown"
    assert results[0].confidence == 0.3


@pytest.mark.asyncio
async def test_batch_v2_llm_failure_falls_back_to_unknown():
    """When LLM raises an exception, contacts fall back to 'unknown' without crashing."""
    contacts = [
        {"display_name": "Alisher", "is_group": False, "messages": [{"text": "hi", "is_outgoing": False}]},
    ]

    with patch(
        "app.services.contact_classifier.generate_structured_json",
        AsyncMock(side_effect=Exception("LLM down")),
    ):
        results = await classify_contacts_batch_v2(contacts)

    assert len(results) == 1
    assert results[0].contact_type == "unknown"


@pytest.mark.asyncio
async def test_batch_v2_uses_only_last_5_messages():
    """Only the last 5 messages per contact are sent to the LLM prompt."""
    messages = [{"text": f"msg{i}", "is_outgoing": i % 2 == 0} for i in range(20)]
    contacts = [{"display_name": "Verbose", "is_group": False, "messages": messages}]

    llm_response = _make_llm_response(json.dumps([
        {"index": 0, "contact_type": "customer", "confidence": 0.8, "reasoning": "ok"},
    ]))

    mock_llm = AsyncMock(return_value=llm_response)

    with patch("app.services.contact_classifier.generate_structured_json", mock_llm):
        await classify_contacts_batch_v2(contacts)

    # The prompt should contain only the last 5 messages (msg15 through msg19)
    prompt_text = mock_llm.call_args.kwargs["prompt"]
    # First 15 messages should NOT appear
    assert "msg0" not in prompt_text
    assert "msg14" not in prompt_text
    # Last 5 messages SHOULD appear
    assert "msg15" in prompt_text
    assert "msg19" in prompt_text
