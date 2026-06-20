"""The runtime injects local crm_lead_links state into conversation_state["crm"]
so the agent always sees the lead's stage + the human-touch trigger for free
(spec 2026-06-14-amocrm-slice2-crm-context-read)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.agent_runtime_v2.runtime_service import _load_crm_state

pytestmark = pytest.mark.asyncio


def _pipeline_config() -> dict:
    return {
        "pipeline_id": "777",
        "stage_map": {
            "new": {"stage_id": "1001", "sort": 10},
            "negotiation": {"stage_id": "1002", "sort": 20},
        },
        "pipeline_snapshot": [
            {"stage_id": "1001", "name": "New", "sort": 10, "kind": "active"},
            {"stage_id": "1002", "name": "Negotiation", "sort": 20, "kind": "active"},
        ],
    }


async def _seed(db_session, workspace, *, config=None, **link_over):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="mybiz",
        access_token="tok",
        refresh_token="ref",
        webhook_token="wh-1",
        pipeline_config=config or _pipeline_config(),
    )
    db_session.add(conn)
    await db_session.flush()
    customer = Customer(
        workspace_id=workspace.id, display_name="Ali", contact_type="customer"
    )
    db_session.add(customer)
    await db_session.flush()
    conv = Conversation(
        workspace_id=workspace.id, customer_id=customer.id, channel="telegram_dm",
        pipeline_stage="new",
    )
    db_session.add(conv)
    await db_session.flush()
    link = CrmLeadLink(
        workspace_id=workspace.id,
        connection_id=conn.id,
        conversation_id=conv.id,
        customer_id=customer.id,
        **link_over,
    )
    db_session.add(link)
    await db_session.flush()
    return conv, link


async def test_human_touched_lead_injects_full_state(db_session, workspace):
    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="negotiation",
        synced_stage_role="negotiation",
        stage_authority="human",
        provider_lead_id="55",
        last_observed_stage_id="1002",
    )
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state == {
        "stage_role": "negotiation",
        "stage_authority": "human",
        "stage_label": "Negotiation",
        "lead_ref": "amocrm:lead:55",
    }


async def test_no_link_returns_none(db_session, workspace):
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=999999
    )
    assert state is None


async def test_load_crm_state_injects_deal_value(db_session, workspace):
    """The lead's deal_value (a free PK lookup on the open session) rides into
    conversation_state["crm"] so the model sees it without calling a tool —
    this replaces the retired crm.context round-trip."""
    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="negotiation",
        synced_stage_role="negotiation",
        stage_authority="human",
        provider_lead_id="55",
        last_observed_stage_id="1002",
    )
    conv.deal_value = Decimal("9790000")
    await db_session.flush()
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state is not None
    assert state["deal_value"] == Decimal("9790000")


async def test_load_crm_state_deal_value_is_json_serializable(db_session, workspace):
    """deal_value must ride into conversation_state as a JSON-native number, not a
    raw Decimal. conversation_state is json.dumps'd in the dynamic-context byte
    estimate; a Decimal there raised TypeError in gather_turn_context and quarantined
    the turn after 3 failed dispatches -> the seller went silent (live 2026-06-15,
    conv 1 deal_value=9790000.00)."""
    import json

    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="negotiation",
        synced_stage_role="negotiation",
        stage_authority="human",
        provider_lead_id="55",
        last_observed_stage_id="1002",
    )
    conv.deal_value = Decimal("9790000.00")
    await db_session.flush()
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state is not None
    json.dumps(state)  # must NOT raise — the live crash was here, transitively
    assert not isinstance(state["deal_value"], Decimal)
    assert state["deal_value"] == 9790000


def test_dynamic_context_payload_survives_non_json_native_value():
    """The dynamic-context byte estimate must NEVER poison a turn on a non-JSON-native
    value in conversation_state. Live 2026-06-15: a Decimal deal_value from the
    slice-5 inject raised TypeError in _dynamic_context_payload -> the turn was
    quarantined -> no reply. A telemetry byte-count must degrade, never crash."""
    from app.modules.agent_runtime_v2.runtime_service import _dynamic_context_payload

    out = _dynamic_context_payload(
        customer_turn_text="narx?",
        customer_query_text="narx?",
        session_summary="",
        transcript_hits=[],
        conversation_state={"crm": {"deal_value": Decimal("9790000.00")}},
        authority_lines=[],
        style_lines=[],
        policy_warnings=[],
    )
    assert out["estimated_bytes"] > 0  # did not raise


async def test_pre_push_link_has_role_and_authority_only(db_session, workspace):
    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="new",
        stage_authority="oqim",
        provider_lead_id=None,
    )
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state == {"stage_role": "new", "stage_authority": "oqim"}


def _nested_config() -> dict:
    return {
        "schema_version": 2,
        "snapshot": {"pipelines": [
            {"id": "222", "name": "B", "statuses": [
                {"stage_id": "301", "name": "Boshlash", "sort": 10, "kind": "active"}]},
        ]},
        "mapping": {
            "default_pipeline_id": "222",
            "pipelines": {"222": {"name": "B",
                          "role_map": {"new": {"stage_id": "301", "sort": 10}}}},
        },
    }


async def test_nested_config_labels_stage_from_pinned_pipeline(db_session, workspace):
    conv, _ = await _seed(
        db_session, workspace, config=_nested_config(),
        desired_stage_role="new", synced_stage_role="new", stage_authority="human",
        provider_lead_id="55", pipeline_id="222", last_observed_stage_id="301",
    )
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state["stage_label"] == "Boshlash"   # from the nested pipeline's snapshot
    assert state["lead_ref"] == "amocrm:lead:55"


async def _seed_agent_session(db_session, workspace, conv, *, channel_config):
    """An Agent (with crm.fields config) + an AgentSession linking it to the
    conversation — the path _load_crm_state walks to find the SEE menu config."""
    from app.models.agent import Agent
    from app.models.agent_session import AgentSession

    agent = Agent(
        workspace_id=workspace.id,
        name="Seller",
        agent_type="seller",
        channel_config=channel_config,
    )
    db_session.add(agent)
    await db_session.flush()
    sess = AgentSession(
        workspace_id=workspace.id,
        conversation_id=conv.id,
        agent_id=agent.id,
        channel="telegram_dm",
        session_key="sess-key-1",
        hermes_session_id="hermes-sess-1",
    )
    db_session.add(sess)
    await db_session.flush()
    return agent


async def test_crm_state_injects_inject_true_field_menu(db_session, workspace):
    """S4 SEE: _load_crm_state adds a DB-only capability menu of the owner-blessed
    inject:true fields (key/label/type + enum LABELS) — no current values."""
    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="negotiation",
        synced_stage_role="negotiation",
        stage_authority="human",
        provider_lead_id="55",
        last_observed_stage_id="1002",
    )
    await _seed_agent_session(
        db_session, workspace, conv,
        channel_config={"crm": {"fields": {
            "budget": {"field_id": 600123, "label": "Budjet", "type": "numeric", "inject": True},
            "source": {"field_id": 600124, "label": "Manba", "type": "select", "inject": True,
                       "enum_map": {"Instagram": 9001}},
            "hidden": {"field_id": 600999, "label": "X", "type": "text", "inject": False},
        }}},
    )
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state is not None
    menu = {f["key"]: f for f in state["fields"]}
    assert set(menu) == {"budget", "source"}            # inject:false excluded
    assert menu["source"]["enums"] == ["Instagram"]     # enum labels only
    assert "value" not in menu["budget"]                # capability only, no current value


async def test_crm_state_no_field_menu_when_config_absent(db_session, workspace):
    """No crm.fields config (or no agent session) -> no 'fields' key (unchanged)."""
    conv, _ = await _seed(
        db_session,
        workspace,
        desired_stage_role="new",
        stage_authority="oqim",
        provider_lead_id=None,
    )
    await _seed_agent_session(
        db_session, workspace, conv, channel_config={"mode": "dm"},
    )
    state = await _load_crm_state(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert state is not None
    assert "fields" not in state
