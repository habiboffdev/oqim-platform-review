"""Onboarding doc-gen orchestrator: streams BUSINESS→AGENT→SKILL section by section."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.agent import Agent
from app.modules.brain.agent_document import AgentDocumentBuilderService
from app.modules.brain.business_document import BusinessDocumentService
from app.modules.brain.contracts import (
    AGENT_SECTIONS,
    BUSINESS_SECTIONS,
    AgentSectionDraft,
    BusinessSectionDraft,
)
from app.modules.brain.skill_learner import SkillLearnerService
from app.services.onboarding_documents_progress import (
    default_docgen_progress,
    load_docgen_progress,
    store_docgen_progress,
)

logger = get_logger("brain.onboarding_documents")

_PLACEHOLDER = "Ma'lumot yetishmadi — OQIM keyinroq qayta o'rganadi."


class OnboardingDocumentsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _first_agent_id(self, *, workspace_id: int) -> int | None:
        stmt = select(Agent.id).where(Agent.workspace_id == workspace_id).order_by(Agent.id).limit(1)
        return (await self.session.execute(stmt)).scalars().first()

    async def _all_agent_ids(self, *, workspace_id: int) -> list[int]:
        stmt = select(Agent.id).where(Agent.workspace_id == workspace_id).order_by(Agent.id)
        return list((await self.session.execute(stmt)).scalars().all())

    def _section_block(self, specs, persisted, progress, doc_name: str) -> dict:
        by_key = {s.section_key: s for s in persisted}
        current = progress.get("current_section") if progress.get("current_doc") == doc_name else None
        sections, approved, proposed = [], 0, 0
        for spec in specs:
            row = by_key.get(spec.key)
            if row is not None and row.generated_by == "owner":
                status = "approved"
                approved += 1
            elif row is not None:
                status = "proposed"
                proposed += 1
            elif current == spec.key:
                status = "generating"
            else:
                status = "pending"
            sections.append({
                "key": spec.key, "title": spec.title, "status": status,
                "body": row.body if row is not None else "",
                "evidence_count": len(row.source_evidence or []) if row is not None else 0,
            })
        generating = current if (progress.get("current_doc") == doc_name and current and by_key.get(current) is None) else None
        return {"total": len(specs), "approved": approved, "proposed": proposed, "generating": generating, "sections": sections}

    async def build_documents_projection(self, *, workspace_id: int) -> dict:
        progress = await load_docgen_progress(workspace_id)
        biz_rows = await BusinessDocumentService(self.session)._load_sections(workspace_id=workspace_id)
        biz = self._section_block(BUSINESS_SECTIONS, biz_rows, progress, "business")
        agent_id = await self._first_agent_id(workspace_id=workspace_id)
        agent_rows = (await AgentDocumentBuilderService(self.session)._load_sections(workspace_id=workspace_id, agent_id=agent_id)) if agent_id else []
        agent = self._section_block(AGENT_SECTIONS, agent_rows, progress, "agent")
        skill = {"status": progress.get("skill_status", "pending"), "candidates": int(progress.get("skill_candidates", 0))}
        total = biz["total"] + agent["total"]
        landed = biz["approved"] + biz["proposed"] + agent["approved"] + agent["proposed"]
        return {
            "schema_version": "onboarding_documents.v1", "workspace_id": workspace_id,
            "running": bool(progress.get("running")), "current_doc": progress.get("current_doc"),
            "error": progress.get("error"), "percent": round(100 * landed / total) if total else 0,
            "documents": {"business": biz, "agent": agent, "skill": skill},
        }

    async def generate_all(self, *, workspace_id: int, agent_id: int | None = None) -> None:
        progress = default_docgen_progress()
        try:
            bsvc = BusinessDocumentService(self.session)
            fact_context = await bsvc.build_fact_context(workspace_id=workspace_id)
            for spec in BUSINESS_SECTIONS:
                progress = {**progress, "running": True, "current_doc": "business", "current_section": spec.key, "error": None}
                await store_docgen_progress(workspace_id, progress)
                try:
                    draft = await bsvc.synthesize_section(workspace_id=workspace_id, section_key=spec.key, fact_context=fact_context)
                except Exception:
                    logger.warning(
                        "onboarding.docgen.section_failed doc=business key=%s",
                        spec.key, exc_info=True,
                    )
                    draft = BusinessSectionDraft(section_key=spec.key, body=_PLACEHOLDER, confidence=0.0)
                await bsvc.persist_section(workspace_id=workspace_id, draft=draft)
                # Commit per section so the docs stream (a separate read session)
                # sees each section land live — flush alone stays invisible to it
                # and the workbench would sit at "Navbatda" until the final commit.
                await self.session.commit()

            agent_ids = await self._all_agent_ids(workspace_id=workspace_id)
            if agent_id is not None and agent_id in agent_ids:
                # Honor an explicit focus agent: generate it first so it owns the
                # workbench's projected AGENT progress.
                agent_ids = [agent_id, *(a for a in agent_ids if a != agent_id)]
            if agent_ids:
                asvc = AgentDocumentBuilderService(self.session)
                business_context = await asvc.build_business_context(workspace_id=workspace_id)
                focus_id = agent_ids[0]
                for current_agent_id in agent_ids:
                    agent = await asvc._load_agent(workspace_id=workspace_id, agent_id=current_agent_id)
                    agent_context = asvc._agent_context(agent)
                    for spec in AGENT_SECTIONS:
                        # Only the focus agent drives the workbench AGENT progress.
                        if current_agent_id == focus_id:
                            progress = {**progress, "current_doc": "agent", "current_section": spec.key}
                            await store_docgen_progress(workspace_id, progress)
                        try:
                            draft = await asvc.synthesize_section(
                                workspace_id=workspace_id,
                                agent_context=agent_context,
                                business_context=business_context,
                                section_key=spec.key,
                            )
                        except Exception:
                            logger.warning(
                                "onboarding.docgen.section_failed doc=agent agent_id=%s key=%s",
                                current_agent_id, spec.key, exc_info=True,
                            )
                            draft = AgentSectionDraft(section_key=spec.key, body=_PLACEHOLDER, confidence=0.0)
                        await asvc.persist_section(
                            workspace_id=workspace_id, agent_id=current_agent_id, draft=draft
                        )
                        # Commit per section for live stream visibility (see business loop).
                        await self.session.commit()

            progress = {**progress, "current_doc": "skill", "current_section": None, "skill_status": "learning"}
            await store_docgen_progress(workspace_id, progress)
            try:
                report = await SkillLearnerService(self.session).learn(
                    workspace_id=workspace_id,
                    agent_id=agent_ids[0] if agent_ids else None,
                )
                skill_status = "proposed" if report.candidates else ("degraded" if report.pairs_used < 5 else "learning")
                skill_candidates = report.candidates
            except Exception:
                logger.warning("onboarding.docgen.skill_learn_failed", exc_info=True)
                skill_status, skill_candidates = "degraded", 0

            await store_docgen_progress(workspace_id, {**default_docgen_progress(), "skill_status": skill_status, "skill_candidates": skill_candidates})
        except Exception as exc:
            await store_docgen_progress(workspace_id, {**default_docgen_progress(), "error": str(exc)})
            raise
