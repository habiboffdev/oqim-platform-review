from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models.agent import Agent
from app.modules.workspace_os.custom_agent import (
    WizardSectionDraft,
    derive_kind_defaults,
)
from app.modules.workspace_os.custom_agent_draft import (
    CustomAgentDraftInput,
    CustomAgentDraftService,
)

class TestKindDefaults:
    def test_seller_defaults_include_catalog_and_send(self) -> None:
        defaults = derive_kind_defaults("seller")
        assert "catalog" in defaults["brain_scopes"]
        assert "telegram.send_message" in defaults["tool_scopes"]
        assert defaults["trigger_sources"] == ["channel_message_received"]
        assert defaults["permission_mode"] == "auto_approve"

    def test_custom_defaults_are_conservative(self) -> None:
        defaults = derive_kind_defaults("custom")
        assert defaults["tool_scopes"] == ["telegram.read_messages"]
        assert defaults["trigger_sources"] == []
        assert defaults["permission_mode"] == "ask_always"

    def test_unknown_kind_falls_back_to_custom(self) -> None:
        assert derive_kind_defaults("not_a_kind") == derive_kind_defaults("custom")

    def test_section_draft_rejects_blank_body(self) -> None:
        with pytest.raises(ValueError):
            WizardSectionDraft(section_key="role", title="Rol", body="", order_index=10)

    def test_section_draft_rejects_whitespace_only_body(self) -> None:
        with pytest.raises(ValueError):
            WizardSectionDraft(section_key="role", title="Rol", body="   ", order_index=10)


_FAKE_DRAFT = {
    "role": "Mijozlarning savollariga javob beradi va sotuvni bir qadam oldinga suradi.",
    "when_to_act": "Yangi Telegram xabari kelganda va dalil yetarli bo'lganda javob beradi.",
    "never_guess": "Narx, ombor va to'lov shartlarini taxmin qilmaydi.",
}


@pytest.mark.asyncio
class TestDraftService:
    async def test_draft_returns_six_sections_and_kind_defaults(
        self, db_session, workspace
    ) -> None:
        service = CustomAgentDraftService(db_session)
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value=_FAKE_DRAFT),
        ):
            result = await service.draft(
                workspace_id=workspace.id,
                payload=CustomAgentDraftInput(
                    agent_kind="seller",
                    name="Sotuvchi agent",
                    does_what="Mahsulot va narx savollariga javob beradi",
                    when_replies="Yangi xabar kelganda",
                    never_does="Narxni taxmin qilmaydi",
                ),
            )
        keys = [s.section_key for s in result.sections]
        assert keys == [
            "role",
            "when_to_act",
            "brain_and_sources",
            "tools",
            "never_guess",
            "runtime_config",
        ]
        bodies = {s.section_key: s.body for s in result.sections}
        assert bodies["role"] == _FAKE_DRAFT["role"]
        assert bodies["never_guess"] == _FAKE_DRAFT["never_guess"]
        assert "catalog" in result.brain_scopes
        assert "telegram.send_message" in result.tool_scopes
        assert result.trust_mode == "autopilot"  # auto_approve -> autopilot

    async def test_draft_persists_nothing(self, db_session, workspace) -> None:
        before = (
            await db_session.scalars(
                select(Agent).where(Agent.workspace_id == workspace.id)
            )
        ).all()
        service = CustomAgentDraftService(db_session)
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value=_FAKE_DRAFT),
        ):
            await service.draft(
                workspace_id=workspace.id,
                payload=CustomAgentDraftInput(
                    agent_kind="custom",
                    name="Test agent",
                    does_what="Nimadir qiladi",
                ),
            )
        after = (
            await db_session.scalars(
                select(Agent).where(Agent.workspace_id == workspace.id)
            )
        ).all()
        assert len(after) == len(before)

    async def test_draft_falls_back_when_llm_returns_empty(
        self, db_session, workspace
    ) -> None:
        service = CustomAgentDraftService(db_session)
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value={}),
        ):
            result = await service.draft(
                workspace_id=workspace.id,
                payload=CustomAgentDraftInput(
                    agent_kind="support",
                    name="Qollab agent",
                    does_what="Muammolarni hal qiladi",
                ),
            )
        bodies = {s.section_key: s.body for s in result.sections}
        assert bodies["role"].strip()
        assert bodies["when_to_act"].strip()
        assert bodies["never_guess"].strip()

    async def test_draft_role_body_meets_mission_min_length(
        self, db_session, workspace
    ) -> None:
        # A tiny non-empty LLM role ("ok") must still yield a role section >= 8 chars,
        # because the wizard sends it as `mission` (CustomAgentPackageInput min_length 8).
        service = CustomAgentDraftService(db_session)
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value={"role": "ok", "when_to_act": "", "never_guess": ""}),
        ):
            result = await service.draft(
                workspace_id=workspace.id,
                payload=CustomAgentDraftInput(
                    agent_kind="custom",
                    name="Test agent",
                    does_what="Mahsulotlar haqida javob beradi",
                ),
            )
        role_body = next(s.body for s in result.sections if s.section_key == "role")
        assert len(role_body) >= 8
        assert role_body == "Mahsulotlar haqida javob beradi"  # fell back to does_what
