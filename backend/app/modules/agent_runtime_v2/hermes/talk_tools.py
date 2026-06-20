from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.modules.agent_runtime_v2.hermes.tool_context import current_tool_context
from app.modules.agent_talking.contracts import TalkAction, TalkActionKind
from app.modules.agent_talking.output_normalize import normalize_outgoing_text


def _response(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _ctx_and_bundle():
    ctx = current_tool_context.get()
    if ctx is None:
        return None, None
    return ctx, getattr(ctx, "talk_bundle", None)


# Customer-visible text-bubble kinds. The same text twice in one turn is never
# valid output (a forced talk iteration re-emitting its last line); guard it.
_DEDUP_TEXT_KINDS = (TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG)


def _queued_bubble_texts(bundle) -> set[str]:
    """Normalized texts of customer message bubbles already queued this turn."""
    return {
        action.text
        for action in bundle.actions
        if action.kind in _DEDUP_TEXT_KINDS and action.text
    }


def _append_action(action: TalkAction) -> str:
    _ctx, bundle = _ctx_and_bundle()
    if bundle is None:
        return _response(status="blocked", reason="no_talk_bundle")
    if (
        action.kind in _DEDUP_TEXT_KINDS
        and action.text
        and action.text in _queued_bubble_texts(bundle)
    ):
        return _response(status="skipped", reason="duplicate_bubble")
    bundle.actions.append(action)
    return _response(
        status="queued",
        action_kind=action.kind.value,
        action_index=len(bundle.actions) - 1,
    )


def _validation_error(exc: ValidationError) -> str:
    return _response(status="blocked", reason="invalid_action", errors=exc.errors())


def talk_send_msg(args: dict, **_kw) -> str:
    try:
        action = TalkAction(
            kind=TalkActionKind.SEND_MSG,
            text=normalize_outgoing_text(str(args.get("text") or "").strip()),
            requires_scope="telegram.send_message",
            risk_level="high",
        )
    except ValidationError as exc:
        return _validation_error(exc)
    return _append_action(action)


def talk_send_msgs(args: dict, **_kw) -> str:
    ctx, bundle = _ctx_and_bundle()
    if bundle is None:
        return _response(status="blocked", reason="no_talk_bundle")
    raw_bubbles = args.get("bubbles")
    if not isinstance(raw_bubbles, list):
        return _response(status="blocked", reason="invalid_bubbles")
    first_reply_ref = (
        str(
            args.get("reply_message_id")
            or args.get("target_message_ref")
            or args.get("reply_to_message_ref")
            or ""
        ).strip()
        or None
    )
    bubbles = [
        normalized
        for index, item in enumerate(raw_bubbles)
        if (normalized := _normalize_bubble_item(item, first_reply_ref if index == 0 else None))
        is not None
    ]
    if not bubbles:
        return _response(status="blocked", reason="empty_bubbles")

    policy = bundle.talking_policy_snapshot

    # Enforce bubble mechanics host-side: the policy cap was guidance-only
    # and the model shipped a ~280-char two-beat bubble with a blank line in
    # the live pilot (2026-06-10). Blank lines are beat boundaries; over-cap
    # beats split at sentence boundaries. The model writes, OQIM formats.
    cap = max(1, int(policy.max_chars_per_bubble or 220))
    expanded: list[tuple[str, str | None]] = []
    for text, reply_ref in bubbles:
        for part_index, part in enumerate(_split_bubble_text(text, cap)):
            expanded.append((part, reply_ref if part_index == 0 else None))
    bubbles = expanded

    # Drop bubbles whose exact (normalized) text is already queued this turn,
    # or repeated earlier in this same batch, BEFORE budget accounting so a
    # repeat never clips away genuinely-new content. A later loop iteration
    # forced to talk (mode=ANY) with nothing new re-emits its last line; two
    # identical customer bubbles in one turn are never valid output (live
    # 2026-06-13 run 120: msgs 399 == 400). Reactions/media carry no msg text.
    seen = _queued_bubble_texts(bundle)
    deduped: list[tuple[str, str | None]] = []
    for text, reply_ref in bubbles:
        norm = normalize_outgoing_text(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append((norm, reply_ref))
    if not deduped:
        return _response(status="skipped", reason="duplicate_bubble", queued_count=0)
    bubbles = deduped

    remaining = policy.max_bubbles_per_turn - len(bundle.actions)
    if len(bubbles) > remaining:
        if remaining <= 0:
            if ctx is not None:
                ctx.authority_warnings.append("talking_policy_max_bubbles_exceeded")
            return _response(
                status="blocked",
                reason="max_bubbles_exceeded",
                remaining_bubbles=0,
            )
        # The split may have grown the count past the budget; keep the first
        # beats rather than refusing the whole reply (trailing macro-tails
        # are the least important content).
        if ctx is not None:
            ctx.authority_warnings.append("talking_policy_bubbles_clipped")
        bubbles = bubbles[:remaining]

    actions: list[TalkAction] = []
    try:
        for text, reply_message_id in bubbles:
            actions.append(
                TalkAction(
                    kind=TalkActionKind.REPLY_TO_MSG if reply_message_id else TalkActionKind.SEND_MSG,
                    text=normalize_outgoing_text(text),
                    target_message_ref=reply_message_id,
                    requires_scope="telegram.send_message",
                    risk_level="high",
                )
            )
    except ValidationError as exc:
        return _validation_error(exc)
    start = len(bundle.actions)
    bundle.actions.extend(actions)
    return _response(
        status="queued",
        action_kind="send_msg_batch",
        action_indexes=list(range(start, start + len(actions))),
        queued_count=len(actions),
    )


def _split_bubble_text(text: str, cap: int) -> list[str]:
    """Split one requested bubble into channel-sized parts.

    Blank lines always split (one bubble = one beat); beats over the cap
    split at sentence boundaries; a single sentence over the cap wraps at a
    word boundary as a last resort.
    """
    beats = [beat.strip() for beat in text.split("\n\n") if beat.strip()]
    parts: list[str] = []
    for beat in beats:
        if len(beat) <= cap:
            parts.append(beat)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?…])\s+", beat):
            candidate = f"{current} {sentence}".strip()
            if len(candidate) <= cap or not current:
                current = candidate
            else:
                parts.append(current)
                current = sentence
        if current:
            parts.append(current)
    wrapped: list[str] = []
    for part in parts:
        while len(part) > cap:
            cut = part.rfind(" ", 0, cap + 1)
            cut = cut if cut > 0 else cap
            wrapped.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            wrapped.append(part)
    return wrapped or ([text.strip()] if text.strip() else [])


def _normalize_bubble_item(item: object, default_reply_ref: str | None) -> tuple[str, str | None] | None:
    if isinstance(item, dict):
        text = str(item.get("text") or item.get("message") or "").strip()
        reply_ref = (
            str(
                item.get("reply_message_id")
                or item.get("target_message_ref")
                or item.get("reply_to_message_ref")
                or ""
            ).strip()
            or None
        )
    else:
        text = str(item).strip()
        reply_ref = default_reply_ref
    if not text:
        return None
    return text, reply_ref


def talk_send_media(args: dict, **_kw) -> str:
    try:
        action = TalkAction(
            kind=TalkActionKind.SEND_MEDIA,
            text=(str(args.get("caption") or "").strip() or None),
            media_ref=str(args.get("media_ref") or "").strip(),
            requires_scope="telegram.send_message",
            risk_level="high",
        )
    except ValidationError as exc:
        return _validation_error(exc)
    return _append_action(action)


def talk_send_reaction(args: dict, **_kw) -> str:
    _ctx, bundle = _ctx_and_bundle()
    target_message_ref = (
        str(args.get("target_message_ref") or "").strip()
        or getattr(bundle, "trigger_ref", None)
        or None
    )
    try:
        action = TalkAction(
            kind=TalkActionKind.SEND_REACTION,
            reaction=str(args.get("reaction") or "").strip(),
            target_message_ref=target_message_ref,
            requires_scope="telegram.send_reaction",
            risk_level="medium",
        )
    except ValidationError as exc:
        return _validation_error(exc)
    return _append_action(action)


def talk_reply_to_msg(args: dict, **_kw) -> str:
    try:
        action = TalkAction(
            kind=TalkActionKind.REPLY_TO_MSG,
            text=normalize_outgoing_text(str(args.get("text") or "").strip()),
            target_message_ref=(str(args.get("target_message_ref") or "").strip() or None),
            requires_scope="telegram.send_message",
            risk_level="high",
        )
    except ValidationError as exc:
        return _validation_error(exc)
    return _append_action(action)


def talk_delete_message(args: dict, **_kw) -> str:
    ctx, bundle = _ctx_and_bundle()
    policy = getattr(bundle, "talking_policy_snapshot", None)
    if policy is None or not policy.allow_delete:
        if ctx is not None:
            ctx.authority_warnings.append("talking_policy_delete_disabled")
        return _response(status="blocked", reason="talking_policy_delete_disabled")
    try:
        action = TalkAction(
            kind=TalkActionKind.DELETE_MESSAGE,
            target_message_ref=str(args.get("target_message_ref") or "").strip(),
            requires_scope="telegram.delete_message",
            risk_level="high",
        )
    except ValidationError as exc:
        return _validation_error(exc)
    return _append_action(action)


_SEND_MSG_SCHEMA = {
    "name": "talk.send_msg",
    "description": (
        "Queue one visible customer-facing text bubble in OQIM's talk bundle. "
        "Use for a single short Telegram beat. This never delivers directly; "
        "OQIM handles policy, pacing, audit, and send."
    ),
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}

_SEND_MSGS_SCHEMA = {
    "name": "talk.send_msgs",
    "description": (
        "Queue one, two, or three separate customer-facing Telegram text "
        "bubbles in OQIM's talk bundle. Each bubble item has text and optional "
        "reply_message_id when that exact bubble should be anchored as a "
        "Telegram reply. Keep each bubble to one clear idea; how long bubbles "
        "run follows the agent's voice and AGENT.md. Do not pack multiple "
        "beats into one text with blank lines."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bubbles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "reply_message_id": {"type": "string"},
                    },
                    "required": ["text"],
                },
                "minItems": 1,
                "maxItems": 3,
            },
            "reply_message_id": {"type": "string"},
            "target_message_ref": {"type": "string"},
        },
        "required": ["bubbles"],
    },
}

_SEND_MEDIA_SCHEMA = {
    "name": "talk.send_media",
    "description": (
        "Queue approved media in OQIM's talk bundle. Use only when media_ref is "
        "already grounded/approved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "media_ref": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": ["media_ref"],
    },
}

_SEND_REACTION_SCHEMA = {
    "name": "talk.send_reaction",
    "description": (
        "Queue a Telegram reaction. Reaction ALONE is the complete reply only "
        "for pure social acknowledgements: 'ok', 'rahmat', 'zo'r', thanks, or "
        "an emoji-only message. When the customer PROVIDES something — a phone "
        "number, contact, payment proof, or requested detail — react AND send "
        "one short bubble confirming receipt and what happens next. Never "
        "answer a customer's shared contact or deliverable with an emoji "
        "alone. If target_message_ref is omitted, OQIM reacts to the current "
        "customer trigger message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reaction": {"type": "string"},
            "target_message_ref": {"type": "string"},
        },
        "required": ["reaction"],
    },
}

_REPLY_TO_MSG_SCHEMA = {
    "name": "talk.reply_to_msg",
    "description": "Queue a text bubble that replies to a specific inbound message.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "target_message_ref": {"type": "string"},
        },
        "required": ["text", "target_message_ref"],
    },
}

_DELETE_MESSAGE_SCHEMA = {
    "name": "talk.delete_message",
    "description": "Request deletion of a message. Disabled by default by OQIM policy.",
    "parameters": {
        "type": "object",
        "properties": {"target_message_ref": {"type": "string"}},
        "required": ["target_message_ref"],
    },
}

def register_talk_tools() -> None:
    from tools.registry import registry

    registry.register(
        name="talk.send_msg",
        toolset="oqim",
        schema=_SEND_MSG_SCHEMA,
        handler=lambda args, **kw: talk_send_msg(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
    registry.register(
        name="talk.send_msgs",
        toolset="oqim",
        schema=_SEND_MSGS_SCHEMA,
        handler=lambda args, **kw: talk_send_msgs(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
    registry.register(
        name="talk.send_media",
        toolset="oqim",
        schema=_SEND_MEDIA_SCHEMA,
        handler=lambda args, **kw: talk_send_media(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
    registry.register(
        name="talk.send_reaction",
        toolset="oqim",
        schema=_SEND_REACTION_SCHEMA,
        handler=lambda args, **kw: talk_send_reaction(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
    registry.register(
        name="talk.reply_to_msg",
        toolset="oqim",
        schema=_REPLY_TO_MSG_SCHEMA,
        handler=lambda args, **kw: talk_reply_to_msg(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
    registry.register(
        name="talk.delete_message",
        toolset="oqim",
        schema=_DELETE_MESSAGE_SCHEMA,
        handler=lambda args, **kw: talk_delete_message(args, **kw),
        check_fn=lambda: True,
        requires_env=[],
        override=True,
    )
