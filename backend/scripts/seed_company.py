"""Idempotently seed an OQIM company source package.

Company facts, approved policies, catalog data, and missing fields live in JSON
under ``backend/seed_data/companies``. This script imports that package into the
database so the same data can be applied locally or on a server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import utc_now
from app.db.session import async_session
from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_session import AgentSession
from app.models.commerce_catalog import (
    CatalogMissingFieldRecord,
    CatalogOfferRecord,
    CatalogProductRecord,
    CatalogSourceFactRecord,
)
from app.models.hermes_session import HermesSessionMessageRecord, HermesSessionRecord
from app.models.knowledge_mcp import (
    KnowledgeChunkRecord,
    KnowledgeCollectionRecord,
    KnowledgeItemRecord,
    KnowledgeSourceRecord,
)
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.telegram_tools.contracts import (
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_SEND_REACTION,
)
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService

DEFAULT_PACKAGE = (
    Path(__file__).resolve().parents[1]
    / "seed_data"
    / "companies"
    / "biznesni_tizimlashtirish.json"
)

TALK_TOOL_EXTERNAL_SCOPES = {
    "talk.send_msgs": TELEGRAM_SEND_MESSAGE,
    "talk.send_media": TELEGRAM_SEND_MESSAGE,
    "talk.send_reaction": TELEGRAM_SEND_REACTION,
}


async def seed(
    *,
    package_path: Path,
    workspace_id: int | None = None,
    phone_number: str | None = None,
    telegram_user_id: int | None = None,
    mark_telegram_connected: bool = False,
    reset_hermes_sessions: bool = False,
) -> dict[str, Any]:
    package = _load_package(package_path)
    source_ref = str(package["source_ref"])
    workspace_payload = dict(package["workspace"])
    agent_payload = dict(package["agent"])
    async with async_session() as session:
        workspace = await _load_or_create_workspace(
            session,
            workspace_id=workspace_id,
            phone_number=phone_number or str(workspace_payload["phone_number"]),
        )
        phone = _resolve_workspace_phone(
            workspace,
            workspace_id=workspace_id,
            requested_phone_number=phone_number,
            package_phone_number=str(workspace_payload["phone_number"]),
        )
        _apply_workspace(
            workspace,
            payload=workspace_payload,
            phone_number=phone,
            telegram_user_id=telegram_user_id,
            mark_telegram_connected=mark_telegram_connected,
        )
        await session.flush()

        agent = await _load_or_create_agent(
            session,
            workspace_id=workspace.id,
            name=str(agent_payload["name"]),
        )
        await _apply_agent(session, agent=agent, workspace_id=workspace.id, payload=agent_payload)
        tool_grant_count = await _upsert_tool_grants(
            session,
            workspace_id=workspace.id,
            agent_id=agent.id,
            scopes=_external_tool_grants_for_agent(agent_payload),
            source_ref=source_ref,
        )

        docs = AgentDocumentService(session)
        await _replace_document_sections(
            session,
            workspace_id=workspace.id,
            agent_id=agent.id,
        )
        await _upsert_business_sections(
            docs,
            workspace_id=workspace.id,
            sections=list(package.get("business_sections") or []),
            source_ref=source_ref,
        )
        await _upsert_agent_sections(
            docs,
            workspace_id=workspace.id,
            agent_id=agent.id,
            sections=list(package.get("agent_sections") or []),
            source_ref=source_ref,
        )
        await _upsert_seller_playbook(
            docs,
            workspace_id=workspace.id,
            body=str(package.get("seller_playbook") or ""),
            source_ref=source_ref,
        )
        await _upsert_skills(
            docs,
            workspace_id=workspace.id,
            agent_id=agent.id,
            skills=list(package.get("skills") or []),
        )
        await _upsert_knowledge_items(
            session,
            workspace_id=workspace.id,
            company_slug=str(package.get("company_slug") or "company"),
            source_ref=source_ref,
            items=list(package.get("knowledge_items") or []),
        )
        await _upsert_catalog(
            session,
            workspace_id=workspace.id,
            catalog=dict(package["catalog"]),
            source_ref=source_ref,
        )
        if reset_hermes_sessions:
            deleted_sessions = await _reset_agent_hermes_sessions(
                session,
                workspace_id=workspace.id,
                agent_id=agent.id,
            )
        else:
            deleted_sessions = 0

        await session.commit()
        return {
            "status": "seeded",
            "package": str(package_path),
            "source_ref": source_ref,
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "workspace_phone": workspace.phone_number,
            "telegram_connected": workspace.telegram_connected,
            "telegram_user_id": workspace.telegram_user_id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_type": agent.agent_type,
            "trust_mode": agent.trust_mode,
            "auto_send_threshold": agent.auto_send_threshold,
            "tool_grant_count": tool_grant_count,
            "deleted_hermes_sessions": deleted_sessions,
        }


def _resolve_workspace_phone(
    workspace: Workspace,
    *,
    workspace_id: int | None,
    requested_phone_number: str | None,
    package_phone_number: str,
) -> str:
    if requested_phone_number:
        return requested_phone_number
    if workspace_id is not None and workspace.phone_number:
        return workspace.phone_number
    return package_phone_number


def _load_package(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != "company_seed.v1":
        raise ValueError("unsupported seed package schema")
    return payload


async def _load_or_create_workspace(
    session,
    *,
    workspace_id: int | None,
    phone_number: str,
) -> Workspace:
    if workspace_id is not None:
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError(f"workspace not found: {workspace_id}")
        return workspace

    workspace = await session.scalar(
        select(Workspace).where(Workspace.phone_number == phone_number)
    )
    if workspace is not None:
        return workspace

    workspace = Workspace(
        phone_number=phone_number,
        name="",
        password_hash="",
        type="education",
        subscription_tier="pilot",
        trust_mode="autopilot",
        onboarding_completed=True,
        telegram_connected=False,
    )
    session.add(workspace)
    await session.flush()
    return workspace


def _apply_workspace(
    workspace: Workspace,
    *,
    payload: dict[str, Any],
    phone_number: str,
    telegram_user_id: int | None,
    mark_telegram_connected: bool,
) -> None:
    workspace.phone_number = phone_number
    workspace.name = str(payload.get("name") or workspace.name or "Pilot Workspace")
    workspace.type = str(payload.get("type") or workspace.type or "education")
    workspace.description = str(payload.get("description") or "")
    workspace.subscription_tier = str(payload.get("subscription_tier") or "pilot")
    workspace.trust_mode = str(payload.get("trust_mode") or "autopilot")
    workspace.onboarding_completed = True
    workspace.is_active = True
    if telegram_user_id is not None:
        workspace.telegram_user_id = telegram_user_id
    if mark_telegram_connected:
        workspace.telegram_connected = True
    workspace.updated_at = utc_now()


async def _load_or_create_agent(
    session,
    *,
    workspace_id: int,
    name: str,
) -> Agent:
    agent = await session.scalar(
        select(Agent).where(Agent.workspace_id == workspace_id, Agent.name == name)
    )
    if agent is not None:
        return agent
    agent = Agent(
        workspace_id=workspace_id,
        name=name,
        is_default=True,
        is_active=True,
        agent_type="seller",
        contact_scope="business",
        trust_mode="autopilot",
        auto_send_threshold=0.0,
        persona={},
        tools_config={},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
    )
    session.add(agent)
    await session.flush()
    return agent


async def _apply_agent(
    session,
    *,
    agent: Agent,
    workspace_id: int,
    payload: dict[str, Any],
) -> None:
    await session.execute(
        select(Agent)
        .where(Agent.workspace_id == workspace_id, Agent.id != agent.id)
        .with_for_update()
    )
    agent.name = str(payload.get("name") or agent.name)
    agent.is_default = True
    agent.is_active = True
    agent.agent_type = str(payload.get("agent_type") or "seller")
    agent.contact_scope = str(payload.get("contact_scope") or "business")
    agent.trust_mode = str(payload.get("trust_mode") or "autopilot")
    agent.auto_send_threshold = float(payload.get("auto_send_threshold") or 0.0)
    agent.persona = dict(payload.get("persona") or {})
    agent.tools_config = dict(payload.get("tools_config") or {})
    agent.knowledge_config = dict(
        payload.get("knowledge_config") or {"use_catalog": True, "use_knowledge": True}
    )
    agent.updated_at = utc_now()

    other_agents = (
        await session.scalars(
            select(Agent).where(Agent.workspace_id == workspace_id, Agent.id != agent.id)
        )
    ).all()
    for other in other_agents:
        other.is_default = False
        other.is_active = False


def _external_tool_grants_for_agent(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("tool_grants")
    if explicit is None:
        explicit = dict(payload.get("tools_config") or {}).get("external_tool_scopes")
    scopes: list[str] = [str(scope).strip() for scope in list(explicit or []) if str(scope).strip()]
    enabled_tools = dict(payload.get("tools_config") or {}).get("enabled_tools") or []
    for tool_name in enabled_tools:
        scope = TALK_TOOL_EXTERNAL_SCOPES.get(str(tool_name).strip())
        if scope:
            scopes.append(scope)
    return list(dict.fromkeys(scopes))


async def _upsert_tool_grants(
    session,
    *,
    workspace_id: int,
    agent_id: int,
    scopes: list[str],
    source_ref: str,
) -> int:
    grants = ToolGrantService(session)
    count = 0
    for scope in scopes:
        await grants.grant(
            workspace_id=workspace_id,
            payload=ToolGrantInput(
                agent_id=agent_id,
                scope=scope,
                granted_by="seed_company",
                grant_reason="External Telegram permission required by the seeded agent runtime tools.",
                audit_metadata={
                    "source": "seed_company",
                    "source_ref": source_ref,
                    "schema_version": "company_seed.v1",
                },
            ),
        )
        count += 1
    return count


async def _replace_document_sections(
    session,
    *,
    workspace_id: int,
    agent_id: int,
) -> None:
    await session.execute(
        delete(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == "business",
            AgentDocumentSection.subject_type == "workspace",
        )
    )
    await session.execute(
        delete(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == "agent",
            AgentDocumentSection.subject_type == "agent",
            AgentDocumentSection.subject_id == agent_id,
        )
    )
    await session.execute(
        delete(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == "playbook",
            AgentDocumentSection.subject_type == "workspace",
        )
    )


async def _upsert_seller_playbook(
    docs: AgentDocumentService,
    *,
    workspace_id: int,
    body: str,
    source_ref: str,
) -> None:
    """Per-workspace selling-method override (empty body -> keep the managed
    default by writing nothing)."""
    if not body.strip():
        return
    await docs.upsert_section(
        workspace_id=workspace_id,
        payload=AgentDocumentSectionInput(
            document_kind="playbook",
            subject_type="workspace",
            section_key="seller_playbook",
            title="Seller playbook",
            body=body,
            source_evidence=[{"ref": source_ref}],
            generated_by="owner",
        ),
    )


async def _upsert_business_sections(
    docs: AgentDocumentService,
    *,
    workspace_id: int,
    sections: list[dict[str, Any]],
    source_ref: str,
) -> None:
    for section in sections:
        await docs.upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="business",
                subject_type="workspace",
                section_key=str(section["section_key"]),
                title=str(section["title"]),
                body=str(section.get("body") or ""),
                order_index=int(section.get("order_index") or 0),
                source_evidence=[{"ref": source_ref}],
                generated_by="owner",
            ),
        )


async def _upsert_agent_sections(
    docs: AgentDocumentService,
    *,
    workspace_id: int,
    agent_id: int,
    sections: list[dict[str, Any]],
    source_ref: str,
) -> None:
    for section in sections:
        await docs.upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent_id,
                section_key=str(section["section_key"]),
                title=str(section["title"]),
                body=str(section.get("body") or ""),
                order_index=int(section.get("order_index") or 0),
                source_evidence=[{"ref": source_ref}],
                generated_by="owner",
            ),
        )


async def _upsert_skills(
    docs: AgentDocumentService,
    *,
    workspace_id: int,
    agent_id: int,
    skills: list[dict[str, Any]],
) -> None:
    for skill in skills:
        await docs.upsert_skill(
            workspace_id=workspace_id,
            payload=AgentSkillInput(
                slug=str(skill["slug"]),
                name=str(skill["name"]),
                description=str(skill.get("description") or ""),
                instructions=str(skill.get("instructions") or ""),
                when_to_use=str(skill.get("when_to_use") or ""),
                when_not_to_use=str(skill.get("when_not_to_use") or ""),
                tools=[str(item) for item in list(skill.get("tools") or [])],
                examples=[item for item in list(skill.get("examples") or []) if isinstance(item, dict)],
                agent_id=agent_id,
                enabled=True,
            ),
        )


async def _upsert_catalog(
    session,
    *,
    workspace_id: int,
    catalog: dict[str, Any],
    source_ref: str,
) -> None:
    product_payload = dict(catalog["product"])
    offer_payload = dict(catalog["offer"])
    product_ref = str(product_payload["product_ref"])
    source_fact_id = f"catalog_product:{product_ref.removeprefix('product:')}"

    product = await session.scalar(
        select(CatalogProductRecord).where(
            CatalogProductRecord.workspace_id == workspace_id,
            CatalogProductRecord.product_ref == product_ref,
        )
    )
    if product is None:
        product = CatalogProductRecord(
            workspace_id=workspace_id,
            product_ref=product_ref,
            name=str(product_payload["name"]),
            authority_state="approved",
        )
        session.add(product)
    product.name = str(product_payload["name"])
    product.aliases = [str(item) for item in list(product_payload.get("aliases") or [])]
    product.description = str(product_payload.get("description") or "")
    product.attributes = dict(product_payload.get("attributes") or {})
    product.authority_state = "approved"
    product.source_refs = [source_ref]
    product.source_fact_ids = [source_fact_id]
    product.freshness = {
        "source": "manual_pilot_seed",
        "seeded_at": datetime.now(UTC).isoformat(),
    }

    source_fact = await session.scalar(
        select(CatalogSourceFactRecord).where(
            CatalogSourceFactRecord.workspace_id == workspace_id,
            CatalogSourceFactRecord.source_fact_id == source_fact_id,
        )
    )
    if source_fact is None:
        source_fact = CatalogSourceFactRecord(
            workspace_id=workspace_id,
            source_fact_id=source_fact_id,
            fact_type="catalog_product",
        )
        session.add(source_fact)
    source_fact.product_ref = product_ref
    source_fact.authority_state = "approved"
    source_fact.value = dict(product_payload)
    source_fact.source_refs = [source_ref]

    offer_ref = str(offer_payload["offer_ref"])
    offer_source_fact_id = f"catalog_offer:{offer_ref.removeprefix('offer:')}"
    offer = await session.scalar(
        select(CatalogOfferRecord).where(
            CatalogOfferRecord.workspace_id == workspace_id,
            CatalogOfferRecord.offer_ref == offer_ref,
        )
    )
    if offer is None:
        offer = CatalogOfferRecord(
            workspace_id=workspace_id,
            offer_ref=offer_ref,
            product_ref=product_ref,
            authority_state="approved",
        )
        session.add(offer)
    offer.product_ref = product_ref
    offer.variant_ref = offer_payload.get("variant_ref")
    offer.price = offer_payload.get("price")
    offer.currency = offer_payload.get("currency")
    offer.stock_state = offer_payload.get("stock_state")
    offer.availability = offer_payload.get("availability")
    offer.authority_state = "approved"
    offer.source_refs = [source_ref]
    offer.source_fact_ids = [offer_source_fact_id]

    offer_source_fact = await session.scalar(
        select(CatalogSourceFactRecord).where(
            CatalogSourceFactRecord.workspace_id == workspace_id,
            CatalogSourceFactRecord.source_fact_id == offer_source_fact_id,
        )
    )
    if offer_source_fact is None:
        offer_source_fact = CatalogSourceFactRecord(
            workspace_id=workspace_id,
            source_fact_id=offer_source_fact_id,
            fact_type="catalog_offer",
        )
        session.add(offer_source_fact)
    offer_source_fact.product_ref = product_ref
    offer_source_fact.authority_state = "approved"
    offer_source_fact.value = dict(offer_payload)
    offer_source_fact.source_refs = [source_ref]

    for field in list(catalog.get("missing_fields") or []):
        field_name = str(field).strip()
        if not field_name:
            continue
        missing = await session.scalar(
            select(CatalogMissingFieldRecord).where(
                CatalogMissingFieldRecord.workspace_id == workspace_id,
                CatalogMissingFieldRecord.product_ref == product_ref,
                CatalogMissingFieldRecord.field == field_name,
            )
        )
        if missing is None:
            missing = CatalogMissingFieldRecord(
                workspace_id=workspace_id,
                product_ref=product_ref,
                field=field_name,
            )
            session.add(missing)
        missing.authority_state = "candidate"
        missing.source_refs = [source_ref]


async def _upsert_knowledge_items(
    session,
    *,
    workspace_id: int,
    company_slug: str,
    source_ref: str,
    items: list[dict[str, Any]],
) -> None:
    owner_type = "workspace"
    owner_id = f"workspace:{workspace_id}"
    for item in items:
        item_slug = str(item["item_id"]).strip()
        body_text = str(item.get("body_text") or "").strip()
        if not item_slug or not body_text:
            continue
        collection_ids = [str(value).strip() for value in list(item.get("collection_ids") or []) if str(value).strip()]
        for collection_id in collection_ids:
            await _upsert_knowledge_collection(
                session,
                owner_type=owner_type,
                owner_id=owner_id,
                workspace_id=workspace_id,
                collection_id=collection_id,
            )

        source_id = f"company_source:{company_slug}:{item_slug}"
        item_id = f"company_item:{company_slug}:{item_slug}"
        chunk_id = f"company_chunk:{company_slug}:{item_slug}:0"
        checksum = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

        source = await session.scalar(
            select(KnowledgeSourceRecord).where(
                KnowledgeSourceRecord.owner_type == owner_type,
                KnowledgeSourceRecord.owner_id == owner_id,
                KnowledgeSourceRecord.source_id == source_id,
            )
        )
        if source is None:
            source = KnowledgeSourceRecord(
                owner_type=owner_type,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_id=source_id,
                source_kind="paste",
                checksum=checksum,
                raw_content=body_text,
            )
            session.add(source)
        source.workspace_id = workspace_id
        source.source_kind = "paste"
        source.external_ref = source_ref
        source.checksum = checksum
        source.raw_content = body_text
        source.ingestion_status = "ready"
        source.acl_snapshot = {"scope": "workspace"}
        source.freshness = {"source_ref": source_ref}
        source.metadata_json = {
            "schema_version": "company_seed_source.v1",
            "source_ref": source_ref,
            "company_slug": company_slug,
        }

        knowledge_item = await session.scalar(
            select(KnowledgeItemRecord).where(
                KnowledgeItemRecord.owner_type == owner_type,
                KnowledgeItemRecord.owner_id == owner_id,
                KnowledgeItemRecord.item_id == item_id,
            )
        )
        if knowledge_item is None:
            knowledge_item = KnowledgeItemRecord(
                owner_type=owner_type,
                owner_id=owner_id,
                workspace_id=workspace_id,
                item_id=item_id,
                kind=str(item.get("kind") or "source"),
                title=str(item.get("title") or item_slug),
                body_text=body_text,
                authority_state=str(item.get("authority_state") or "approved"),
                visibility="workspace",
                created_by="user",
                created_by_ref=source_ref,
            )
            session.add(knowledge_item)
        knowledge_item.workspace_id = workspace_id
        knowledge_item.kind = str(item.get("kind") or "source")
        knowledge_item.title = str(item.get("title") or item_slug)
        knowledge_item.body_text = body_text
        knowledge_item.source_refs = [source_id]
        knowledge_item.collection_ids = collection_ids
        knowledge_item.tags = [str(value).strip() for value in list(item.get("tags") or []) if str(value).strip()]
        knowledge_item.authority_state = str(item.get("authority_state") or "approved")
        knowledge_item.visibility = "workspace"
        knowledge_item.created_by = "user"
        knowledge_item.created_by_ref = source_ref
        knowledge_item.metadata_json = {
            "schema_version": "company_seed_item.v1",
            "source_ref": source_ref,
            "company_slug": company_slug,
        }

        chunk = await session.scalar(
            select(KnowledgeChunkRecord).where(
                KnowledgeChunkRecord.owner_type == owner_type,
                KnowledgeChunkRecord.owner_id == owner_id,
                KnowledgeChunkRecord.chunk_id == chunk_id,
            )
        )
        if chunk is None:
            chunk = KnowledgeChunkRecord(
                owner_type=owner_type,
                owner_id=owner_id,
                workspace_id=workspace_id,
                chunk_id=chunk_id,
                item_id=item_id,
                source_id=source_id,
                text=body_text,
            )
            session.add(chunk)
        chunk.workspace_id = workspace_id
        chunk.item_id = item_id
        chunk.source_id = source_id
        chunk.text = body_text
        chunk.contextual_prefix = f"{knowledge_item.kind}: {knowledge_item.title}"
        chunk.metadata_json = {
            "source_ref": source_ref,
            "company_slug": company_slug,
        }
        chunk.citation = {
            "source_id": source_id,
            "item_id": item_id,
            "title": knowledge_item.title,
            "source_ref": source_ref,
        }
        chunk.embedding_state = "pending"


async def _upsert_knowledge_collection(
    session,
    *,
    owner_type: str,
    owner_id: str,
    workspace_id: int,
    collection_id: str,
) -> None:
    collection = await session.scalar(
        select(KnowledgeCollectionRecord).where(
            KnowledgeCollectionRecord.owner_type == owner_type,
            KnowledgeCollectionRecord.owner_id == owner_id,
            KnowledgeCollectionRecord.collection_id == collection_id,
        )
    )
    if collection is None:
        collection = KnowledgeCollectionRecord(
            owner_type=owner_type,
            owner_id=owner_id,
            workspace_id=workspace_id,
            collection_id=collection_id,
            title=collection_id,
        )
        session.add(collection)
    collection.workspace_id = workspace_id
    collection.title = collection_id
    collection.description = "Company source data"
    collection.tags = ["company_source"]
    collection.metadata_json = {"schema_version": "company_seed_collection.v1"}


async def _reset_agent_hermes_sessions(
    session,
    *,
    workspace_id: int,
    agent_id: int,
) -> int:
    agent_session_ids = (
        await session.scalars(
            select(AgentSession.id).where(
                AgentSession.workspace_id == workspace_id,
                AgentSession.agent_id == agent_id,
            )
        )
    ).all()
    if not agent_session_ids:
        return 0
    hermes_record_ids = (
        await session.scalars(
            select(HermesSessionRecord.id).where(
                HermesSessionRecord.workspace_id == workspace_id,
                HermesSessionRecord.agent_session_id.in_(agent_session_ids),
            )
        )
    ).all()
    if hermes_record_ids:
        await session.execute(
            delete(HermesSessionMessageRecord).where(
                HermesSessionMessageRecord.hermes_session_id.in_(hermes_record_ids)
            )
        )
        await session.execute(
            delete(HermesSessionRecord).where(
                HermesSessionRecord.id.in_(hermes_record_ids)
            )
        )
    return len(hermes_record_ids)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--workspace-id", type=int)
    parser.add_argument("--phone-number")
    parser.add_argument("--telegram-user-id", type=int)
    parser.add_argument("--mark-telegram-connected", action="store_true")
    parser.add_argument("--reset-hermes-sessions", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    payload = await seed(
        package_path=args.package,
        workspace_id=args.workspace_id,
        phone_number=args.phone_number,
        telegram_user_id=args.telegram_user_id,
        mark_telegram_connected=args.mark_telegram_connected,
        reset_hermes_sessions=args.reset_hermes_sessions,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))  # noqa: T201 — CLI seed-result output


if __name__ == "__main__":
    asyncio.run(_main())
