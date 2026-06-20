from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.hermes_runtime.contracts import (
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    build_hermes_run_idempotency_key,
)


def test_hermes_run_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        HermesRunInput(
            workspace_id=1,
            agent_id=2,
            trigger_type="telegram_message",
            trigger_id="message:123",
            surprise="not-allowed",
        )


def test_idempotency_key_is_stable_for_same_trigger() -> None:
    first = build_hermes_run_idempotency_key(
        workspace_id=1,
        agent_id=2,
        trigger_type="telegram_message",
        trigger_id="message:123",
        run_mode=HermesRunMode.REPLY,
    )
    second = build_hermes_run_idempotency_key(
        workspace_id=1,
        agent_id=2,
        trigger_type="telegram_message",
        trigger_id="message:123",
        run_mode="reply",
    )

    assert first == second
    assert first == "hermes_run:1:2:telegram_message:message:123:reply"


def test_platform_agent_modes_serialize_as_stable_json() -> None:
    modes = [
        HermesRunMode.REPLY,
        HermesRunMode.PERSONAL,
        HermesRunMode.BROADCAST,
        HermesRunMode.SCAN,
        HermesRunMode.ENTERPRISE_QA,
        HermesRunMode.LEARNING,
    ]

    payloads = [
        HermesRunInput(
            workspace_id=1,
            agent_id=2,
            lane=HermesRunLane.FAST_INTERACTIVE,
            run_mode=mode,
            trigger_type="manual",
            trigger_id=f"trigger:{mode}",
        ).model_dump(mode="json")
        for mode in modes
    ]

    assert [payload["run_mode"] for payload in payloads] == [
        "reply",
        "personal",
        "broadcast",
        "scan",
        "enterprise_qa",
        "learning",
    ]
    assert all(payload["tenant_id"] == 1 for payload in payloads)
    assert all(payload["idempotency_key"].startswith("hermes_run:1:2:manual:") for payload in payloads)
