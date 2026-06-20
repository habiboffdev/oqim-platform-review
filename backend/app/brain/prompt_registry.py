"""Markdown prompt registry for OQIM LLM workflows.

The registry loads prompt assets by ID/version, validates frontmatter, and
returns a digest suitable for traces. It does not call any model provider.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.brain.model_policy import ModelRouteNotFoundError, get_model_route

DEFAULT_PROMPT_ROOT = Path(__file__).resolve().parent / "prompt_assets"
DEFAULT_PROMPT_MANIFEST = DEFAULT_PROMPT_ROOT / "registry.yaml"
REQUIRED_FRONTMATTER_FIELDS = {
    "id",
    "version",
    "status",
    "owner",
    "model_policy",
    "output_schema",
    "cache_policy",
}

ALLOWED_CACHE_POLICIES = {
    "stable_system_prompt",
    "no_cache",
}


class PromptRegistryError(RuntimeError):
    """Base class for prompt registry failures."""


class PromptNotFoundError(PromptRegistryError):
    """Raised when a prompt ID/version pair is not registered."""


class DuplicatePromptError(PromptRegistryError):
    """Raised when more than one file declares the same prompt ID/version."""


class PromptMetadataError(PromptRegistryError):
    """Raised when a prompt file has invalid frontmatter."""


@dataclass(frozen=True)
class PromptAsset:
    """Loaded markdown prompt with validated metadata."""

    id: str
    version: str
    status: str
    owner: str
    model_policy: str
    output_schema: str
    cache_policy: str
    body: str
    path: Path
    digest: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PromptManifestEntry:
    """One manifest row for prompt ownership, evals, and review tooling."""

    id: str
    version: str
    path: Path
    model_policy: str
    output_schema: str
    eval_suite: str
    cache_policy: str


@dataclass(frozen=True)
class PromptManifest:
    """Validated prompt registry manifest."""

    entries: tuple[PromptManifestEntry, ...]


class PromptRegistry:
    """Load versioned markdown prompts from a prompt asset root."""

    def __init__(self, *, root: Path = DEFAULT_PROMPT_ROOT) -> None:
        self.root = root
        self._index: dict[tuple[str, str], PromptAsset] | None = None

    def load(self, prompt_id: str, *, version: str) -> PromptAsset:
        """Load one prompt by ID and version."""
        index = self._load_index()
        try:
            return index[(prompt_id, version)]
        except KeyError as exc:
            raise PromptNotFoundError(f"Prompt {prompt_id!r} version {version!r} was not found") from exc

    def list(self) -> list[PromptAsset]:
        """Return all known prompts in deterministic order."""
        return [self._load_index()[key] for key in sorted(self._load_index())]

    def _load_index(self) -> dict[tuple[str, str], PromptAsset]:
        if self._index is not None:
            return self._index

        index: dict[tuple[str, str], PromptAsset] = {}
        for path in sorted(self.root.rglob("*.md")):
            prompt = _read_prompt(path)
            key = (prompt.id, prompt.version)
            if key in index:
                raise DuplicatePromptError(
                    f"Duplicate prompt {prompt.id!r} version {prompt.version!r}: "
                    f"{index[key].path} and {prompt.path}"
                )
            index[key] = prompt

        self._index = index
        return index


@lru_cache(maxsize=1)
def get_prompt_registry() -> PromptRegistry:
    """Return the default prompt registry."""
    return PromptRegistry()


def load_prompt_manifest(
    *,
    path: Path = DEFAULT_PROMPT_MANIFEST,
    registry: PromptRegistry | None = None,
) -> PromptManifest:
    """Load and validate `registry.yaml` against prompt files.

    The markdown frontmatter is the runtime source of prompt IDs and schemas.
    The YAML manifest is the management surface for eval ownership and review
    tooling. This function keeps those two surfaces from drifting.
    """
    root = path.parent
    prompt_registry = registry or PromptRegistry(root=root)
    assets = {
        (prompt.id, prompt.version): prompt
        for prompt in prompt_registry.list()
    }
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise PromptMetadataError(f"{path} must be a mapping")
    raw_prompts = raw.get("prompts")
    if not isinstance(raw_prompts, list) or not raw_prompts:
        raise PromptMetadataError(f"{path} must include a non-empty prompts list")

    entries: list[PromptManifestEntry] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_prompts):
        if not isinstance(item, dict):
            raise PromptMetadataError(f"{path} prompt entry {index} must be a mapping")
        prompt_id = _metadata_str(item, "id", path)
        version = _metadata_str(item, "version", path)
        relative_path = _metadata_str(item, "path", path)
        model_policy = _metadata_str(item, "model_policy", path)
        output_schema = _metadata_str(item, "output_schema", path)
        eval_suite = _metadata_str(item, "eval_suite", path)
        cache_policy = _metadata_str(item, "cache_policy", path)
        key = (prompt_id, version)
        if key in seen:
            raise DuplicatePromptError(
                f"Duplicate prompt manifest entry {prompt_id!r} version {version!r}"
            )
        seen.add(key)
        asset = assets.get(key)
        if asset is None:
            raise PromptNotFoundError(
                f"Prompt manifest references unknown prompt {prompt_id!r} version {version!r}"
            )
        expected_path = (root / relative_path).resolve()
        if asset.path.resolve() != expected_path:
            raise PromptMetadataError(
                f"{path} entry {prompt_id!r} points at {relative_path!r}, "
                f"but registry loaded {asset.path.relative_to(root)!s}"
            )
        if asset.model_policy != model_policy:
            raise PromptMetadataError(
                f"{path} entry {prompt_id!r} model_policy {model_policy!r} "
                f"does not match frontmatter {asset.model_policy!r}"
            )
        if asset.output_schema != output_schema:
            raise PromptMetadataError(
                f"{path} entry {prompt_id!r} output_schema {output_schema!r} "
                f"does not match frontmatter {asset.output_schema!r}"
            )
        if asset.cache_policy != cache_policy:
            raise PromptMetadataError(
                f"{path} entry {prompt_id!r} cache_policy {cache_policy!r} "
                f"does not match frontmatter {asset.cache_policy!r}"
            )
        entries.append(
            PromptManifestEntry(
                id=prompt_id,
                version=version,
                path=Path(relative_path),
                model_policy=model_policy,
                output_schema=output_schema,
                eval_suite=eval_suite,
                cache_policy=cache_policy,
            )
        )

    missing = sorted(set(assets) - seen)
    if missing:
        formatted = ", ".join(f"{prompt_id}@{version}" for prompt_id, version in missing)
        raise PromptMetadataError(f"{path} is missing prompt assets: {formatted}")

    return PromptManifest(entries=tuple(entries))


def _read_prompt(path: Path) -> PromptAsset:
    text = path.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(text, path)
    missing = sorted(REQUIRED_FRONTMATTER_FIELDS - metadata.keys())
    if missing:
        raise PromptMetadataError(f"{path} is missing frontmatter fields: {', '.join(missing)}")

    prompt_id = _metadata_str(metadata, "id", path)
    version = _metadata_str(metadata, "version", path)
    status = _metadata_str(metadata, "status", path)
    owner = _metadata_str(metadata, "owner", path)
    model_policy = _metadata_str(metadata, "model_policy", path)
    output_schema = _metadata_str(metadata, "output_schema", path)
    cache_policy = _metadata_str(metadata, "cache_policy", path)
    if cache_policy not in ALLOWED_CACHE_POLICIES:
        raise PromptMetadataError(
            f"{path} frontmatter field 'cache_policy' must be one of: "
            f"{', '.join(sorted(ALLOWED_CACHE_POLICIES))}"
        )
    try:
        get_model_route(model_policy)
    except ModelRouteNotFoundError as exc:
        raise PromptMetadataError(f"{path} references unknown model_policy {model_policy!r}") from exc
    if not body.strip():
        raise PromptMetadataError(f"{path} has an empty prompt body")

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return PromptAsset(
        id=prompt_id,
        version=version,
        status=status,
        owner=owner,
        model_policy=model_policy,
        output_schema=output_schema,
        cache_policy=cache_policy,
        body=body,
        path=path,
        digest=digest,
        metadata=metadata,
    )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise PromptMetadataError(f"{path} must start with YAML frontmatter")
    end_marker = "\n---\n"
    end = text.find(end_marker, 4)
    if end == -1:
        raise PromptMetadataError(f"{path} frontmatter is not closed")

    raw_metadata = text[4:end]
    body = text[end + len(end_marker):]
    loaded = yaml.safe_load(raw_metadata) or {}
    if not isinstance(loaded, dict):
        raise PromptMetadataError(f"{path} frontmatter must be a mapping")
    return loaded, body


def _metadata_str(metadata: dict[str, Any], key: str, path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PromptMetadataError(f"{path} frontmatter field {key!r} must be a non-empty string")
    return value.strip()
