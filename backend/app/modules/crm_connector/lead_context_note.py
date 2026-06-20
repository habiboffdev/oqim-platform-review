"""Compose the amoCRM lead-context note (#428).

A pure, deterministic function: given the latest ``set_state`` packet (product /
price / next-action) and the turn's ``record_intelligence`` payload (objections /
owner-notes), emit a compact Uzbek note. "Write what we have, omit blanks" — only
lines with data appear; the header (the stage) is always present.

This is an internal CRM note read by the owner in amoCRM, NOT a customer-facing
message — the em-dash normalizer does not apply; it is plain deterministic text.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.crm_connector.contracts import crm_role_label

_TASK_TEXT_MAX = 400


def latest_numeric_price(state_packet: dict[str, Any] | None) -> Decimal | None:
    """The most-recent numeric ``shown_prices`` amount as a Decimal, or None.
    Used to populate ``conversation.deal_value`` for amoCRM forecasting."""
    prices = (state_packet or {}).get("shown_prices")
    if not isinstance(prices, list):
        return None
    latest: Decimal | None = None
    for price in prices:
        if not isinstance(price, dict):
            continue
        amount = price.get("amount")
        if amount is None or amount == "":
            continue
        try:
            latest = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return latest


def compose_handoff_task_text(
    state_packet: dict[str, Any] | None, intelligence: dict[str, Any] | None
) -> str:
    """Action-phrased one-liner for the amoCRM handoff task (internal CRM text,
    em-dash-free, deterministic)."""
    state_packet = state_packet or {}
    intelligence = intelligence or {}
    parts = ["Mijoz bilan bog'laning (OQIM)."]
    products = _item_names(state_packet.get("selected_items"))
    prices = _price_displays(state_packet.get("shown_prices"))
    detail = " ".join(x for x in [", ".join(products), ", ".join(prices)] if x)
    if detail:
        parts.append(detail + ".")
    next_action = (str(state_packet.get("next_best_action") or "").strip()
                   or str(intelligence.get("next_best_action") or "").strip())
    if next_action:
        parts.append(f"Keyingi qadam: {next_action}.")
    return " ".join(parts)[:_TASK_TEXT_MAX]


def compose_lead_context_note(
    *,
    stage: str,
    state_packet: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
) -> str:
    """Build the note. ``state_packet`` is the latest set_state snapshot's ``state``
    dict (or ``None``); ``intelligence`` is the turn's latest record_intelligence
    payload (or ``None``)."""
    state_packet = state_packet or {}
    intelligence = intelligence or {}

    lines = [f"OQIM ({crm_role_label(stage)}):"]

    products = _item_names(state_packet.get("selected_items"))
    if products:
        lines.append(f"Mahsulot: {', '.join(products)}")

    prices = _price_displays(state_packet.get("shown_prices"))
    if prices:
        lines.append(f"Narx: {', '.join(prices)}")

    objections = _clean_list(intelligence.get("objections"))
    if objections:
        lines.append(f"E'tiroz: {', '.join(objections)}")

    # next action: the set_state packet first (accumulated), else this turn's intel
    next_action = (str(state_packet.get("next_best_action") or "").strip()
                   or str(intelligence.get("next_best_action") or "").strip())
    if next_action:
        lines.append(f"Keyingi qadam: {next_action}")

    owner_notes = _clean_list(intelligence.get("owner_notes"))
    if owner_notes:
        lines.append(f"Izoh: {', '.join(owner_notes)}")

    return "\n".join(lines)


def _item_names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _price_displays(prices: Any) -> list[str]:
    if not isinstance(prices, list):
        return []
    out: list[str] = []
    for price in prices:
        if not isinstance(price, dict):
            continue
        display = _format_price(price.get("amount"), price.get("currency"))
        if display:
            out.append(display)
    return out


def _format_price(amount: Any, currency: Any) -> str:
    if amount is None or amount == "":
        return ""
    try:
        formatted = f"{int(amount):,}".replace(",", " ")
    except (TypeError, ValueError):
        formatted = str(amount).strip()
        if not formatted:
            return ""
    code = str(currency or "").strip()
    label = "so'm" if code.upper() == "UZS" else code
    return f"{formatted} {label}".strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
