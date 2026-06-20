"""Import a SKILL.md candidate from pasted or uploaded text.

Normalizes an existing SKILL.md document or free-text description into one
SynthesizedSkill via the LLM and persists it as a reviewable LearnedSkillCandidate
(source="upload"). Approval reuses the same /brain/skills flow as auto-learned
candidates.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset, get_prompt_registry
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.modules.brain.contracts import SynthesizedSkill
from app.modules.brain.skill_learner import _slugify

_SKILL_UPLOAD_PROMPT_ID = "learning.skill_upload_normalize"
_SKILL_UPLOAD_PROMPT_VERSION = "1.0.0"


class SkillUploadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def import_from_text(self, *, workspace_id: int, content: str) -> LearnedSkillCandidate:
        if not content.strip():
            raise ValueError("content must not be empty")
        result = await generate_structured_json(
            chain=FLASH_CHAIN,
            system=_skill_upload_system_prompt(),
            prompt=f"Normalize this into one skill:\n\n{content}",
            response_schema=SynthesizedSkill,
            operation="skill_upload_normalize",
            workspace_id=workspace_id,
            prompt_cache=_skill_upload_prompt_cache(),
        )
        synth = SynthesizedSkill.model_validate(result)
        slug = await self._unique_slug(workspace_id=workspace_id, base=_slugify(synth.slug or synth.name))
        candidate = LearnedSkillCandidate(
            workspace_id=workspace_id, slug=slug, name=synth.name, trigger=synth.trigger,
            action=synth.action, example_phrase=synth.example_phrase, dimension=synth.dimension,
            confidence=synth.confidence, evidence_conv_ids=[], status="proposed", source="upload",
        )
        self.session.add(candidate)
        await self.session.flush()
        return candidate

    async def _unique_slug(self, *, workspace_id: int, base: str) -> str:
        existing = set((await self.session.execute(
            select(LearnedSkillCandidate.slug).where(LearnedSkillCandidate.workspace_id == workspace_id)
        )).scalars().all())
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"


def _skill_upload_system_prompt() -> str:
    return _skill_upload_prompt_asset().body.strip()


def _skill_upload_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _skill_upload_prompt_asset(),
        cache_scope="learning.skill_upload_normalize",
    )


@lru_cache(maxsize=1)
def _skill_upload_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _SKILL_UPLOAD_PROMPT_ID,
        version=_SKILL_UPLOAD_PROMPT_VERSION,
    )
