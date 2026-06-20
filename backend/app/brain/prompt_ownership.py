"""Prompt/LLM ownership ledger for the current OQIM runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.brain.prompt_registry import DEFAULT_PROMPT_ROOT, PromptRegistry

DEFAULT_PROMPT_OWNERSHIP_LEDGER = DEFAULT_PROMPT_ROOT / "ownership.yaml"
ALLOWED_CLASSIFICATIONS = frozenset(
    {
        "composer-only",
        "composer-boundary",
        "planner-composer",
        "signal-producer",
        "verifier",
        "compatibility-only",
        "delete",
    }
)


@dataclass(frozen=True, slots=True)
class PromptOwnershipEntry:
    prompt_id: str | None
    prompt_prefix: str | None
    prompt_version: str | None
    call_site: str
    current_owner: str
    target_owner: str
    classification: str
    commercial_truth_inferred: tuple[str, ...]
    replacement_memory_fields: tuple[str, ...]
    cutover_status: str
    deletion_plan: str | None
    may_mutate_commercial_truth: bool
    proof_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptOwnershipLedger:
    schema_version: str
    entries: tuple[PromptOwnershipEntry, ...]


def load_prompt_ownership_ledger(
    path: Path = DEFAULT_PROMPT_OWNERSHIP_LEDGER,
) -> PromptOwnershipLedger:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("prompt ownership ledger must be a mapping")
    schema_version = _required_str(raw, "schema_version")
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("prompt ownership ledger must include entries")

    entries = tuple(_entry(item) for item in raw_entries)
    _validate_prompt_ids(path=path, entries=entries)
    return PromptOwnershipLedger(schema_version=schema_version, entries=entries)


def _entry(raw: Any) -> PromptOwnershipEntry:
    if not isinstance(raw, dict):
        raise ValueError("prompt ownership entry must be a mapping")
    classification = _required_str(raw, "classification")
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise ValueError(f"unknown prompt ownership classification {classification!r}")
    return PromptOwnershipEntry(
        prompt_id=_optional_str(raw.get("prompt_id")),
        prompt_prefix=_optional_str(raw.get("prompt_prefix")),
        prompt_version=_optional_str(raw.get("prompt_version")),
        call_site=_required_str(raw, "call_site"),
        current_owner=_required_str(raw, "current_owner"),
        target_owner=_required_str(raw, "target_owner"),
        classification=classification,
        commercial_truth_inferred=_required_str_tuple(
            raw,
            "commercial_truth_inferred",
        ),
        replacement_memory_fields=_required_str_tuple(
            raw,
            "replacement_memory_fields",
        ),
        cutover_status=_required_str(raw, "cutover_status"),
        deletion_plan=_optional_str(raw.get("deletion_plan")),
        may_mutate_commercial_truth=bool(raw.get("may_mutate_commercial_truth")),
        proof_refs=_required_str_tuple(raw, "proof_refs"),
    )


def _validate_prompt_ids(
    *,
    path: Path,
    entries: tuple[PromptOwnershipEntry, ...],
) -> None:
    registry = PromptRegistry(root=path.parent)
    prompt_ids = {prompt.id for prompt in registry.list()}
    for entry in entries:
        if not entry.prompt_id and not entry.prompt_prefix:
            raise ValueError("prompt ownership entry must include prompt_id or prompt_prefix")
        if entry.prompt_id and entry.prompt_id not in prompt_ids:
            raise ValueError(f"unknown prompt_id in ownership ledger: {entry.prompt_id}")
        if entry.prompt_prefix and not any(
            prompt_id.startswith(entry.prompt_prefix) for prompt_id in prompt_ids
        ):
            raise ValueError(
                f"unknown prompt_prefix in ownership ledger: {entry.prompt_prefix}"
            )


def covered_prompt_ids(
    ledger: PromptOwnershipLedger,
    *,
    root: Path = DEFAULT_PROMPT_ROOT,
) -> set[str]:
    registry = PromptRegistry(root=root)
    prompt_ids = {prompt.id for prompt in registry.list()}
    covered: set[str] = set()
    for entry in ledger.entries:
        if entry.prompt_id:
            covered.add(entry.prompt_id)
        if entry.prompt_prefix:
            covered.update(
                prompt_id
                for prompt_id in prompt_ids
                if prompt_id.startswith(entry.prompt_prefix)
            )
    return covered


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = _optional_str(raw.get(key))
    if value is None:
        raise ValueError(f"prompt ownership ledger field {key!r} is required")
    return value


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _required_str_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"prompt ownership ledger field {key!r} must be a list")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if not items:
        raise ValueError(f"prompt ownership ledger field {key!r} cannot be empty")
    return items
