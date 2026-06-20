"""Central LLM model policy for OQIM runtime.

Keep model choice boring and auditable:
- Flash for customer-facing seller replies and semantic safety review.
- Flash-Lite for high-volume control, extraction, onboarding, and reflex work.
- Pro is not a default runtime fallback; add it only as an explicit opt-in lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ChainItem = tuple[str, str]

MODEL_GEMINI_35_FLASH = "gemini-3.5-flash"  # GA 2026-05-19; built for fast agentic loops
MODEL_GEMINI_3_FLASH = "gemini-3-flash-preview"
MODEL_GEMINI_31_FLASH_LITE = "gemini-3.1-flash-lite-preview"
MODEL_GEMINI_31_FLASH_LITE_GA = "gemini-3.1-flash-lite"  # GA: 0.8-1.5s, $0.25/M in — benched 2026-06-09 vs 3.5-flash
MODEL_GEMINI_31_PRO_PREVIEW = "gemini-3.1-pro-preview"

LLM_POLICY_OVERRIDES_KEY_PREFIX = "llm_policy_overrides"


@dataclass(frozen=True)
class LLMModelInfo:
    id: str
    label: str
    lane: str
    provider: str = "gemini"
    description: str = ""


@dataclass(frozen=True)
class LLMTaskPolicy:
    key: str
    label: str
    model: str
    lane: str
    description: str
    allow_override: bool = True
    fallback_model: str | None = None
    # Gemini thinking_level for this task (None = caller's default). Per the
    # Gemini 3.x docs, "minimal" is Flash-Lite's own default and the
    # chat/latency-optimized setting; "low" suits simple instruction following.
    thinking_level: str | None = None
    # Gemini sampling temperature for this task (None = caller's default, 1.0).
    # Lower = more deterministic / less invented content. Per Google's Gemini 3
    # prompting guidance, low temperature sharply reduces hallucination on
    # factual tasks; set it only where the task must stay grounded.
    temperature: float | None = None

    @property
    def chain(self) -> list[ChainItem]:
        chain: list[ChainItem] = [("gemini", self.model)]
        if self.fallback_model:
            chain.append(("gemini", self.fallback_model))
        return chain


MODEL_REGISTRY: dict[str, LLMModelInfo] = {
    MODEL_GEMINI_35_FLASH: LLMModelInfo(
        id=MODEL_GEMINI_35_FLASH,
        label="Gemini 3.5 Flash",
        lane="quality",
        description="GA agentic-loop model (2026-05): faster/cheaper than 3 Flash for customer-facing seller replies.",
    ),
    MODEL_GEMINI_3_FLASH: LLMModelInfo(
        id=MODEL_GEMINI_3_FLASH,
        label="Gemini 3 Flash",
        lane="quality",
        description="Balanced quality/latency model for seller replies, multimodal reasoning, and semantic review.",
    ),
    MODEL_GEMINI_31_FLASH_LITE: LLMModelInfo(
        id=MODEL_GEMINI_31_FLASH_LITE,
        label="Gemini 3.1 Flash-Lite",
        lane="reflex",
        description="Fast, cost-efficient model for high-volume JSON control, extraction, classification, and onboarding tasks.",
    ),
    MODEL_GEMINI_31_PRO_PREVIEW: LLMModelInfo(
        id=MODEL_GEMINI_31_PRO_PREVIEW,
        label="Gemini 3.1 Pro Preview",
        lane="deep",
        description="Explicit opt-in lane for expensive BI investigation, hard verifier, and complex reasoning work.",
    ),
}


TASK_POLICIES: dict[str, LLMTaskPolicy] = {
    "agent_turn_generation": LLMTaskPolicy(
        key="agent_turn_generation",
        label="Agent turn generation",
        model=MODEL_GEMINI_3_FLASH,
        fallback_model=MODEL_GEMINI_35_FLASH,
        lane="quality",
        thinking_level="medium",
        # Pilot hardening (2026-06-18): drop the customer-facing seller reply from
        # Gemini's default 1.0 to 0.3 to cut confabulation (the bio->priced-service
        # class). 0.3 stays just above fully-deterministic so warm/varied phrasing
        # and the diagnose->close flow survive without lapsing into repetition.
        temperature=0.3,
        description=(
            "Customer-facing reply text. 3 Flash primary (2026-06-13): the "
            "consultative seller flow needs real judgment (when value is "
            "established, when to deflect price) and resilience to the "
            "output->input replay flaw — Flash-Lite imitated its own prior "
            "holding replies over the prompt instructions, perpetuating "
            "'operator busy' loops. 3.5 Flash in-chain fallback. Thinking "
            "'medium' (2026-06-13, owner call: latency is acceptable): the "
            "A-class diagnose->qualify->close flow benefits from deeper "
            "per-turn judgment (which diagnostic step next, when value is "
            "established, when to close), and more reasoning helps the model "
            "catch its own holding-reply repetition. Still drives the "
            "multi-step tool loop (lead + react + confirm) fine."
        ),
    ),
    "agent_turn_rewrite": LLMTaskPolicy(
        key="agent_turn_rewrite",
        label="Agent turn rewrite",
        model=MODEL_GEMINI_3_FLASH,
        lane="quality",
        description="Customer-facing rewrite after quality review or seller correction.",
    ),
    "agent_turn_finalize": LLMTaskPolicy(
        key="agent_turn_finalize",
        label="Agent turn finalize",
        model=MODEL_GEMINI_3_FLASH,
        lane="quality",
        description="Final customer-facing wording pass.",
    ),
    "agent_turn_review": LLMTaskPolicy(
        key="agent_turn_review",
        label="Agent turn review",
        model=MODEL_GEMINI_3_FLASH,
        lane="quality",
        description="Semantic review for hallucination, payment claims, tone, and business risk.",
    ),
    "vision_json_extraction": LLMTaskPolicy(
        key="vision_json_extraction",
        label="Vision/media semantics",
        model=MODEL_GEMINI_3_FLASH,
        lane="quality",
        description="Image/video-aware semantic extraction when media can affect the answer.",
    ),
    "agent_turn_planner": LLMTaskPolicy(
        key="agent_turn_planner",
        label="Agent turn planner",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Small structured plan before drafting.",
    ),
    "agent_turn_choose": LLMTaskPolicy(
        key="agent_turn_choose",
        label="Agent turn chooser",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Small structured choice between original and rewritten candidate.",
    ),
    "structured_json": LLMTaskPolicy(
        key="structured_json",
        label="Generic structured JSON",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Default for bounded JSON-only extraction tasks.",
    ),
    "contact_classification": LLMTaskPolicy(
        key="contact_classification",
        label="Contact classification",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Onboarding/contact triage.",
    ),
    "batch_contact_classification": LLMTaskPolicy(
        key="batch_contact_classification",
        label="Batch contact classification",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Bulk onboarding/contact triage.",
    ),
    "chip_generation": LLMTaskPolicy(
        key="chip_generation",
        label="Action chips",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Small seller action suggestions.",
    ),
    "json_extraction": LLMTaskPolicy(
        key="json_extraction",
        label="JSON extraction",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Generic low-cost structured extraction.",
    ),
    "kb_extraction": LLMTaskPolicy(
        key="kb_extraction",
        label="Knowledge base extraction",
        model=MODEL_GEMINI_31_FLASH_LITE,
        lane="reflex",
        description="Knowledge base onboarding extraction.",
    ),
}

_OPERATION_ALIASES: dict[str, str] = {
    # The Hermes tool-loop reply IS the agent turn: same task policy
    # (model chain, workspace overrides, thinking level).
    "hermes_reply": "agent_turn_generation",
    "agent_turn_planner_retry": "agent_turn_planner",
    "agent_turn_planner_tagged": "agent_turn_planner",
    "agent_turn_review_retry": "agent_turn_review",
    "agent_turn_review_tagged": "agent_turn_review",
    "agent_turn_choose_retry": "agent_turn_choose",
    "agent_turn_choose_tagged": "agent_turn_choose",
}


def llm_policy_key(workspace_id: int) -> str:
    return f"{LLM_POLICY_OVERRIDES_KEY_PREFIX}:{workspace_id}"


def normalize_operation(operation: str | None) -> str:
    if not operation:
        return "structured_json"
    normalized = operation.strip()
    return _OPERATION_ALIASES.get(normalized, normalized)


def get_task_policy(operation: str | None) -> LLMTaskPolicy:
    key = normalize_operation(operation)
    return TASK_POLICIES.get(key, TASK_POLICIES["structured_json"])


def sanitize_llm_policy_overrides(raw: dict[str, Any] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if not isinstance(raw, dict):
        return overrides
    for key, value in raw.items():
        policy_key = normalize_operation(str(key))
        if policy_key not in TASK_POLICIES:
            continue
        if value is None:
            continue
        model = str(value).strip()
        if model in MODEL_REGISTRY and TASK_POLICIES[policy_key].allow_override:
            overrides[policy_key] = model
    return overrides


def resolve_chain_for_operation(
    *,
    operation: str | None,
    requested_chain: list[ChainItem],
    overrides: dict[str, str] | None = None,
) -> list[ChainItem]:
    if operation is None:
        return requested_chain
    policy_key = normalize_operation(operation)
    policy = TASK_POLICIES.get(policy_key)
    if policy is None:
        return requested_chain
    override_model = sanitize_llm_policy_overrides(overrides).get(policy.key)
    model = override_model or policy.model
    model_info = MODEL_REGISTRY.get(model)
    if model_info is None:
        return policy.chain
    return [(model_info.provider, model_info.id)]


def build_llm_policy_snapshot(
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    clean_overrides = sanitize_llm_policy_overrides(overrides)
    tasks = []
    for policy in TASK_POLICIES.values():
        model = clean_overrides.get(policy.key, policy.model)
        tasks.append(
            {
                "key": policy.key,
                "label": policy.label,
                "lane": policy.lane,
                "description": policy.description,
                "default_model": policy.model,
                "effective_model": model,
                "override_model": clean_overrides.get(policy.key),
                "allow_override": policy.allow_override,
            }
        )
    return {
        "models": [model.__dict__ for model in MODEL_REGISTRY.values()],
        "tasks": tasks,
        "overrides": clean_overrides,
    }


FLASH_CHAIN: list[ChainItem] = TASK_POLICIES["agent_turn_generation"].chain
FLASH_LITE_CHAIN: list[ChainItem] = TASK_POLICIES["structured_json"].chain
FLASH_LITE_GEMINI_CHAIN: list[ChainItem] = TASK_POLICIES["structured_json"].chain
CONTROL_CHAIN: list[ChainItem] = TASK_POLICIES["agent_turn_planner"].chain

# Owner Agent (#455, owner directive 2026-06-18): gemini-3.5-flash PRIMARY (GA,
# "built for fast agentic loops"), 3-flash-preview as the in-chain fallback —
# distinct from FLASH_CHAIN, where 3.5-flash is only the fallback.
OWNER_CHAIN: list[ChainItem] = [
    ("gemini", MODEL_GEMINI_35_FLASH),
    ("gemini", MODEL_GEMINI_3_FLASH),
]
