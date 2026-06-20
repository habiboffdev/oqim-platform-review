"""Learn SKILL.md candidates from a workspace's past Telegram conversations.

Clio+LLooM pipeline: acquire customer->owner turn-pairs (PII stripped) ->
distill each to one sentence (FLASH_LITE) -> embed -> KMeans cluster ->
synthesize one skill per cluster (FLASH_CHAIN) -> dedup + confidence-rank ->
persist as reviewable LearnedSkillCandidate rows.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_CHAIN, FLASH_LITE_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset, get_prompt_registry
from app.core.log_sanitizer import sanitize
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.models.message import Message, SenderType
from app.modules.agent_runtime_context.service import _agent_kind
from app.modules.brain.contracts import DistilledBatch, LearnedSkill, SkillLearnReport, SynthesizedSkill
from app.modules.retrieval_core.indexing import RetrievalIndexEmbeddingService

_DISTILL_BATCH = 20
_MIN_PAIRS = 5
_SKILL_DISTILL_PROMPT_ID = "learning.skill_distill"
_SKILL_DISTILL_PROMPT_VERSION = "1.0.0"
_SKILL_SYNTH_PROMPT_ID = "learning.skill_synthesis"
_SKILL_SYNTH_PROMPT_VERSION = "1.0.0"

# Which contact types each agent kind learns skills from. Scales by adding a
# kind: a seller learns from customer chats, a personal/custom agent from the
# owner's personal/work chats — so a personal conversation is noise for a seller
# but signal for a personal agent. None means "every conversation" (legacy).
_CONTACT_TYPES_BY_AGENT_KIND: dict[str, tuple[str, ...]] = {
    "seller_agent": ("customer",),
    "support_agent": ("customer",),
    "follow_up_agent": ("customer",),
    "promoter_agent": ("customer",),
    "bi_agent": ("customer",),
    "catalog_update_agent": ("customer", "supplier"),
    "custom_agent": ("personal", "work"),
}
_DEFAULT_CONTACT_TYPES: tuple[str, ...] = ("customer",)

# Per-kind framing so a support or personal agent isn't distilled/synthesized as
# a "seller". The dimension hints stay shared across kinds.
_EXCHANGE_BY_KIND: dict[str, str] = {
    "seller_agent": "customer→owner sales exchange",
    "support_agent": "customer→support exchange",
    "follow_up_agent": "customer→owner follow-up exchange",
    "custom_agent": "person→owner exchange",
}
_SKILL_NOUN_BY_KIND: dict[str, str] = {
    "seller_agent": "selling skill",
    "support_agent": "support skill",
    "follow_up_agent": "follow-up skill",
    "custom_agent": "reusable skill",
}


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "skill"


@dataclass
class TurnPair:
    conversation_id: int
    customer_text: str
    owner_text: str


@dataclass
class DistilledPair:
    conversation_id: int
    summary: str
    dimension: str
    customer_text: str
    owner_text: str


class SkillLearnerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._embedding_boundary = RetrievalIndexEmbeddingService()

    async def acquire_pairs(
        self,
        *,
        workspace_id: int,
        contact_types: tuple[str, ...] | None = None,
        window_minutes: int = 10,
        limit: int = 500,
    ) -> list[TurnPair]:
        stmt = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == workspace_id,
                Message.is_deleted.is_(False),
                Message.content != "",
                Message.sender_type.in_([SenderType.CUSTOMER.value, SenderType.SELLER.value]),
            )
        )
        if contact_types is not None:
            # Scope to conversations whose contact matches this agent kind, so a
            # seller doesn't learn from the owner's personal chats and vice versa.
            stmt = stmt.join(Customer, Conversation.customer_id == Customer.id).where(
                Customer.contact_type.in_(tuple(contact_types))
            )
        stmt = stmt.order_by(
            Message.conversation_id,
            Message.conversation_seq.nulls_last(),
            Message.created_at,
            Message.id,
        )
        rows = list((await self.session.execute(stmt)).scalars().all())

        by_conv: dict[int, list[Message]] = {}
        for m in rows:
            by_conv.setdefault(m.conversation_id, []).append(m)

        pairs: list[TurnPair] = []
        for msgs in by_conv.values():
            for prev, cur in pairwise(msgs):
                if prev.sender_type != SenderType.CUSTOMER.value:
                    continue
                if cur.sender_type != SenderType.SELLER.value:
                    continue
                prev_t = prev.telegram_timestamp or prev.created_at
                cur_t = cur.telegram_timestamp or cur.created_at
                if (cur_t - prev_t).total_seconds() > window_minutes * 60:
                    continue
                customer_text = sanitize(prev.content).strip()
                owner_text = sanitize(cur.content).strip()
                if not customer_text or not owner_text:
                    continue
                pairs.append(TurnPair(prev.conversation_id, customer_text, owner_text))
                if len(pairs) >= limit:
                    return pairs
        return pairs

    async def distill(
        self, *, workspace_id: int, pairs: list[TurnPair], agent_kind: str = "seller_agent"
    ) -> list[DistilledPair]:
        out: list[DistilledPair] = []
        for start in range(0, len(pairs), _DISTILL_BATCH):
            batch = pairs[start : start + _DISTILL_BATCH]
            listing = "\n".join(
                f"[{i}] customer: {p.customer_text}\n    owner: {p.owner_text}"
                for i, p in enumerate(batch)
            )
            result = await generate_structured_json(
                chain=FLASH_LITE_CHAIN,
                system=_skill_distill_system_prompt(),
                prompt=(
                    f"Agent kind: {agent_kind}\n"
                    f"Exchange frame: {_EXCHANGE_BY_KIND.get(agent_kind, 'customer->owner exchange')}\n\n"
                    f"Distill each pair. Pairs:\n{listing}"
                ),
                response_schema=DistilledBatch,
                operation="skill_distill",
                workspace_id=workspace_id,
                prompt_cache=_skill_distill_prompt_cache(),
            )
            parsed = DistilledBatch.model_validate(result)
            by_index = {item.index: item for item in parsed.items}
            for i, p in enumerate(batch):
                item = by_index.get(i)
                if item is None or not item.summary.strip():
                    continue
                out.append(DistilledPair(p.conversation_id, item.summary.strip(), item.dimension, p.customer_text, p.owner_text))
        return out

    async def embed_and_cluster(self, distilled: list[DistilledPair]) -> list[int]:
        if len(distilled) <= 1:
            return [0] * len(distilled)
        vectors = await self._embed_texts(
            [d.summary for d in distilled],
            context_prefix="skill_learning.distilled",
        )
        if vectors is None:
            return [0] * len(distilled)
        return await asyncio.to_thread(self._cluster, vectors)

    @staticmethod
    def _cluster(vectors: list[list[float]]) -> list[int]:
        x = np.asarray(vectors, dtype=float)
        n = x.shape[0]
        if n <= 2:
            return list(range(n))
        base_k = max(2, min(40, round(math.sqrt(n))))
        candidate_ks = sorted({base_k, min(40, round(base_k * 1.5)), min(40, base_k * 2)})
        candidate_ks = [k for k in candidate_ks if 2 <= k < n]
        best_labels = [0] * n
        best_score = -2.0
        for k in candidate_ks:
            labels = KMeans(n_clusters=k, random_state=0, n_init=10).fit_predict(x)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(x, labels)
            if score > best_score:
                best_score, best_labels = score, labels.tolist()
            if best_score >= 0.1 and k == base_k:
                break
        return best_labels

    async def synthesize(
        self,
        *,
        workspace_id: int,
        distilled: list[DistilledPair],
        labels: list[int],
        agent_kind: str = "seller_agent",
    ) -> list[LearnedSkill]:
        clusters: dict[int, list[DistilledPair]] = {}
        for d, label in zip(distilled, labels, strict=True):
            clusters.setdefault(label, []).append(d)

        skills: list[LearnedSkill] = []
        used_slugs: set[str] = set()
        for label in sorted(clusters):
            members = clusters[label]
            summaries = "\n".join(f"- {m.summary} (owner said: {m.owner_text})" for m in members[:15])
            result = await generate_structured_json(
                chain=FLASH_CHAIN,
                system=_skill_synthesis_system_prompt(),
                prompt=(
                    f"Agent kind: {agent_kind}\n"
                    f"Skill noun: {_SKILL_NOUN_BY_KIND.get(agent_kind, 'reusable skill')}\n\n"
                    f"Examples in this cluster:\n{summaries}"
                ),
                response_schema=SynthesizedSkill,
                operation="skill_synthesis",
                workspace_id=workspace_id,
                prompt_cache=_skill_synthesis_prompt_cache(),
            )
            synth = SynthesizedSkill.model_validate(result)
            slug = _slugify(synth.slug or synth.name)
            base = slug
            n = 2
            while slug in used_slugs:
                slug = f"{base}-{n}"
                n += 1
            used_slugs.add(slug)
            evidence = sorted({m.conversation_id for m in members})
            skills.append(LearnedSkill(
                slug=slug, name=synth.name, trigger=synth.trigger, action=synth.action,
                example_phrase=synth.example_phrase, dimension=synth.dimension,
                confidence=synth.confidence, evidence_conv_ids=evidence,
            ))
        return skills

    async def dedup_and_rank(
        self, skills: list[LearnedSkill], *, threshold: float = 0.85, top_n: int = 20
    ) -> list[LearnedSkill]:
        if len(skills) <= 1:
            return skills
        ordered = sorted(skills, key=lambda s: s.confidence, reverse=True)
        vectors = await self._embed_texts(
            [f"{s.trigger} {s.action}" for s in ordered],
            context_prefix="skill_learning.dedup",
        )
        if vectors is None:
            return ordered[:top_n]
        x = np.asarray(vectors, dtype=float)
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        x_norm = x / norms
        kept: list[int] = []
        for i in range(len(ordered)):
            if any(float(np.dot(x_norm[i], x_norm[j])) > threshold for j in kept):
                continue
            kept.append(i)
        return [ordered[i] for i in kept[:top_n]]

    async def persist_candidates(self, *, workspace_id: int, skills: list[LearnedSkill]) -> None:
        existing = (await self.session.execute(
            select(LearnedSkillCandidate).where(
                LearnedSkillCandidate.workspace_id == workspace_id,
                LearnedSkillCandidate.status == "proposed",
                LearnedSkillCandidate.source == "learned",
            )
        )).scalars().all()
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()
        for s in skills:
            self.session.add(LearnedSkillCandidate(
                workspace_id=workspace_id, slug=s.slug, name=s.name, trigger=s.trigger,
                action=s.action, example_phrase=s.example_phrase, dimension=s.dimension,
                confidence=s.confidence, evidence_conv_ids=s.evidence_conv_ids,
                status="proposed", source="learned",
            ))
        await self.session.flush()

    async def learn(self, *, workspace_id: int, agent_id: int | None = None) -> SkillLearnReport:
        agent_kind, contact_types = await self._learning_scope(workspace_id, agent_id)
        pairs = await self.acquire_pairs(workspace_id=workspace_id, contact_types=contact_types)
        if len(pairs) < _MIN_PAIRS:
            return SkillLearnReport(pairs_used=0, clusters=0, candidates=0)
        distilled = await self.distill(workspace_id=workspace_id, pairs=pairs, agent_kind=agent_kind)
        if not distilled:
            return SkillLearnReport(pairs_used=len(pairs), clusters=0, candidates=0)
        labels = await self.embed_and_cluster(distilled)
        skills = await self.synthesize(
            workspace_id=workspace_id, distilled=distilled, labels=labels, agent_kind=agent_kind
        )
        ranked = await self.dedup_and_rank(skills)
        await self.persist_candidates(workspace_id=workspace_id, skills=ranked)
        return SkillLearnReport(pairs_used=len(pairs), clusters=len(set(labels)), candidates=len(ranked))

    async def _learning_scope(
        self, workspace_id: int, agent_id: int | None
    ) -> tuple[str, tuple[str, ...] | None]:
        """Resolve (agent_kind, contact_types) for skill learning.

        No agent → learn from every conversation under the seller framing
        (legacy/ad-hoc). An agent → scope to that kind's relevant contact types
        so a seller learns from customers and a personal agent from personal chats.
        """
        if agent_id is None:
            return "seller_agent", None
        agent = await self.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            return "seller_agent", None
        kind = _agent_kind(agent)
        return kind, _CONTACT_TYPES_BY_AGENT_KIND.get(kind, _DEFAULT_CONTACT_TYPES)

    async def _embed_texts(
        self,
        texts: list[str],
        *,
        context_prefix: str,
    ) -> list[list[float]] | None:
        results = await self._embedding_boundary.embed_texts(
            texts,
            enabled=True,
            context_prefix=context_prefix,
        )
        vectors = [result.embedding for result in results]
        if len(vectors) != len(texts) or any(vector is None for vector in vectors):
            return None
        return [list(vector) for vector in vectors if vector is not None]


def _skill_distill_system_prompt() -> str:
    return _skill_distill_prompt_asset().body.strip()


def _skill_distill_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _skill_distill_prompt_asset(),
        cache_scope="learning.skill_distill",
    )


@lru_cache(maxsize=1)
def _skill_distill_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _SKILL_DISTILL_PROMPT_ID,
        version=_SKILL_DISTILL_PROMPT_VERSION,
    )


def _skill_synthesis_system_prompt() -> str:
    return _skill_synthesis_prompt_asset().body.strip()


def _skill_synthesis_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _skill_synthesis_prompt_asset(),
        cache_scope="learning.skill_synthesis",
    )


@lru_cache(maxsize=1)
def _skill_synthesis_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _SKILL_SYNTH_PROMPT_ID,
        version=_SKILL_SYNTH_PROMPT_VERSION,
    )
