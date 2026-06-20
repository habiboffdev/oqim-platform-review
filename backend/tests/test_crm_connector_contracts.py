"""CRM connector contracts + settings."""
from __future__ import annotations

import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_settings_expose_amocrm_config_defaults():
    settings = get_settings()
    assert settings.amocrm_client_id == ""
    assert settings.amocrm_client_secret == ""
    assert settings.amocrm_redirect_uri.endswith("/api/amocrm/auth/callback")


async def test_role_order_is_the_approved_ladder():
    from app.modules.crm_connector.contracts import ROLE_ORDER, role_index

    assert ROLE_ORDER == ("new", "negotiation", "qualified", "won", "lost")
    assert role_index("qualified") > role_index("negotiation") > role_index("new")
    assert role_index("unknown_role") == -1


async def test_target_role_from_facts_is_pure_and_monotonic_inputs():
    from app.modules.crm_connector.contracts import target_role_for_facts

    assert target_role_for_facts({}) == "new"
    assert target_role_for_facts({"engaged": True}) == "new"
    assert target_role_for_facts({"buying_signal_seen": True}) == "negotiation"
    # S3 #422: customer_name_known/need_known are unreachable (record_intelligence
    # never emits customer_name/need) and were removed — they no longer advance a lead.
    assert target_role_for_facts({"customer_name_known": True, "need_known": True}) == "new"
    assert target_role_for_facts({"contact_captured": True}) == "negotiation"
    assert target_role_for_facts({"handoff_recorded": "lead"}) == "qualified"
    # OQIM never targets terminal roles (human closes won/lost).
    assert target_role_for_facts({"handoff_recorded": "lead"}) == "qualified"


async def test_crm_account_schema_holds_pipelines_with_empty_s2_fields():
    from app.modules.crm_connector.contracts import (
        CrmAccountSchema,
        CrmPipeline,
        CrmPipelineStatus,
    )

    schema = CrmAccountSchema(
        pipelines=[
            CrmPipeline(
                pipeline_id="111",
                name="Main",
                is_main=True,
                statuses=[CrmPipelineStatus(stage_id="201", name="First", sort=10, kind="active")],
            )
        ]
    )
    assert schema.pipelines[0].pipeline_id == "111"
    # S2 fields default empty (custom fields / users / task types added later).
    assert schema.custom_fields == {}
    assert schema.users == []
    assert schema.task_types == []


async def test_crm_field_and_account_schema_types():
    from app.modules.crm_connector.contracts import (
        CrmAccountSchema,
        CrmFieldDef,
        CrmFieldEnum,
        CrmPipeline,
        CrmTaskType,
        CrmUser,
    )

    fld = CrmFieldDef(
        key_id="600124", code=None, name="Manba", type="select",
        enums=(CrmFieldEnum(enum_id="9001", value="Instagram"),),
    )
    schema = CrmAccountSchema(
        pipelines=[CrmPipeline(pipeline_id="111", name="A", is_main=True, statuses=[])],
        custom_fields={"leads": [fld]},
        users=[CrmUser(user_id="55001", name="Aziz")],
        task_types=[CrmTaskType(task_type_id="1", name="Aloqa")],
    )
    assert schema.custom_fields["leads"][0].enums[0].value == "Instagram"
    assert schema.users[0].name == "Aziz"
    assert schema.task_types[0].task_type_id == "1"


async def test_crm_role_label_maps_roles_to_uzbek():
    from app.modules.crm_connector.contracts import crm_role_label

    assert crm_role_label("new") == "Yangi"
    assert crm_role_label("negotiation") == "Muzokara"
    assert crm_role_label("qualified") == "Malakali"
    assert crm_role_label("won") == "Muvaffaqiyatli"
    assert crm_role_label("lost") == "Yopilgan"
    # unknown role falls back to the raw key (never crashes a note).
    assert crm_role_label("weird") == "weird"


def test_crm_stage_event_carries_optional_author():
    from app.modules.crm_connector.contracts import CrmStageEvent
    # default is None (author unknown / not parsed)
    assert CrmStageEvent(kind="note_lead", lead_id="200").author_id is None
    # a real human user id can be carried
    ev = CrmStageEvent(kind="update_lead", lead_id="200", author_id=777)
    assert ev.author_id == 777
