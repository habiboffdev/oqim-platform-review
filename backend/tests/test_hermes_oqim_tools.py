import json
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.agent_runtime_v2.hermes.oqim_tools import register_oqim_tools
from app.modules.agent_runtime_v2.hermes.tool_context import use_tool_context


def _ctx(**kw):
    from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext
    base = dict(workspace_id=1, agent_id=2, conversation_id=None,
                grounding=[], history=[], loop=object())
    base.update(kw)
    return ToolContext(**base)


def test_register_oqim_tools_exposes_knowledge_tools_without_legacy_retrieval_tools():
    from tools.registry import registry
    register_oqim_tools()
    register_oqim_tools()  # no raise
    names = set(registry.get_tool_names_for_toolset("oqim"))
    assert "search_catalog_truth" not in names
    assert "search_business_rules" not in names
    assert "search_voice_examples" not in names
    assert "recall_business_facts" not in names
    assert "knowledge_search" in names
    assert "knowledge_search_chat_memory" in names
    assert "knowledge_search_catalog" in names
    assert "knowledge_search_media" in names
    assert "knowledge_get_item" in names
    assert "knowledge_explain_sources" in names
    assert "knowledge_save_script" in names
    assert "knowledge_save_note" in names
    assert "knowledge_create_source_doc" in names
    assert "knowledge_attach_to_collection" in names
    assert "knowledge_tag_item" in names
    assert "knowledge_propose_candidate" in names
    assert "knowledge_extract_candidates" in names
    assert "knowledge_propose_catalog_update" in names
    assert "knowledge_propose_policy_update" in names
    assert "knowledge_propose_faq_update" in names
    assert "knowledge_propose_rule" in names


def test_knowledge_save_script_tool_returns_saved_item():
    from unittest.mock import MagicMock

    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_save_script

    ctx = _ctx()
    fake_future = MagicMock()
    fake_future.result.return_value = {
        "item_id": "knowledge:1",
        "kind": "script",
        "title": "Launch script",
        "collection_ids": ["Mirzo / Marketing Scripts"],
    }
    captured = {}

    def _capture(coro, _loop):
        captured["coro"] = coro
        return fake_future

    with use_tool_context(ctx), patch(
        "app.modules.agent_runtime_v2.hermes.oqim_tools.asyncio.run_coroutine_threadsafe",
        MagicMock(side_effect=_capture),
    ):
        out = json.loads(
            knowledge_save_script(
                {
                    "title": "Launch script",
                    "body_text": "Starter coins promo",
                    "collection_ids": ["Mirzo / Marketing Scripts"],
                    "tags": ["script"],
                }
            )
        )

    captured["coro"].close()
    fake_future.result.assert_called_once()
    assert out["status"] == "ok"
    assert out["item"]["item_id"] == "knowledge:1"


@pytest.mark.asyncio
async def test_knowledge_save_async_records_executed_agent_control_action(
    db_session,
    workspace_with_telegram_user,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from app.models.commercial_action import CommercialActionProposalRecord
    from app.modules.agent_runtime_v2.hermes.oqim_tools import _knowledge_save_async

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr("app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session)

    result = await _knowledge_save_async(
        workspace_id=workspace_with_telegram_user.id,
        agent_id=2,
        hermes_run_id="hermes-run-save-1",
        raw_scope="personal",
        kind="script",
        title="Launch script",
        body_text="Starter coins promo",
        collection_ids=["Mirzo / Marketing Scripts"],
        tags=["script"],
        created_by_ref="agent:2",
        source_kind="agent_note",
        correlation_id="corr-save-tool",
        idempotency_key="save-tool-1",
    )

    row = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace_with_telegram_user.id,
            CommercialActionProposalRecord.proposal_id
            == result["agent_control_action"]["action_id"],
        )
    )

    assert result["item_id"].startswith("knowledge:")
    assert result["agent_control_action"]["action_kind"] == "knowledge.write"
    assert result["agent_control_action"]["status"] == "executed"
    assert result["agent_control_action"]["hermes_run_id"] == "hermes-run-save-1"
    assert row is not None
    assert row.action_type == "knowledge.write"
    assert row.lifecycle_state == "executed"
    assert row.trace_id == "hermes-run-save-1"
    assert "agent_run:hermes-run-save-1" in row.source_refs
    assert row.payload["agent_control"]["user_id"] == "user:999888777"
    assert row.payload["agent_control"]["proposed_payload"]["operation"] == "knowledge.script.save"


@pytest.mark.asyncio
async def test_knowledge_attach_and_tag_tools_record_agent_control_actions(
    db_session,
    workspace,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from app.models.commercial_action import CommercialActionProposalRecord
    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        _knowledge_attach_to_collection_async,
        _knowledge_tag_item_async,
    )
    from app.modules.knowledge_mcp.contracts import KnowledgeSaveInput, KnowledgeScope
    from app.modules.knowledge_mcp.service import KnowledgeMCPService

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr("app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session)
    scope = KnowledgeScope(owner_type="user", owner_id=f"workspace-user:{workspace.id}")
    item = await KnowledgeMCPService(db_session).save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="script",
            title="Existing script",
            body_text="Starter coins draft.",
            created_by_ref="agent:2",
            correlation_id="corr-existing-script",
            idempotency_key="existing-script",
        )
    )

    attached = await _knowledge_attach_to_collection_async(
        workspace_id=workspace.id,
        agent_id=2,
        hermes_run_id="hermes-run-metadata-1",
        raw_scope="personal",
        item_id=item.item_id,
        collection_ids=["Marketing"],
    )
    tagged = await _knowledge_tag_item_async(
        workspace_id=workspace.id,
        agent_id=2,
        hermes_run_id="hermes-run-metadata-1",
        raw_scope="personal",
        item_id=item.item_id,
        tags=["promo"],
    )

    rows = (
        await db_session.execute(
            select(CommercialActionProposalRecord)
            .where(
                CommercialActionProposalRecord.workspace_id == workspace.id,
                CommercialActionProposalRecord.action_type == "knowledge.write",
                CommercialActionProposalRecord.trace_id == "hermes-run-metadata-1",
            )
            .order_by(CommercialActionProposalRecord.id.asc())
        )
    ).scalars().all()

    assert attached["agent_control_action"]["status"] == "executed"
    assert tagged["agent_control_action"]["status"] == "executed"
    assert [row.payload["agent_control"]["proposed_payload"]["operation"] for row in rows] == [
        "knowledge.attach_to_collection",
        "knowledge.tag_item",
    ]
    assert all("agent_run:hermes-run-metadata-1" in row.source_refs for row in rows)


@pytest.mark.asyncio
async def test_knowledge_tool_events_feed_agent_control_run_audit(
    db_session,
    workspace_with_telegram_user,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from app.models.hermes_run import HermesRunEvent
    from app.modules.agent_control.audit import AgentControlAuditService
    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        _knowledge_save_async,
        _knowledge_search_async,
    )
    from app.modules.hermes_runtime.contracts import HermesRunInput, HermesRunMode
    from app.modules.hermes_runtime.service import HermesRunService

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr("app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session)
    run = await HermesRunService(db_session).start_or_dedupe(
        HermesRunInput(
            workspace_id=workspace_with_telegram_user.id,
            agent_id=None,
            run_mode=HermesRunMode.PERSONAL,
            trigger_type="manual",
            trigger_id="phase4-audit",
            correlation_id="corr-phase4-audit",
            idempotency_key="phase4-audit-run",
        )
    )
    saved = await _knowledge_save_async(
        workspace_id=workspace_with_telegram_user.id,
        agent_id=2,
        hermes_run_id=run.run_id,
        raw_scope="personal",
        kind="script",
        title="Audit launch script",
        body_text="Starter coins audit promo script.",
        collection_ids=["Mirzo / Marketing Scripts"],
        tags=["script"],
        created_by_ref="agent:2",
        source_kind="agent_note",
        correlation_id="corr-audit-save",
        idempotency_key="audit-save-tool-1",
    )
    await _knowledge_search_async(
        workspace_id=workspace_with_telegram_user.id,
        hermes_run_id=run.run_id,
        raw_scope="personal",
        query="audit promo",
        collection_ids=["Mirzo / Marketing Scripts"],
        tags=["script"],
        enable_semantic=False,
        limit=5,
    )

    audit = await AgentControlAuditService(db_session).run_audit(
        workspace_id=workspace_with_telegram_user.id,
        run_id=run.run_id,
    )

    assert audit["summary"]["knowledge_operation_count"] == 2
    assert audit["summary"]["knowledge_search_count"] == 1
    assert audit["summary"]["action_count"] == 1
    assert audit["knowledge_searches"][0]["query"] == "audit promo"
    assert audit["knowledge_searches"][0]["hit_count"] == 1
    assert audit["knowledge_searches"][0]["citations"][0]["item_id"] == saved["item_id"]
    search_event = await db_session.scalar(
        select(HermesRunEvent).where(
            HermesRunEvent.workspace_id == workspace_with_telegram_user.id,
            HermesRunEvent.run_id == run.run_id,
            HermesRunEvent.tool_name == "knowledge_search",
        )
    )
    assert search_event is not None
    assert search_event.payload["latency_ms"] >= 0
    assert search_event.payload["citation_count"] == 1
    assert search_event.payload["source_ref_count"] == 1
    assert search_event.payload["evidence_backed"] is True
    assert audit["actions"][0]["action_kind"] == "knowledge.write"
    assert audit["actions"][0]["lifecycle_state"] == "executed"
    assert audit["actions"][0]["trace_id"] == run.run_id


@pytest.mark.asyncio
async def test_knowledge_search_async_commits_tool_event_audit(
    db_session,
    workspace_with_telegram_user,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from app.modules.agent_runtime_v2.hermes.oqim_tools import _knowledge_search_async
    from app.modules.hermes_runtime.contracts import HermesRunInput, HermesRunMode
    from app.modules.hermes_runtime.service import HermesRunService

    class SessionProxy:
        def __init__(self, inner):
            self._inner = inner
            self.commit = AsyncMock(wraps=inner.commit)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    proxy = SessionProxy(db_session)

    @asynccontextmanager
    async def fake_session():
        yield proxy

    monkeypatch.setattr("app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session)
    run = await HermesRunService(db_session).start_or_dedupe(
        HermesRunInput(
            workspace_id=workspace_with_telegram_user.id,
            agent_id=None,
            run_mode=HermesRunMode.PERSONAL,
            trigger_type="manual",
            trigger_id="phase4-search-commit",
            correlation_id="corr-phase4-search-commit",
            idempotency_key="phase4-search-commit-run",
        )
    )

    await _knowledge_search_async(
        workspace_id=workspace_with_telegram_user.id,
        hermes_run_id=run.run_id,
        raw_scope="personal",
        query="no matching item is fine",
        collection_ids=[],
        tags=[],
        enable_semantic=False,
        limit=5,
    )

    proxy.commit.assert_awaited_once()


def test_knowledge_search_no_loop_closes_coroutine_and_records_error():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search

    ctx = _ctx(loop=None)
    with use_tool_context(ctx):
        out = json.loads(knowledge_search({"query": "starter script"}))

    assert out["status"] == "degraded"
    assert out["error"] == "no_loop"
    assert ctx.tool_errors == ["knowledge_search:no_loop"]


def test_knowledge_search_defaults_to_semantic_retrieval():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search

    ctx = _ctx()
    captured = {}

    def _fake_search(**kwargs):
        captured.update(kwargs)

        async def _coro():
            return {"hits": []}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        captured["error_prefix"] = error_prefix
        return {"hits": []}

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_search_async", _fake_search),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        out = json.loads(knowledge_search({"query": "marketing script"}))

    assert out["status"] == "ok"
    assert captured["enable_semantic"] is True
    assert captured["error_prefix"] == "knowledge_search"


def test_knowledge_chat_memory_search_no_loop_records_error():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search_chat_memory

    ctx = _ctx(loop=None)
    with use_tool_context(ctx):
        out = json.loads(knowledge_search_chat_memory({"query": "qarz berganlar"}))

    assert out["status"] == "degraded"
    assert out["error"] == "no_loop"
    assert ctx.tool_errors == ["knowledge_chat_memory_search:no_loop"]


def test_knowledge_catalog_search_no_loop_records_error():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search_catalog

    ctx = _ctx(loop=None)
    with use_tool_context(ctx):
        out = json.loads(knowledge_search_catalog({"query": "starter coins"}))

    assert out["status"] == "degraded"
    assert out["error"] == "no_loop"
    assert ctx.tool_errors == ["knowledge_catalog_search:no_loop"]


def test_knowledge_catalog_search_obeys_runtime_profile_caps_and_modes():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search_catalog

    ctx = _ctx(
        max_catalog_searches=1,
        catalog_enable_semantic=False,
        catalog_enable_rerank=False,
    )
    captured = {}

    def _fake_search(**kwargs):
        captured.update(kwargs)

        async def _coro():
            return {"hits": []}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        captured["error_prefix"] = error_prefix
        return {"hits": []}

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_catalog_search_async", _fake_search),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        first = json.loads(knowledge_search_catalog({"query": "starter coins"}))
        second = json.loads(knowledge_search_catalog({"query": "starter coins again"}))

    assert first["status"] == "ok"
    assert captured["enable_semantic"] is False
    assert captured["enable_rerank"] is False
    assert captured["error_prefix"] == "knowledge_catalog_search"
    assert second["status"] == "blocked"
    assert second["reason"] == "catalog_search_limit"
    assert ctx.tool_errors == ["knowledge_catalog_search:catalog_search_limit"]


def test_knowledge_catalog_search_stashes_approved_authority_lines():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search_catalog

    ctx = _ctx()

    def _fake_search(**_kwargs):
        async def _coro():
            return {"hits": []}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        assert error_prefix == "knowledge_catalog_search"
        return {
            "hits": [
                {
                    "item": {
                        "kind": "catalog",
                        "title": "Starter coins",
                        "authority_state": "approved",
                        "metadata": {
                            "fact_type": "catalog_offer",
                            "value": {"price": "40 000", "currency": "UZS"},
                        },
                    },
                    "score": 0.99,
                }
            ]
        }

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_catalog_search_async", _fake_search),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        out = json.loads(knowledge_search_catalog({"query": "starter coins"}))

    assert out["status"] == "ok"
    assert ctx.tool_authority_lines == ["[OFFER] Starter coins: 40 000 UZS"]


def test_knowledge_media_search_defaults_to_image_modality():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_search_media

    ctx = _ctx()
    captured = {}

    def _fake_search(**kwargs):
        captured.update(kwargs)

        async def _coro():
            return {"hits": []}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        captured["error_prefix"] = error_prefix
        return {"hits": []}

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_media_search_async", _fake_search),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        out = json.loads(knowledge_search_media({"query": "buyer sent ruby ring photo"}))

    assert out["status"] == "ok"
    assert captured["query_modalities"] == ["image"]
    assert captured["error_prefix"] == "knowledge_media_search"


def test_knowledge_get_item_no_loop_records_error():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_get_item

    ctx = _ctx(loop=None)
    with use_tool_context(ctx):
        out = json.loads(knowledge_get_item({"item_id": "knowledge:1"}))

    assert out["status"] == "degraded"
    assert out["error"] == "no_loop"
    assert ctx.tool_errors == ["knowledge_get_item:no_loop"]


def test_knowledge_tag_item_empty_requires_tags():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_tag_item

    ctx = _ctx()
    with use_tool_context(ctx):
        out = json.loads(knowledge_tag_item({"item_id": "knowledge:1", "tags": []}))

    assert out["status"] == "empty"


def test_knowledge_propose_candidate_passes_hermes_run_id():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_propose_candidate

    ctx = _ctx(hermes_run_id="hermes-run-tool-1")
    captured = {}

    def _fake_candidate(**kwargs):
        captured.update(kwargs)

        async def _coro():
            return {"candidate": {"candidate_id": "knowledge_candidate:1"}}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        captured["error_prefix"] = error_prefix
        return {"candidate": {"candidate_id": "knowledge_candidate:1"}}

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_candidate_async", _fake_candidate),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        out = json.loads(
            knowledge_propose_candidate(
                {
                    "source_id": "knowledge_source:1",
                    "proposed_kind": "policy",
                    "proposed_payload": {"topic": "delivery"},
                }
            )
        )

    assert out["status"] == "ok"
    assert captured["hermes_run_id"] == "hermes-run-tool-1"
    assert captured["error_prefix"] == "knowledge_candidate"


def test_typed_knowledge_proposal_tools_create_approval_candidates():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        knowledge_propose_catalog_update,
        knowledge_propose_faq_update,
        knowledge_propose_policy_update,
        knowledge_propose_rule,
    )

    ctx = _ctx(hermes_run_id="hermes-run-typed-proposals")
    captured = []

    def _fake_candidate(**kwargs):
        captured.append(kwargs)

        async def _coro():
            return {"candidate": {"candidate_id": f"knowledge_candidate:{len(captured)}"}}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        return {"candidate": {"candidate_id": f"knowledge_candidate:{len(captured)}"}}

    payload = {
        "source_id": "knowledge_source:typed",
        "proposed_payload": {"title": "Starter update"},
    }
    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_candidate_async", _fake_candidate),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        catalog = json.loads(knowledge_propose_catalog_update(payload))
        policy = json.loads(knowledge_propose_policy_update(payload))
        faq = json.loads(knowledge_propose_faq_update(payload))
        rule = json.loads(knowledge_propose_rule(payload))

    assert [out["status"] for out in (catalog, policy, faq, rule)] == ["ok", "ok", "ok", "ok"]
    assert [item["proposed_kind"] for item in captured] == [
        "catalog_product",
        "policy",
        "faq",
        "rule",
    ]
    assert all(item["hermes_run_id"] == "hermes-run-typed-proposals" for item in captured)


def test_knowledge_extract_candidates_creates_multiple_approval_proposals():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_extract_candidates

    ctx = _ctx(hermes_run_id="hermes-run-extract")
    captured = []

    def _fake_candidate(**kwargs):
        captured.append(kwargs)

        async def _coro():
            return {"candidate": {"candidate_id": f"knowledge_candidate:{len(captured)}"}}

        return _coro()

    def _fake_run(_ctx, coro, *, error_prefix):
        coro.close()
        return {"candidate": {"candidate_id": f"knowledge_candidate:{len(captured)}"}}

    with (
        use_tool_context(ctx),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._knowledge_candidate_async", _fake_candidate),
        patch("app.modules.agent_runtime_v2.hermes.oqim_tools._run_knowledge_coro", _fake_run),
    ):
        out = json.loads(
            knowledge_extract_candidates(
                {
                    "source_id": "knowledge_source:bulk",
                    "candidates": [
                        {
                            "proposed_kind": "policy",
                            "proposed_payload": {"topic": "delivery"},
                        },
                        {
                            "proposed_kind": "faq",
                            "proposed_payload": {"question": "Narxi qancha?"},
                        },
                    ],
                }
            )
        )

    assert out["status"] == "ok"
    assert len(out["proposals"]) == 2
    assert [item["proposed_kind"] for item in captured] == ["policy", "faq"]
    assert captured[0]["idempotency_key"] != captured[1]["idempotency_key"]
    assert all(item["hermes_run_id"] == "hermes-run-extract" for item in captured)


def test_knowledge_save_no_loop_reports_degraded_not_fake_item():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_save_note

    ctx = _ctx(loop=None)
    with use_tool_context(ctx):
        out = json.loads(knowledge_save_note({"title": "Call notes", "body_text": "Follow up tomorrow"}))

    assert out["status"] == "degraded"
    assert out["error"] == "no_loop"
    assert "item" not in out
    assert ctx.tool_errors == ["knowledge_save_note:no_loop"]


def test_knowledge_propose_candidate_requires_structured_payload():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import knowledge_propose_candidate

    ctx = _ctx()
    with use_tool_context(ctx):
        out = json.loads(
            knowledge_propose_candidate(
                {
                    "source_id": "knowledge_source:1",
                    "proposed_kind": "policy",
                    "proposed_payload": "not structured",
                }
            )
        )

    assert out["status"] == "empty"


def test_register_oqim_tools_does_not_expose_crm_context():
    """Slice 5: crm.context is retired (stage/deal_value pre-injected into
    conversation_state["crm"]); the tool is no longer registered."""
    from tools.registry import registry
    register_oqim_tools()
    names = set(registry.get_tool_names_for_toolset("oqim"))
    assert "crm.context" not in names


def test_crm_context_handler_is_removed():
    import app.modules.agent_runtime_v2.hermes.oqim_tools as oqim_tools

    assert not hasattr(oqim_tools, "crm_context")
    assert not hasattr(oqim_tools, "_crm_context_async")
    assert not hasattr(oqim_tools, "_CRM_CONTEXT_SCHEMA")


def test_conversation_record_stashes_record_payload():
    from app.modules.agent_runtime_v2.hermes import tool_context as tc
    from app.modules.agent_runtime_v2.hermes.oqim_tools import conversation_record

    ctx = _ctx(conversation_id=7, agent_session_id=3)
    token = tc.current_tool_context.set(ctx)
    try:
        out = conversation_record(
            {
                "stage": "quoted",
                "deal_value": 9790000,
                "currency": "UZS",
                "items": [{"name": "HR Management kursi", "quantity": 1}],
                "customer": {"name": "Mirzosharif", "phone": "+998901635207"},
                "handoff": {"needed": True, "kind": "lead", "reason": "shared phone"},
            }
        )
    finally:
        tc.current_tool_context.reset(token)

    assert json.loads(out)["status"] == "ok"
    assert ctx.record_payload is not None
    assert ctx.record_payload["deal_value"] == 9790000
    assert ctx.record_payload["stage"] == "quoted"
    assert ctx.record_payload["handoff"]["kind"] == "lead"


def test_conversation_record_requires_stage():
    from app.modules.agent_runtime_v2.hermes import tool_context as tc
    from app.modules.agent_runtime_v2.hermes.oqim_tools import conversation_record

    ctx = _ctx(conversation_id=7, agent_session_id=3)
    token = tc.current_tool_context.set(ctx)
    try:
        out = conversation_record({"deal_value": 100})
    finally:
        tc.current_tool_context.reset(token)
    assert json.loads(out)["status"] == "empty"
    assert ctx.record_payload is None


def test_conversation_record_no_context_returns_error():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import conversation_record

    assert json.loads(conversation_record({"stage": "new"}))["status"] == "error"


def test_conversation_record_schema_has_pipeline_key():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import _CONVERSATION_RECORD_SCHEMA

    props = _CONVERSATION_RECORD_SCHEMA["parameters"]["properties"]
    assert props["pipeline_key"] == {"type": "string"}
    assert "pipeline_key" not in _CONVERSATION_RECORD_SCHEMA["parameters"]["required"]


def test_conversation_record_carries_custom_fields_and_tags():
    """S4: conversation.record carries logical custom_fields [{key,value}] + tags
    [key] onto ctx.record_payload (resolved to provider ids later in the records
    pass)."""
    from app.modules.agent_runtime_v2.hermes import tool_context as tc
    from app.modules.agent_runtime_v2.hermes.oqim_tools import conversation_record

    ctx = _ctx(conversation_id=7, agent_session_id=3)
    token = tc.current_tool_context.set(ctx)
    try:
        out = json.loads(conversation_record({
            "stage": "qualified",
            "custom_fields": [{"key": "budget", "value": "5000000"}],
            "tags": ["vip"],
        }))
    finally:
        tc.current_tool_context.reset(token)
    assert out["status"] == "ok"
    assert out["record"]["custom_fields"] == [{"key": "budget", "value": "5000000"}]
    assert out["record"]["tags"] == ["vip"]
    assert ctx.record_payload["custom_fields"] == [{"key": "budget", "value": "5000000"}]
    assert ctx.record_payload["tags"] == ["vip"]


def test_conversation_record_schema_has_custom_fields_and_tags():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import _CONVERSATION_RECORD_SCHEMA

    props = _CONVERSATION_RECORD_SCHEMA["parameters"]["properties"]
    # custom_fields items are TYPED (key+value) so function-calling has slots to
    # fill — an untyped {type:object} made the model emit empty {} objects.
    assert props["custom_fields"]["type"] == "array"
    assert props["custom_fields"]["items"]["type"] == "object"
    assert set(props["custom_fields"]["items"]["properties"]) == {"key", "value"}
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}


def test_conversation_record_schema_custom_fields_items_are_typed():
    """The model emitted empty {} objects (live 2026-06-17) because the
    custom_fields items schema had no properties. The items must declare
    key+value string properties + required, so function-calling has slots to
    fill."""
    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        _CONVERSATION_RECORD_SCHEMA,
    )

    cf = _CONVERSATION_RECORD_SCHEMA["parameters"]["properties"]["custom_fields"]
    items = cf["items"]
    assert items["properties"]["key"]["type"] == "string"
    assert items["properties"]["value"]["type"] == "string"
    assert set(items["required"]) == {"key", "value"}


async def test_ask_async_counts_customers_and_is_workspace_scoped(
    db_session, workspace, workspace_b
):
    from app.models.customer import Customer
    from app.modules.agent_runtime_v2.hermes.oqim_tools import _ask_async

    db_session.add_all(
        [
            Customer(workspace_id=workspace.id, display_name="A"),
            Customer(workspace_id=workspace.id, display_name="B"),
            Customer(workspace_id=workspace_b.id, display_name="other"),
        ]
    )
    await db_session.flush()

    out = await _ask_async(workspace_id=workspace.id, metric="customers", session=db_session)
    assert out == {"status": "ok", "metric": "customers", "count": 2}

    convos = await _ask_async(
        workspace_id=workspace.id, metric="conversations", session=db_session
    )
    assert convos["status"] == "ok"
    assert convos["metric"] == "conversations"
    assert convos["count"] == 0


def test_ask_blocks_unknown_metric():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import ask

    with use_tool_context(_ctx()):
        out = json.loads(ask({"metric": "revenue"}))
    assert out["status"] == "blocked"
    assert out["reason"] == "invalid_metric"


def test_ask_without_active_context_errors():
    from app.modules.agent_runtime_v2.hermes.oqim_tools import ask

    out = json.loads(ask({"metric": "customers"}))
    assert out["status"] == "error"
