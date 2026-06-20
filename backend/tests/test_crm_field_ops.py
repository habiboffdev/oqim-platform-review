"""S4 pure field/tag op helpers: coerce + allowlist + opt-in resolution.

These are the Logical-Keys-above-the-seam helpers (no I/O, no provider calls)
plus the channel_config -> AgentConfig resolvers.
"""
from __future__ import annotations

from app.modules.crm_connector.field_ops import coerce_field_value, resolve_field_ops


def test_coerce_numeric_text_checkbox():
    assert coerce_field_value({"type": "numeric"}, "5000000") == 5000000
    assert coerce_field_value({"type": "numeric"}, "abc") is None
    assert coerce_field_value({"type": "text"}, 42) == "42"
    assert coerce_field_value({"type": "checkbox"}, True) is True


def test_coerce_select_uses_enum_allowlist():
    f = {"type": "select", "enum_map": {"Instagram": 9001, "Telegram": 9002}}
    assert coerce_field_value(f, "Instagram") == 9001
    assert coerce_field_value(f, "Carrier pigeon") is None  # not in allowlist


def test_resolve_field_ops_only_writes_blessed_fields():
    cfg = {
        "budget": {"field_id": 600123, "type": "numeric", "write": True},
        "secret": {"field_id": 600999, "type": "numeric", "write": False},
    }
    ops = resolve_field_ops(
        cfg,
        [{"key": "budget", "value": "5000000"},
         {"key": "secret", "value": "1"},
         {"key": "unknown", "value": "x"}],
    )
    assert ops == [{"kind": "custom_field", "entity": "lead", "field_id": "600123", "value": 5000000, "type": "numeric"}]


def test_resolve_field_ops_carries_contact_entity():
    from app.modules.crm_connector.field_ops import resolve_field_ops
    cfg = {"budget": {"field_id": 740937, "type": "text", "write": True, "entity": "contact"}}
    ops = resolve_field_ops(cfg, [{"key": "budget", "value": "5 mln"}])
    assert ops == [{"kind": "custom_field", "entity": "contact", "field_id": "740937", "value": "5 mln", "type": "text"}]


def test_resolvers_absent_return_none():
    from app.modules.agent_runtime_v2.config_loader import (
        resolve_crm_dnc,
        resolve_crm_fields,
        resolve_crm_tags,
    )
    assert resolve_crm_fields({}) is None
    assert resolve_crm_tags({"crm": {"tags": {}}}) is None
    assert resolve_crm_dnc({"crm": {}}) is None
    assert resolve_crm_fields({"crm": {"fields": {"budget": {"field_id": 1}}}}) == {
        "budget": {"field_id": 1}
    }


# --- (S4c Task 1) tolerant resolution: key-or-label, case-insensitive enums ---


def test_coerce_select_is_case_insensitive():
    f = {"type": "select", "enum_map": {"Instagram": 9001, "Telegram": 9002}}
    assert coerce_field_value(f, "instagram") == 9001   # lowercased
    assert coerce_field_value(f, "TELEGRAM") == 9002     # uppercased
    assert coerce_field_value(f, "Instagram") == 9001    # exact still works
    assert coerce_field_value(f, "Carrier pigeon") is None


def test_resolve_field_ops_matches_logical_key_case_insensitively():
    cfg = {"manba": {"field_id": 740941, "type": "select", "write": True,
                     "entity": "contact", "label": "manba",
                     "enum_map": {"Instagram": 1308885}}}
    # the model emitted the key capitalized + the value lowercased
    assert resolve_field_ops(cfg, [{"key": "Manba", "value": "instagram"}]) == [
        {"kind": "custom_field", "entity": "contact",
         "field_id": "740941", "value": 1308885, "type": "select"}
    ]


def test_resolve_field_ops_matches_by_label_when_key_differs():
    # logical key 'src', human label 'Manba' — the model emitted the label.
    cfg = {"src": {"field_id": 740941, "type": "text", "write": True, "label": "Manba"}}
    assert resolve_field_ops(cfg, [{"key": "manba", "value": "Instagram"}]) == [
        {"kind": "custom_field", "entity": "lead",
         "field_id": "740941", "value": "Instagram", "type": "text"}
    ]


# --- (S4c Task 2) the enumerated records-agent field directive ----------------


def test_describe_writable_fields_enumerates_with_enums():
    from app.modules.crm_connector.field_ops import describe_writable_fields
    cfg = {
        "budjet": {"field_id": 1, "type": "text", "write": True, "label": "Budjet"},
        "manba": {"field_id": 2, "type": "select", "write": True, "label": "Manba",
                  "enum_map": {"Instagram": 10, "Telegram": 11}},
        "ro": {"field_id": 3, "type": "text", "write": False, "label": "RO"},
    }
    out = describe_writable_fields(cfg)
    assert "custom_fields" in out
    assert "budjet" in out and "manba" in out
    assert "Instagram" in out and "Telegram" in out
    assert "`ro`" not in out  # not writable -> excluded


def test_describe_writable_fields_none_when_no_writable():
    from app.modules.crm_connector.field_ops import describe_writable_fields
    assert describe_writable_fields({}) is None
    assert describe_writable_fields({"x": {"write": False, "field_id": 1}}) is None


# --- (typed serialization) the op carries the field type so the adapter can
# pick the amoCRM slot (select -> enum_id). ---


def test_resolve_field_ops_stamps_field_type_on_op():
    cfg = {
        "manba": {"field_id": 740941, "type": "select", "write": True,
                  "label": "Manba", "enum_map": {"Instagram": 1308885}},
        "budjet": {"field_id": 740937, "type": "text", "write": True, "label": "Budjet"},
    }
    ops = resolve_field_ops(
        cfg, [{"key": "manba", "value": "Instagram"}, {"key": "budjet", "value": "5 mln"}]
    )
    assert ops[0]["type"] == "select" and ops[0]["value"] == 1308885
    assert ops[1]["type"] == "text" and ops[1]["value"] == "5 mln"


# --- (S4c-fix) the records agent emits objects WITHOUT a {"key","value"} shape:
# live trace 2026-06-17 = 2 entries, each dict, get("key")=None. Accept the
# shapes models actually produce (single-pair map + name/field/label aliases).


def test_resolve_field_ops_accepts_single_pair_map_shape():
    # the model emitted [{<fieldkey>: value}] instead of [{"key","value"}]
    cfg = {
        "budjet": {"field_id": 740937, "type": "text", "write": True, "entity": "contact"},
        "manba": {"field_id": 740941, "type": "select", "write": True, "entity": "contact",
                  "enum_map": {"Instagram": 1308885}},
    }
    ops = resolve_field_ops(cfg, [{"budjet": "5 mln"}, {"manba": "Instagram"}])
    assert ops == [
        {"kind": "custom_field", "entity": "contact", "field_id": "740937", "value": "5 mln", "type": "text"},
        {"kind": "custom_field", "entity": "contact", "field_id": "740941", "value": 1308885, "type": "select"},
    ]


def test_resolve_field_ops_accepts_name_value_alias_shape():
    cfg = {"budjet": {"field_id": 740937, "type": "text", "write": True}}
    assert resolve_field_ops(cfg, [{"name": "budjet", "value": "5 mln"}]) == [
        {"kind": "custom_field", "entity": "lead", "field_id": "740937", "value": "5 mln", "type": "text"}
    ]
    # 'field' alias + label match, case-insensitive
    cfg2 = {"src": {"field_id": 740941, "type": "text", "write": True, "label": "Manba"}}
    assert resolve_field_ops(cfg2, [{"field": "manba", "value": "Instagram"}]) == [
        {"kind": "custom_field", "entity": "lead", "field_id": "740941", "value": "Instagram", "type": "text"}
    ]


def test_resolve_field_ops_accepts_multi_field_single_object():
    # the model put all fields in ONE object {<k1>: v1, <k2>: v2}
    cfg = {
        "budjet": {"field_id": 740937, "type": "text", "write": True},
        "manba": {"field_id": 740941, "type": "select", "write": True,
                  "enum_map": {"Instagram": 1308885}},
    }
    ops = resolve_field_ops(cfg, [{"budjet": "5 mln", "manba": "Instagram"}])
    assert {o["field_id"] for o in ops} == {"740937", "740941"}


def test_resolve_field_ops_canonical_shape_still_works():
    # regression: the directive-asked {"key","value"} shape must keep resolving
    cfg = {"budjet": {"field_id": 740937, "type": "text", "write": True}}
    assert resolve_field_ops(cfg, [{"key": "budjet", "value": "5 mln"}]) == [
        {"kind": "custom_field", "entity": "lead", "field_id": "740937", "value": "5 mln", "type": "text"}
    ]
