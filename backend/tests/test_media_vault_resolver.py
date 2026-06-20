"""Media vault (spike #439): reusable per-workspace asset pointers.

The owner curates intro videos / media once; the seller sends them by handle via
the existing talk.send_media path. This file proves the model roundtrip, the
seller-side resolver third source, and the owner media.store/media.list tools.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from app.models.media_vault import MediaVaultRecord
from app.modules.telegram_tools.contracts import TELEGRAM_SEND_MESSAGE
from app.modules.telegram_tools.runtime import TelegramToolRuntime
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.channel_adapter_contract import ChannelCapabilities
from app.services.delivery import DeliveryResult

pytestmark = pytest.mark.asyncio


@dataclass
class _FakeDelivery:
    media_calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(self, conversation_id, text, **kw):
        return DeliveryResult(success=True, external_message_id="9001", state="confirmed")

    async def deliver_media(self, conversation_id, media, **kw):
        self.media_calls.append({"conversation_id": conversation_id, "media": media, **kw})
        return DeliveryResult(success=True, external_message_id="9002", state="confirmed")


@dataclass
class _FakeAdapter:
    channel: str = "telegram_dm"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            send_message=True,
            edit_message=True,
            send_reaction=True,
            fetch_history=True,
            fetch_media_blob=True,
        )


async def test_media_vault_record_roundtrip(db_session, workspace):
    rec = MediaVaultRecord(
        workspace_id=workspace.id,
        handle="intro_kozimxon",
        media_type="video",
        cdn_url="https://cdn.example.com/intro/kozimxon.mp4",
        mime_type="video/mp4",
        file_name="kozimxon.mp4",
        caption="Murabbiy-aka tanishuv",
        created_by="owner",
    )
    db_session.add(rec)
    await db_session.flush()

    fetched = await db_session.scalar(
        select(MediaVaultRecord).where(
            MediaVaultRecord.workspace_id == workspace.id,
            MediaVaultRecord.handle == "intro_kozimxon",
        )
    )
    assert fetched is not None
    assert fetched.media_type == "video"
    assert fetched.cdn_url == "https://cdn.example.com/intro/kozimxon.mp4"
    assert fetched.mime_type == "video/mp4"
    assert fetched.caption == "Murabbiy-aka tanishuv"
    # Telegram-cloud pointer columns are nullable until the sidecar vault lands.
    assert fetched.vault_peer is None
    assert fetched.document_id is None
    assert fetched.access_hash is None
    assert fetched.file_reference is None


async def test_media_vault_document_pointer_row_inserts(db_session, workspace):
    """A channel-anchored asset has no cdn_url, only a (peer, message_id) pointer."""
    rec = MediaVaultRecord(
        workspace_id=workspace.id,
        handle="intro_kozimxon_doc",
        media_type="video",
        cdn_url=None,
        vault_peer="-1001234567890",
        vault_message_id=42,
        mime_type="video/mp4",
        file_name="kozimxon.mp4",
        caption="Murabbiy-aka tanishuv",
        created_by="owner",
    )
    db_session.add(rec)
    await db_session.flush()
    fetched = await db_session.scalar(
        select(MediaVaultRecord).where(MediaVaultRecord.handle == "intro_kozimxon_doc")
    )
    assert fetched.cdn_url is None
    assert fetched.vault_peer == "-1001234567890"
    assert fetched.vault_message_id == 42


async def test_media_vault_rejects_row_without_url_or_pointer(db_session, workspace):
    from sqlalchemy.exc import IntegrityError

    rec = MediaVaultRecord(
        workspace_id=workspace.id,
        handle="bad_no_addr",
        media_type="video",
        cdn_url=None,
        vault_message_id=None,
    )
    db_session.add(rec)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_send_media_resolves_vault_handle(db_session, workspace, agent, conversation):
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE),
    )
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon",
            media_type="video",
            cdn_url="https://cdn.example.com/intro/kozimxon.mp4",
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
            created_by="owner",
        )
    )
    await db_session.flush()

    delivery = _FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=_FakeAdapter())

    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref="intro_kozimxon",
        caption="Murabbiy-aka tanishuv",
        correlation_id="test-vault-send",
        idempotency_key="vault-send-1",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    media = delivery.media_calls[0]["media"]
    assert media.url == "https://cdn.example.com/intro/kozimxon.mp4"
    assert media.media_type == "video"
    assert media.asset_id == "intro_kozimxon"


async def test_resolver_returns_document_pointer_for_pointer_record(db_session, workspace):
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon",
            media_type="video",
            cdn_url=None,
            vault_peer="-1001234567890",
            vault_message_id=42,
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
        )
    )
    await db_session.flush()

    runtime = TelegramToolRuntime(db_session, delivery=_FakeDelivery(), adapter=_FakeAdapter())
    media = await runtime._resolve_outbound_media(
        workspace_id=workspace.id, media_ref="intro_kozimxon"
    )
    assert media is not None
    assert media.vault_peer == "-1001234567890"
    assert media.vault_message_id == 42
    assert media.media_type == "video"
    assert media.url == "vault://-1001234567890/42"  # non-empty internal locator
    assert media.asset_id == "intro_kozimxon"


async def test_resolver_does_not_carry_snapshot_caption_for_document_pointer(db_session, workspace):
    """Document-pointer assets read their caption LIVE from the channel post (sidecar),
    not from the stored snapshot — so the resolver must NOT carry the stored caption."""
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon",
            media_type="video",
            cdn_url=None,
            vault_peer="-1001234567890",
            vault_message_id=42,
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
            caption="Ustoz Murabbiy haqida qisqacha",
        )
    )
    await db_session.flush()

    runtime = TelegramToolRuntime(db_session, delivery=_FakeDelivery(), adapter=_FakeAdapter())
    media = await runtime._resolve_outbound_media(
        workspace_id=workspace.id, media_ref="intro_kozimxon"
    )
    assert media is not None
    # The stored snapshot must NOT shadow the live channel caption.
    assert media.caption is None


async def test_resolver_carries_stored_caption_for_cdn_url(db_session, workspace):
    """A cdn_url vault record's stored caption rides on the resolved media — a url asset
    has no channel message to read, so the snapshot is the only caption source."""
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon",
            media_type="video",
            cdn_url="https://cdn.example.com/intro/kozimxon.mp4",
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
            caption="Ustoz Murabbiy haqida qisqacha",
        )
    )
    await db_session.flush()

    runtime = TelegramToolRuntime(db_session, delivery=_FakeDelivery(), adapter=_FakeAdapter())
    media = await runtime._resolve_outbound_media(
        workspace_id=workspace.id, media_ref="intro_kozimxon"
    )
    assert media is not None
    assert media.caption == "Ustoz Murabbiy haqida qisqacha"


async def test_send_media_document_pointer_does_not_leak_snapshot_caption(
    db_session, workspace, agent, conversation
):
    """send_media(caption=None) on a DOCUMENT-pointer asset passes NO caption downstream,
    letting the sidecar fill it from the live channel post (Part A)."""
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE),
    )
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon_doc",
            media_type="video",
            cdn_url=None,
            vault_peer="-1001234567890",
            vault_message_id=42,
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
            caption="STALE SNAPSHOT",
            created_by="owner",
        )
    )
    await db_session.flush()

    delivery = _FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=_FakeAdapter())

    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref="intro_kozimxon_doc",
        caption=None,
        correlation_id="test-vault-doc-no-snapshot",
        idempotency_key="vault-doc-no-snapshot-1",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    # No stored-snapshot caption leaks — the sidecar fills the live channel caption.
    assert not delivery.media_calls[0]["caption"]


async def test_send_media_defaults_to_stored_caption(
    db_session, workspace, agent, conversation
):
    """send_media(caption=None) falls back to the vault record's stored caption."""
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE),
    )
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle="intro_kozimxon",
            media_type="video",
            cdn_url="https://cdn.example.com/intro/kozimxon.mp4",
            mime_type="video/mp4",
            file_name="kozimxon.mp4",
            caption="Ustoz Murabbiy haqida qisqacha",
            created_by="owner",
        )
    )
    await db_session.flush()

    delivery = _FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=_FakeAdapter())

    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref="intro_kozimxon",
        caption=None,
        correlation_id="test-vault-default-caption",
        idempotency_key="vault-default-caption-1",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    assert delivery.media_calls[0]["caption"] == "Ustoz Murabbiy haqida qisqacha"


async def test_catalog_media_wins_over_vault_handle(db_session, workspace, agent, conversation):
    """The vault is the THIRD source — approved catalog media for the same ref
    must take precedence (the vault must not shadow catalog authority)."""
    from app.models.commerce_catalog import CatalogMediaRecord

    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE),
    )
    ref = "shared_handle:hero"
    db_session.add(
        CatalogMediaRecord(
            workspace_id=workspace.id,
            media_ref=ref,
            product_ref="catalog_product:x",
            media_kind="image",
            url="https://cdn.example.com/catalog/authoritative.jpg",
            caption="Catalog",
            ocr_text="",
            visual_summary="",
            authority_state="approved",
            source_refs=["source:x"],
            source_fact_ids=["fact:x"],
            metadata_={"content_type": "image/jpeg", "file_name": "authoritative.jpg"},
        )
    )
    db_session.add(
        MediaVaultRecord(
            workspace_id=workspace.id,
            handle=ref,
            media_type="video",
            cdn_url="https://cdn.example.com/vault/should-not-win.mp4",
            created_by="owner",
        )
    )
    await db_session.flush()

    delivery = _FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=_FakeAdapter())
    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref=ref,
        correlation_id="test-precedence",
        idempotency_key="precedence-1",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    assert (
        delivery.media_calls[0]["media"].url
        == "https://cdn.example.com/catalog/authoritative.jpg"
    )


async def test_media_store_and_list(db_session, workspace, monkeypatch):
    from contextlib import asynccontextmanager

    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        _media_list_async,
        _media_store_async,
    )

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session
    )

    stored = await _media_store_async(
        workspace_id=workspace.id,
        handle="intro_hr",
        cdn_url="https://cdn.example.com/hr.mp4",
        media_type="video",
        caption="HR kursi tanishuv",
    )
    assert stored["status"] == "ok"
    assert stored["handle"] == "intro_hr"

    listed = await _media_list_async(workspace_id=workspace.id)
    assert listed["status"] == "ok"
    handles = {item["handle"] for item in listed["items"]}
    assert "intro_hr" in handles

    # re-store same handle upserts (no duplicate row, unique (workspace, handle))
    again = await _media_store_async(
        workspace_id=workspace.id,
        handle="intro_hr",
        cdn_url="https://cdn.example.com/hr-v2.mp4",
        media_type="video",
    )
    assert again["updated"] is True
    relisted = await _media_list_async(workspace_id=workspace.id)
    assert sum(1 for i in relisted["items"] if i["handle"] == "intro_hr") == 1

    # the upsert really changed the stored URL (not just a flag)
    fetched = await db_session.scalar(
        select(MediaVaultRecord).where(
            MediaVaultRecord.workspace_id == workspace.id,
            MediaVaultRecord.handle == "intro_hr",
        )
    )
    assert fetched.cdn_url == "https://cdn.example.com/hr-v2.mp4"


async def test_media_store_rejects_non_http_url(db_session, workspace, monkeypatch):
    from contextlib import asynccontextmanager

    from app.modules.agent_runtime_v2.hermes.oqim_tools import (
        _media_list_async,
        _media_store_async,
    )

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session
    )

    out = await _media_store_async(
        workspace_id=workspace.id,
        handle="bad_asset",
        cdn_url="ftp://evil.example.com/x.mp4",
        media_type="video",
    )
    assert out["status"] == "blocked"
    assert out["reason"] == "invalid_url"

    # rejected, not persisted
    listed = await _media_list_async(workspace_id=workspace.id)
    assert all(item["handle"] != "bad_asset" for item in listed["items"])
