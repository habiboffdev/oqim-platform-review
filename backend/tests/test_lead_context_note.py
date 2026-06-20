"""compose_lead_context_note — pure Uzbek CRM note composer (#428).

Omit-blanks: only lines with data appear; the header is always present. No I/O.
"""
from __future__ import annotations

from app.modules.crm_connector.lead_context_note import compose_lead_context_note


def test_full_data_all_lines_in_order():
    note = compose_lead_context_note(
        stage="qualified",
        state_packet={
            "selected_items": [{"title": "HR kursi"}],
            "shown_prices": [{"amount": 4900000, "currency": "UZS"}],
            "next_best_action": "to'lov havolasini yuborish",
        },
        intelligence={
            "objections": ["narx qimmat"],
            "owner_notes": ["ertaga qayta bog'lanish"],
            "next_best_action": "",
        },
    )
    assert note.splitlines() == [
        "OQIM (Malakali):",
        "Mahsulot: HR kursi",
        "Narx: 4 900 000 so'm",
        "E'tiroz: narx qimmat",
        "Keyingi qadam: to'lov havolasini yuborish",
        "Izoh: ertaga qayta bog'lanish",
    ]


def test_partial_data_omits_blank_lines():
    note = compose_lead_context_note(
        stage="negotiation",
        state_packet={"selected_items": [], "shown_prices": []},
        intelligence={"objections": ["sekin"], "owner_notes": [], "next_best_action": ""},
    )
    assert note.splitlines() == ["OQIM (Muzokara):", "E'tiroz: sekin"]


def test_header_only_when_no_data():
    assert compose_lead_context_note(stage="qualified", state_packet={}, intelligence={}) == (
        "OQIM (Malakali):"
    )


def test_none_inputs_degrade_to_header_only():
    assert compose_lead_context_note(stage="new", state_packet=None, intelligence=None) == (
        "OQIM (Yangi):"
    )


def test_next_action_prefers_state_packet_over_intelligence():
    note = compose_lead_context_note(
        stage="qualified",
        state_packet={"next_best_action": "to'lov"},
        intelligence={"next_best_action": "qo'ng'iroq"},
    )
    assert "Keyingi qadam: to'lov" in note
    assert "qo'ng'iroq" not in note


def test_next_action_falls_back_to_intelligence():
    note = compose_lead_context_note(
        stage="qualified", state_packet={}, intelligence={"next_best_action": "qo'ng'iroq"}
    )
    assert "Keyingi qadam: qo'ng'iroq" in note


def test_lists_join_with_comma_and_support_title_or_name():
    note = compose_lead_context_note(
        stage="qualified",
        state_packet={"selected_items": [{"title": "A"}, {"name": "B"}]},
        intelligence={"objections": ["x", "y"]},
    )
    assert "Mahsulot: A, B" in note
    assert "E'tiroz: x, y" in note
