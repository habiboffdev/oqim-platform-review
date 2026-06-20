from __future__ import annotations

import json
from typing import Any

from app.brain.prompt_registry import PromptAsset, PromptRegistryError, get_prompt_registry

_GEMINI_MIN_CACHED_CONTENT_TOKENS = 1024
_APPROX_CHARS_PER_TOKEN = 4


def prompt_asset_payload(
    prompt_id: str,
    *,
    version: str,
    fallback_body: str | None = None,
) -> dict[str, Any]:
    """Return a traceable prompt payload for LLM Gateway inputs."""
    try:
        prompt = get_prompt_registry().load(prompt_id, version=version)
        return {
            "prompt_id": prompt.id,
            "version": prompt.version,
            "digest": prompt.digest,
            "cache_policy": prompt.cache_policy,
            "cache_key": _prompt_cache_key(
                prompt_id=prompt.id,
                version=prompt.version,
                cache_policy=prompt.cache_policy,
                digest=prompt.digest,
            ),
            "body": prompt.body.strip(),
            "registry_state": "loaded",
        }
    except PromptRegistryError:
        if fallback_body is None:
            raise
        return {
            "prompt_id": prompt_id,
            "version": version,
            "digest": None,
            "cache_policy": "no_cache",
            "cache_key": None,
            "body": fallback_body.strip(),
            "registry_state": "fallback",
        }


def _prompt_cache_key(
    *,
    prompt_id: str,
    version: str,
    cache_policy: str,
    digest: str,
) -> str | None:
    if cache_policy == "no_cache":
        return None
    return f"prompt:{prompt_id}:{version}:{cache_policy}:{digest}"


def prompt_cache_payload_for_asset(
    prompt: PromptAsset,
    *,
    cache_scope: str,
    stable_payload: dict[str, Any] | None = None,
    ttl_seconds: int = 3600,
) -> dict[str, Any] | None:
    """Build a Gemini cached-content payload for a managed prompt asset."""
    cache_key = _prompt_cache_key(
        prompt_id=prompt.id,
        version=prompt.version,
        cache_policy=prompt.cache_policy,
        digest=prompt.digest,
    )
    if not cache_key:
        return None
    prompt_metadata = {
        "prompt_id": prompt.id,
        "version": prompt.version,
        "digest": prompt.digest,
        "cache_policy": prompt.cache_policy,
        "cache_key": cache_key,
    }
    stable = stable_payload or {"prompt_asset": prompt_metadata}
    estimated_tokens = _estimate_cache_tokens(prompt=prompt, stable_payload=stable)
    cacheable = estimated_tokens >= _GEMINI_MIN_CACHED_CONTENT_TOKENS
    return {
        "schema_version": "llm_prompt_cache.v1",
        "provider_strategy": "gemini_cached_content" if cacheable else "none",
        "cacheable": cacheable,
        "skip_reason": None if cacheable else "gemini_min_cache_tokens",
        "estimated_tokens": estimated_tokens,
        "min_cache_tokens": _GEMINI_MIN_CACHED_CONTENT_TOKENS,
        "ttl_seconds": ttl_seconds,
        "prompt_asset": prompt_metadata,
        "runtime_context": {
            "cache_scope": cache_scope,
            "cache_key": cache_key,
            "material_hash": prompt.digest,
            "stable_payload": stable,
            "stable_payload_keys": sorted(stable.keys()),
            "dynamic_payload_keys": ["prompt"],
            "invalidation_refs": [
                f"prompt:{prompt.id}:{prompt.version}:{prompt.digest[:16]}",
            ],
        },
    }


def _estimate_cache_tokens(
    *,
    prompt: PromptAsset,
    stable_payload: dict[str, Any],
) -> int:
    material = json.dumps(
        {
            "prompt_body": prompt.body,
            "stable_payload": stable_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return max(1, len(material) // _APPROX_CHARS_PER_TOKEN)
