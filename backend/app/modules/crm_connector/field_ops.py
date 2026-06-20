"""Pure helpers for S4 field/tag writes: coerce logical values to provider-native
values and resolve logical field keys -> queued ops. No I/O, no provider calls —
the Logical-Keys-above-the-seam boundary lives here."""
from __future__ import annotations

from typing import Any


def coerce_field_value(field_cfg: dict, value: Any) -> Any | None:
    """Coerce a logical value to the provider-native value for ``field_cfg['type']``.
    Returns None when the value is unusable (the caller drops the op):
    numeric->int (else None), checkbox->bool, select->enum_id via the enum_map
    allowlist (unknown label -> None), text/other->str."""
    ftype = (field_cfg.get("type") or "text").strip()
    if value is None:
        return None
    if ftype == "numeric":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    if ftype == "checkbox":
        return bool(value)
    if ftype == "select":
        enum_map = field_cfg.get("enum_map") or {}
        if value in enum_map:  # exact label wins
            return enum_map[value]
        folded = str(value).strip().casefold()
        for label, enum_id in enum_map.items():
            if str(label).strip().casefold() == folded:
                return enum_id
        return None  # not in the allowlist
    text = str(value).strip()
    return text or None


def _field_lookup(fields_cfg: dict) -> dict[str, tuple[str, dict]]:
    """Normalized {casefolded key|label -> (logical_key, cfg)} for tolerant
    matching: the model may emit the logical key OR the human label, in any case.
    Exact logical keys take precedence over labels on a casefold collision.
    Never fuzzy/substring — only exact-after-casefold, so it cannot mis-route."""
    by_label: dict[str, tuple[str, dict]] = {}
    by_key: dict[str, tuple[str, dict]] = {}
    for key, cfg in (fields_cfg or {}).items():
        if not isinstance(cfg, dict):
            continue
        by_key[str(key).strip().casefold()] = (key, cfg)
        label = cfg.get("label")
        if label:
            by_label.setdefault(str(label).strip().casefold(), (key, cfg))
    return {**by_label, **by_key}  # keys override labels on collision


# Field/value key aliases the model emits instead of the directive's {key,value}.
_KEY_FIELDS = ("key", "name", "field", "field_key", "label")
_VALUE_FIELDS = ("value", "val")
_RESERVED = set(_KEY_FIELDS) | set(_VALUE_FIELDS) | {"type", "entity", "enum", "enums", "id", "field_id"}


def _entry_pairs(entry: dict) -> list[tuple[str, Any]]:
    """Normalize one emitted ``custom_fields`` entry into (key, value) pairs.

    Accepts the directive's ``{"key","value"}`` shape, aliased key/value fields
    (name/field/label + val), and the shapes models actually emit when they
    ignore the wrapper: the single-pair map ``{<field>: <value>}`` and the
    multi-field map ``{<f1>: v1, <f2>: v2}`` (live 2026-06-17: the records agent
    emitted ``[{<field>: value}]`` with no ``"key"`` field). Returns ``[]`` for
    an unusable entry."""
    if not isinstance(entry, dict):
        return []
    # explicit key field (canonical or aliased) + a value field
    for key_field in _KEY_FIELDS:
        if entry.get(key_field):
            key = str(entry[key_field]).strip()
            value = next((entry[v] for v in _VALUE_FIELDS if v in entry), None)
            return [(key, value)]
    # otherwise the field key(s) ARE the dict keys: {<field>: value, ...}
    return [(str(k).strip(), v) for k, v in entry.items() if str(k) not in _RESERVED]


def resolve_field_ops(fields_cfg: dict, entries: list[dict]) -> list[dict]:
    """Resolve emitted custom_fields entries into queued custom_field ops,
    honoring the ``write:true`` opt-in gate and dropping unknown keys /
    uncoercible values. Tolerant of the emitted shape (``_entry_pairs``) and of
    case: matches each pair's key against the field's logical key OR label,
    case-insensitively. field_id is stored as a string (provider ids cross the
    seam as strings)."""
    lookup = _field_lookup(fields_cfg)
    ops: list[dict] = []
    for entry in entries or []:
        for raw_key, raw_value in _entry_pairs(entry):
            match = lookup.get(raw_key.casefold())
            if not match:
                continue
            _logical_key, cfg = match
            if cfg.get("write") is not True or cfg.get("field_id") is None:
                continue
            coerced = coerce_field_value(cfg, raw_value)
            if coerced is None:
                continue
            ops.append(
                {
                    "kind": "custom_field",
                    "entity": (cfg.get("entity") or "lead"),
                    "field_id": str(cfg["field_id"]),
                    "value": coerced,
                    "type": (cfg.get("type") or "text"),
                }
            )
    return ops


def describe_writable_fields(fields_cfg: dict) -> str | None:
    """A concrete, enumerated records-agent directive: one line per writable
    field with its label, type, and (for selects) the enum options inline, so
    the model fills fields without cross-referencing the conversation_state JSON.
    Returns None when no field is writable."""
    lines: list[str] = []
    first_key: str | None = None
    for key, cfg in (fields_cfg or {}).items():
        if not isinstance(cfg, dict) or cfg.get("write") is not True:
            continue
        if first_key is None:
            first_key = str(key)
        label = str(cfg.get("label") or key).strip()
        ftype = str(cfg.get("type") or "text").strip()
        desc = f"- `{key}` ({label}, {ftype}"
        if ftype == "select" and cfg.get("enum_map"):
            opts = ", ".join(str(v) for v in cfg["enum_map"])
            desc += f"; value must be exactly one of: {opts}"
        desc += ")"
        lines.append(desc)
    if not lines:
        return None
    example = f'[{{"key": "{first_key}", "value": "..."}}]'
    return (
        "CRM custom fields — record `custom_fields` as a JSON array of "
        '{"key": ..., "value": ...} objects (e.g. ' + example + ") for any of "
        "these fields the customer's words clearly establish (omit the rest; "
        "never guess; use the exact key shown):\n" + "\n".join(lines)
    )


def resolve_tag_ops(tags_cfg: dict, keys: list[str]) -> list[dict]:
    """Resolve owner-blessed tag keys into namespaced tag ops; drop keys outside
    the configured vocabulary."""
    vocab = set((tags_cfg or {}).get("vocabulary") or [])
    namespace = (tags_cfg or {}).get("namespace") or ""
    ops: list[dict] = []
    for key in keys or []:
        k = (key or "").strip()
        if k and k in vocab:
            ops.append({"kind": "tag", "name": f"{namespace}{k}"})
    return ops
