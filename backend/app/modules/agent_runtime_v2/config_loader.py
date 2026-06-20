"""Load an agent's send policy + rendered documents for the Reply Agent runtime (P5a)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.agent_runtime_context.service import _agent_kind
from app.modules.agent_runtime_v2.context_config import (
    CONTEXT_WINDOW_DEFAULT,
    resolve_context_window,
)
from app.modules.agent_runtime_v2.reply_runtime import (
    load_voice_preset_asset,
    load_workspace_seller_playbook,
    render_voice_block,
)
from app.modules.agent_runtime_v2.voice_presets import resolve_voice
from app.modules.brain.agent_document import AgentDocumentBuilderService


@dataclass(frozen=True)
class AgentConfig:
    agent_id: int
    workspace_id: int
    name: str
    trust_mode: str
    auto_send_threshold: float
    agent_md: str
    agent_kind: str = "custom_agent"
    talking_overrides: dict | None = None
    # The workspace's custom selling method, or None to use the managed default.
    seller_playbook_override: str | None = None
    # Rendered <voice> personality block, or None when the agent has no voice config.
    voice_block: str | None = None
    # Per-agent Hermes context window in tokens (gemini's true 1M by default).
    context_window: int = CONTEXT_WINDOW_DEFAULT
    # Kill switch for the post-reply forced-set_state commercial finalization pass.
    # Default ON (back-compat); a workspace can disable it per-agent via
    # ``channel_config['crm']['commercial_finalization_enabled'] = false``.
    commercial_finalization_enabled: bool = True
    # Per-workspace lead-routing config from channel_config['crm']['routing'], or
    # None when unset (no routing = default-pipeline-only). Consumed by the records
    # pass (S3). Shape: {"pipelines": {key: pipeline_id}, "default": key|None,
    # "instructions": str}.
    crm_routing: dict | None = None
    # S4 owner-blessed CRM custom fields / tag vocabulary / do-not-contact field,
    # each from channel_config['crm'][...]; None when absent (no field SEE/WRITE).
    crm_fields: dict | None = None
    crm_tags: dict | None = None
    crm_dnc: dict | None = None


class AgentConfigLoader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, *, workspace_id: int, agent_id: int) -> AgentConfig:
        agent = await self.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise LookupError(f"agent {agent_id} not found in workspace {workspace_id}")
        rendered = await AgentDocumentBuilderService(self.session).render_current(
            workspace_id=workspace_id, agent_id=agent_id
        )
        seller_playbook_override = await load_workspace_seller_playbook(
            self.session, workspace_id=workspace_id
        )
        agent_kind = _agent_kind(agent)
        resolved_voice = resolve_voice(agent.channel_config or {}, agent_kind)
        context_window = resolve_context_window(agent.channel_config or {})
        commercial_finalization_enabled = resolve_commercial_finalization_enabled(
            agent.channel_config or {}
        )
        crm_routing = resolve_crm_routing(agent.channel_config or {})
        crm_fields = resolve_crm_fields(agent.channel_config or {})
        crm_tags = resolve_crm_tags(agent.channel_config or {})
        crm_dnc = resolve_crm_dnc(agent.channel_config or {})
        voice_block = None
        if resolved_voice.preset_asset_id:
            voice_block = render_voice_block(
                load_voice_preset_asset(resolved_voice.preset_asset_id),
                resolved_voice.verbosity,
                resolved_voice.additional_instructions,
            )
        return AgentConfig(
            agent_id=agent.id,
            workspace_id=agent.workspace_id,
            name=agent.name,
            trust_mode=agent.trust_mode,
            auto_send_threshold=agent.auto_send_threshold,
            agent_md=rendered.markdown,
            agent_kind=agent_kind,
            talking_overrides=resolved_voice.talking_overrides,
            seller_playbook_override=seller_playbook_override,
            voice_block=voice_block,
            context_window=context_window,
            commercial_finalization_enabled=commercial_finalization_enabled,
            crm_routing=crm_routing,
            crm_fields=crm_fields,
            crm_tags=crm_tags,
            crm_dnc=crm_dnc,
        )


def resolve_commercial_finalization_enabled(channel_config: dict | None) -> bool:
    """Per-agent kill switch for the forced commercial-finalization pass, read
    from ``channel_config['crm']['commercial_finalization_enabled']``. Defaults
    True (back-compat) on absent/invalid input; only an explicit ``False``
    disables it."""
    if not isinstance(channel_config, dict):
        return True
    crm_cfg = channel_config.get("crm")
    if not isinstance(crm_cfg, dict):
        return True
    # Only an explicit ``False`` disables it; absent/any-other value stays ON.
    return crm_cfg.get("commercial_finalization_enabled") is not False


def resolve_crm_routing(channel_config: dict | None) -> dict | None:
    """The per-workspace lead-routing config from
    ``channel_config['crm']['routing']``. Returns a normalized dict
    (``pipelines`` logical-key -> pipeline-id strings, ``default`` key, plain
    ``instructions``) or ``None`` when absent/empty (no routing = default pipeline
    only)."""
    if not isinstance(channel_config, dict):
        return None
    crm_cfg = channel_config.get("crm")
    if not isinstance(crm_cfg, dict):
        return None
    routing = crm_cfg.get("routing")
    if not isinstance(routing, dict):
        return None
    pipelines = routing.get("pipelines")
    if not isinstance(pipelines, dict) or not pipelines:
        return None
    return {
        "pipelines": {str(k): str(v) for k, v in pipelines.items()},
        "default": str(routing["default"]) if routing.get("default") else None,
        "instructions": str(routing.get("instructions") or ""),
    }


def resolve_crm_fields(channel_config: dict | None) -> dict | None:
    """``channel_config['crm']['fields']`` (logical key -> {field_id,label,type,
    inject,write,enum_map}); None when absent/empty (no field SEE/WRITE)."""
    if not isinstance(channel_config, dict):
        return None
    fields = ((channel_config.get("crm") or {}).get("fields")) or {}
    return fields if isinstance(fields, dict) and fields else None


def resolve_crm_tags(channel_config: dict | None) -> dict | None:
    """``channel_config['crm']['tags']`` (``vocabulary`` + ``namespace``); None
    when absent or with no vocabulary (no tag writes)."""
    if not isinstance(channel_config, dict):
        return None
    tags = ((channel_config.get("crm") or {}).get("tags")) or {}
    return tags if isinstance(tags, dict) and tags.get("vocabulary") else None


def resolve_crm_dnc(channel_config: dict | None) -> dict | None:
    """``channel_config['crm']['do_not_contact']`` (``field_id`` + ``on_value``);
    None when absent or without a field_id (no DNC mapping)."""
    if not isinstance(channel_config, dict):
        return None
    dnc = ((channel_config.get("crm") or {}).get("do_not_contact")) or {}
    return dnc if isinstance(dnc, dict) and dnc.get("field_id") is not None else None
