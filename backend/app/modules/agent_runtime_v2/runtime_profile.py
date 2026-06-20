from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.context_config import CONTEXT_WINDOW_DEFAULT

ProfileKind = Literal["agent"]
ExecutionMode = Literal["interactive", "action", "setup", "record"]

# Pilot hardening (2026-06-18): the interactive seller answers ONLY from AGENT.md
# (the curated, owner-reviewed business truth). RAG/knowledge retrieval is removed
# from the talk loop: prod run 182 showed knowledge_search returning a raw speaker
# bio, which the model promoted into a sellable "consulting service" and an
# invented price comparison. A retrieved bio is input material, not an offering —
# so the tool that surfaces it has no place on the customer-facing path. AGENT.md
# already carries every fact this pilot sells, and hermes_reply.md instructs the
# model to answer from it directly. The action/setup lanes keep retrieval for
# deliberate, off-customer catalog work.
_INTERACTIVE_AGENT_TOOLS: tuple[str, ...] = (
    "talk.send_msgs",
    "talk.send_media",
    "talk.send_reaction",
)
# Slice 5: crm.context retired (deal_value/stage are pre-injected into
# conversation_state["crm"] — no LLM round-trip); commerce.create_order moved
# out of interactive (the records pass captures items post-reply). Both stay in
# _ACTION_AGENT_TOOLS / _SETUP_AGENT_TOOLS where the action/setup agents use
# them deliberately.
# Phase 2 — the records pass: grant ONLY conversation.record so the single-tool
# grant forces it under Gemini mode=ANY. Runs AFTER the reply (post-commit, off
# the customer path), fed the turn transcript; records the agent's own quoted
# deal_value/stage/items. NO talk tools, no retrieval.
_RECORD_AGENT_TOOLS: tuple[str, ...] = ("conversation.record",)
_ACTION_AGENT_TOOLS: tuple[str, ...] = (
    "conversation.set_state",
    "conversation.record_intelligence",
    "work.create_task",
    "owner.notify",
    "commerce.create_order",
    "commerce.create_checkout_intent",
    "knowledge_search",
    "knowledge_search_catalog",
    "knowledge_search_media",
    "knowledge_search_chat_memory",
    "knowledge_get_item",
    "knowledge_explain_sources",
    "knowledge_create_source_doc",
    "knowledge_extract_candidates",
    "knowledge_propose_catalog_update",
    "knowledge_propose_candidate",
    "knowledge_propose_rule",
    "knowledge_propose_policy_update",
    "knowledge_propose_faq_update",
)
_SETUP_AGENT_TOOLS: tuple[str, ...] = (
    "ask",
    "conversation.set_state",
    "conversation.record_intelligence",
    "work.create_task",
    "owner.notify",
    "owner.edit_doc",
    "media.store",
    "media.list",
    "knowledge_search",
    "knowledge_search_chat_memory",
    "knowledge_search_catalog",
    "knowledge_search_media",
    "knowledge_get_item",
    "knowledge_explain_sources",
    "knowledge_save_note",
    "knowledge_save_script",
    "knowledge_create_source_doc",
    "knowledge_extract_candidates",
    "knowledge_propose_candidate",
    "knowledge_propose_catalog_update",
    "knowledge_propose_rule",
    "knowledge_propose_policy_update",
    "knowledge_propose_faq_update",
)


class _ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HermesSettings(_ProfileModel):
    model: str = "gemini"
    chain: str = "FLASH_CHAIN"  # llm_policy attribute name resolved by the shim
    enabled_toolsets: tuple[str, ...] = ("oqim",)
    max_iterations: int = 4
    skip_memory: bool = False
    skip_context_files: bool = True
    save_trajectories: bool = False
    # Gemini's true window by default; per-agent override flows from AgentConfig.
    # Injected into Hermes by the context-length vendor patch. Also sets where the
    # compactor fires (threshold_tokens = max(context_length * 0.50, 64K)).
    context_length: int = CONTEXT_WINDOW_DEFAULT


class RetrievalPolicy(_ProfileModel):
    enable_contextual_rank: bool = False
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = False
    enable_rerank: bool = False
    enable_eager_grounding: bool = False
    max_in_loop_catalog_calls: int = 2


class ActionPolicy(_ProfileModel):
    trust_mode: str
    auto_send_threshold: float
    faithfulness_required: bool = True


class RuntimeProfile(_ProfileModel):
    schema_version: Literal["runtime_profile.v1"] = "runtime_profile.v1"
    profile_kind: ProfileKind
    execution_mode: ExecutionMode
    workspace_id: int
    agent_id: int
    agent_kind: str
    profile_hash: str
    hermes_settings: HermesSettings
    allowed_tool_names: tuple[str, ...]
    retrieval_policy: RetrievalPolicy
    action_policy: ActionPolicy


class RuntimeProfileCompiler:
    """Compile one generic Hermes agent runtime profile.

    Agent roles live in AgentConfig and Agent Materials. This compiler only
    decides the Hermes execution mode and OQIM tool grants for the run.
    """

    def compile_agent(
        self,
        *,
        config: AgentConfig,
        agent_kind: str,
        execution_mode: ExecutionMode | None = None,
    ) -> RuntimeProfile:
        return self.compile_profile(
            config=config,
            agent_kind=agent_kind,
            profile_kind="agent",
            execution_mode=execution_mode or _default_execution_mode(agent_kind),
        )

    def compile_profile(
        self,
        *,
        config: AgentConfig,
        agent_kind: str,
        profile_kind: ProfileKind = "agent",
        execution_mode: ExecutionMode | None = None,
    ) -> RuntimeProfile:
        if profile_kind != "agent":
            raise ValueError(
                "role-specific runtime profiles are retired; compile the generic agent profile"
            )
        mode = execution_mode or _default_execution_mode(agent_kind)
        hermes_settings, retrieval_policy, allowed_tools, action_policy = self._policies_for(
            config=config,
            execution_mode=mode,
        )
        return _profile(
            config=config,
            agent_kind=agent_kind,
            profile_kind=profile_kind,
            execution_mode=mode,
            hermes_settings=hermes_settings,
            allowed_tool_names=allowed_tools,
            retrieval_policy=retrieval_policy,
            action_policy=action_policy,
        )

    def _policies_for(
        self,
        *,
        config: AgentConfig,
        execution_mode: ExecutionMode,
    ) -> tuple[HermesSettings, RetrievalPolicy, tuple[str, ...], ActionPolicy]:
        if execution_mode == "interactive":
            return (
                HermesSettings(
                    max_iterations=4,
                    skip_memory=False,
                    skip_context_files=True,
                    save_trajectories=False,
                    context_length=config.context_window,
                ),
                RetrievalPolicy(
                    enable_contextual_rank=False,
                    enable_query_rewrite=False,
                    enable_agentic_search=False,
                    enable_rerank=False,
                    enable_eager_grounding=False,
                    # No retrieval tools on the interactive lane (AGENT.md is the
                    # sole truth) — nothing left to bound.
                    max_in_loop_catalog_calls=0,
                ),
                _INTERACTIVE_AGENT_TOOLS,
                _action_policy(config, faithfulness_required=False),
            )
        if execution_mode == "action":
            return (
                HermesSettings(
                    max_iterations=8,
                    skip_memory=False,
                    skip_context_files=True,
                    save_trajectories=True,
                    context_length=config.context_window,
                ),
                RetrievalPolicy(
                    enable_contextual_rank=True,
                    enable_query_rewrite=True,
                    enable_agentic_search=True,
                    enable_rerank=True,
                    enable_eager_grounding=True,
                    max_in_loop_catalog_calls=5,
                ),
                _ACTION_AGENT_TOOLS,
                _action_policy(config, faithfulness_required=False),
            )
        if execution_mode == "record":
            # Single forced iteration: the agent records its own commercial state
            # by being forced to call conversation.record after the reply
            # (post-commit). No retrieval, no talk, faithfulness off; the turn
            # transcript + the same grounding the interactive turn already had
            # ride in, so the quoted deal_value is recorded from what was
            # actually said, not invented.
            return (
                HermesSettings(
                    max_iterations=1,
                    skip_memory=True,
                    skip_context_files=True,
                    save_trajectories=False,
                    context_length=config.context_window,
                ),
                RetrievalPolicy(
                    enable_contextual_rank=False,
                    enable_query_rewrite=False,
                    enable_agentic_search=False,
                    enable_rerank=False,
                    enable_eager_grounding=False,
                    max_in_loop_catalog_calls=0,
                ),
                _RECORD_AGENT_TOOLS,
                _action_policy(config, faithfulness_required=False),
            )
        return (
            HermesSettings(
                max_iterations=8,
                skip_memory=False,
                skip_context_files=False,
                save_trajectories=True,
                context_length=config.context_window,
                # Run-model X (spike #439): the owner/setup plane is Hermes-NATIVE.
                # Grant the "skills" toolset (skills_list / skill_view) so Hermes
                # injects the <available_skills> index from the workspace's
                # per-workspace HERMES_HOME and the agent can load file-drop
                # SKILL.md skills on demand. Owner-plane only — never the seller.
                model="gemini-3.5-flash",
                chain="OWNER_CHAIN",
                enabled_toolsets=("oqim", "skills"),
            ),
            RetrievalPolicy(
                enable_contextual_rank=True,
                enable_query_rewrite=True,
                enable_agentic_search=True,
                enable_rerank=True,
                enable_eager_grounding=True,
                max_in_loop_catalog_calls=4,
            ),
            _SETUP_AGENT_TOOLS,
            _action_policy(config, faithfulness_required=False),
        )


def _default_execution_mode(agent_kind: str) -> ExecutionMode:
    # Owner Agent (and the legacy setup_agent / setup kinds) run the Hermes-native
    # setup profile (owner tools + skills toolset). Everything else is interactive.
    if agent_kind in ("owner", "setup", "setup_agent"):
        return "setup"
    return "interactive"


def _action_policy(config: AgentConfig, *, faithfulness_required: bool = True) -> ActionPolicy:
    return ActionPolicy(
        trust_mode=config.trust_mode,
        auto_send_threshold=config.auto_send_threshold,
        faithfulness_required=faithfulness_required,
    )


def _profile(
    *,
    config: AgentConfig,
    agent_kind: str,
    profile_kind: ProfileKind,
    execution_mode: ExecutionMode,
    hermes_settings: HermesSettings,
    allowed_tool_names: tuple[str, ...],
    retrieval_policy: RetrievalPolicy,
    action_policy: ActionPolicy,
) -> RuntimeProfile:
    material = {
        "schema_version": "runtime_profile.v1",
        "profile_kind": profile_kind,
        "execution_mode": execution_mode,
        "workspace_id": config.workspace_id,
        "agent_id": config.agent_id,
        "agent_kind": agent_kind,
        "agent_md": config.agent_md,
        "hermes_settings": hermes_settings.model_dump(mode="json"),
        "allowed_tool_names": list(allowed_tool_names),
        "retrieval_policy": retrieval_policy.model_dump(mode="json"),
        "action_policy": action_policy.model_dump(mode="json"),
    }
    profile_hash = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return RuntimeProfile(
        profile_kind=profile_kind,
        execution_mode=execution_mode,
        workspace_id=config.workspace_id,
        agent_id=config.agent_id,
        agent_kind=agent_kind,
        profile_hash=profile_hash,
        hermes_settings=hermes_settings,
        allowed_tool_names=allowed_tool_names,
        retrieval_policy=retrieval_policy,
        action_policy=action_policy,
    )
