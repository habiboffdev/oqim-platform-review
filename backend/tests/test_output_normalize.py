"""Deterministic outgoing-text form rules (v1: em-dash -> comma)."""

from __future__ import annotations

from app.modules.agent_talking.output_normalize import normalize_outgoing_text


def test_em_dash_with_spaces_becomes_comma():
    assert normalize_outgoing_text("Maqsad — HR sohasiga") == "Maqsad, HR sohasiga"


def test_em_dash_without_spaces_becomes_comma():
    assert normalize_outgoing_text("a—b") == "a, b"


def test_multiple_em_dashes_all_normalized():
    out = normalize_outgoing_text("bir — ikki — uch")
    assert "—" not in out
    assert out == "bir, ikki, uch"


def test_number_hyphens_and_en_dash_untouched():
    assert normalize_outgoing_text("5-7 mln, 10-15-20 mln+") == "5-7 mln, 10-15-20 mln+"
    assert normalize_outgoing_text("2013–2014") == "2013–2014"  # en-dash kept


def test_clean_text_is_a_no_op():
    s = "Narxi 9 790 000 so'm, bo'lib to'lash bor."
    assert normalize_outgoing_text(s) == s


def test_idempotent():
    once = normalize_outgoing_text("Maqsad — HR — kasb")
    assert normalize_outgoing_text(once) == once


def test_empty_and_non_string_safe():
    assert normalize_outgoing_text("") == ""
    assert normalize_outgoing_text("   ") == "   "
    assert normalize_outgoing_text(None) == ""  # type: ignore[arg-type]


def test_comma_before_em_dash_does_not_double():
    assert normalize_outgoing_text("keldingiz, — lekin boshlaymiz") == "keldingiz, lekin boshlaymiz"


def test_leading_em_dash_has_no_leading_comma():
    assert normalize_outgoing_text("— Birinchi qadam") == "Birinchi qadam"
