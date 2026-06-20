"""OQIM model route policy.

This module names stable route keys for OQIM prompt assets. Feature code
uses route keys; provider model IDs stay here so model changes are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.brain.llm_policy import (
    MODEL_GEMINI_31_PRO_PREVIEW,
    MODEL_GEMINI_3_FLASH,
    MODEL_GEMINI_31_FLASH_LITE,
)

MODEL_GEMINI_EMBEDDING_2 = "gemini-embedding-2"

ResponseMode = Literal["json_schema", "embedding"]


class ModelRouteNotFoundError(KeyError):
    """Raised when a prompt references an unknown model route key."""


@dataclass(frozen=True)
class ModelRoute:
    """Stable model route used by prompt metadata and traces."""

    key: str
    provider: str
    model_id: str
    response_mode: ResponseMode
    default_thinking_level: str | None = None
    fallback_model_ids: tuple[str, ...] = ()
    description: str = ""

    @property
    def chain(self) -> list[tuple[str, str]]:
        """Return the route as the existing LLM chain shape."""
        if self.response_mode == "embedding":
            raise ValueError(f"Embedding route {self.key!r} cannot be used as a generation chain")
        return [
            (self.provider, model_id)
            for model_id in (self.model_id, *self.fallback_model_ids)
        ]


MODEL_ROUTES: dict[str, ModelRoute] = {
    "structured_fast": ModelRoute(
        key="structured_fast",
        provider="gemini",
        model_id=MODEL_GEMINI_31_FLASH_LITE,
        response_mode="json_schema",
        default_thinking_level="minimal",
        fallback_model_ids=(MODEL_GEMINI_3_FLASH,),
        description="High-volume retrieval planning, reply planning, extraction, and JSON control tasks.",
    ),
    "structured_judge": ModelRoute(
        key="structured_judge",
        provider="gemini",
        model_id=MODEL_GEMINI_31_FLASH_LITE,
        response_mode="json_schema",
        default_thinking_level="low",
        description="First-pass groundedness, prompt-injection, media, and seller-voice judges.",
    ),
    "composition_rich": ModelRoute(
        key="composition_rich",
        provider="gemini",
        model_id=MODEL_GEMINI_3_FLASH,
        response_mode="json_schema",
        default_thinking_level="low",
        description="Normal customer-facing seller replies.",
    ),
    "composition_complex": ModelRoute(
        key="composition_complex",
        provider="gemini",
        model_id=MODEL_GEMINI_3_FLASH,
        response_mode="json_schema",
        default_thinking_level="medium",
        description="Multimodal, objection, payment/order, complaint, or otherwise high-risk turns.",
    ),
    "media_rich": ModelRoute(
        key="media_rich",
        provider="gemini",
        model_id=MODEL_GEMINI_3_FLASH,
        response_mode="json_schema",
        default_thinking_level="low",
        description="Media semantic extraction for image, video, sticker, GIF, and richer visual context.",
    ),
    "deep_reasoning": ModelRoute(
        key="deep_reasoning",
        provider="gemini",
        model_id=MODEL_GEMINI_31_PRO_PREVIEW,
        response_mode="json_schema",
        default_thinking_level="high",
        description="Expensive opt-in lane for BI investigation, hard policy review, and complex agentic reasoning.",
    ),
    "embedding_multimodal_primary": ModelRoute(
        key="embedding_multimodal_primary",
        provider="gemini",
        model_id=MODEL_GEMINI_EMBEDDING_2,
        response_mode="embedding",
        description="Multimodal retrieval embeddings for text, image, video, audio, and documents.",
    ),
}


def get_model_route(key: str) -> ModelRoute:
    """Return a configured model route by stable key."""
    try:
        return MODEL_ROUTES[key]
    except KeyError as exc:
        raise ModelRouteNotFoundError(key) from exc


def list_model_routes() -> list[ModelRoute]:
    """Return model routes in deterministic order for admin/debug surfaces."""
    return [MODEL_ROUTES[key] for key in sorted(MODEL_ROUTES)]
