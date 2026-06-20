from __future__ import annotations

from httpx import AsyncClient


async def _write_fact(
    client: AsyncClient,
    auth_headers: dict[str, str],
    *,
    fact_id: str,
    fact_type: str,
    entity_ref: str,
    value: dict,
    source_refs: list[str],
    status: str = "active",
    risk_tier: str = "low",
) -> None:
    response = await client.post(
        "/api/business-brain/memory/facts",
        json={
            "fact_id": fact_id,
            "fact_type": fact_type,
            "entity_ref": entity_ref,
            "value": value,
            "source_refs": source_refs,
            "source": "manual",
            "status": status,
            "approval_state": "confirmed",
            "confidence": 0.88,
            "risk_tier": risk_tier,
            "correlation_id": f"source-projection:{fact_id}",
            "idempotency_key": f"source-projection:{fact_id}",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200


async def test_source_intake_projection_is_continuous_and_owner_facing(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:catalog-channel",
        fact_type="business_source_fact",
        entity_ref="workspace:source:telegram:channel:catalog",
        value={
            "kind": "telegram_channel",
            "label": "@catalog",
            "input": {"handle": "@catalog"},
            "processing": {"state": "completed", "source_unit_count": 4, "source_media_count": 2},
        },
        source_refs=["telegram:channel:catalog"],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="catalog:air-filter",
        fact_type="catalog_product_fact",
        entity_ref="catalog:air-filter",
        value={"title": "Havo tozalagich", "description": "20-30 m2 xona uchun."},
        source_refs=["telegram:channel:catalog"],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:bad-pdf",
        fact_type="business_source_fact",
        entity_ref="workspace:source:brain:source:file:bad-pdf",
        value={
            "kind": "file",
            "label": "Narxlar PDF",
            "input": {"file_name": "narxlar.pdf"},
            "processing": {
                "state": "failed",
                "source_unit_count": 0,
                "source_media_count": 0,
                "degraded_reasons": ["fetch_failed", "no_source_evidence"],
            },
        },
        source_refs=["brain:source:file:bad-pdf"],
    )

    response = await client.get("/api/business-brain/source-intake", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "source_intake_projection.v1"
    sources = {item["source_ref"]: item for item in body["sources"]}

    channel = sources["telegram:channel:catalog"]
    assert channel["title"] == "@catalog"
    assert channel["kind_label"] == "Telegram kanal"
    assert channel["purpose_label"] == "Javob ma'lumoti"
    assert channel["lifecycle"] == "live"
    assert channel["can_pause"] is True
    assert channel["can_resume"] is False
    assert channel["learned_object_count"] == 1
    assert channel["learned_object_labels"] == ["Katalog"]
    assert channel["source_unit_count"] == 4
    assert channel["media_count"] == 2

    bad_pdf = sources["brain:source:file:bad-pdf"]
    assert bad_pdf["title"] == "Narxlar PDF"
    assert bad_pdf["kind_label"] == "Fayl"
    assert bad_pdf["lifecycle"] == "failed"
    assert bad_pdf["status_label"] == "Yordam kerak"
    assert bad_pdf["can_retry"] is True
    assert bad_pdf["issue_label"] in {
        "Manbaga ulanishda muammo bo'ldi.",
        "Manbani tekshirish kerak.",
    }

    visible = " ".join(
        str(bad_pdf[key])
        for key in ("title", "summary", "issue_label", "status_label", "preview")
    )
    assert "fetch_failed" not in visible
    assert "no_source_evidence" not in visible
    assert "brain:source:" not in visible
    assert body["live_count"] == 1
    assert body["failed_count"] == 1


async def test_source_intake_projection_replaces_generic_source_labels(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:poster",
        fact_type="business_source_fact",
        entity_ref="workspace:source:onboarding:source:poster",
        value={
            "kind": "source",
            "label": "Manba",
            "input": {"file_name": "kurs-poster.png"},
            "processing": {"state": "completed"},
        },
        source_refs=["onboarding:source:poster"],
    )

    response = await client.get("/api/business-brain/source-intake", headers=auth_headers)

    assert response.status_code == 200
    sources = {item["source_ref"]: item for item in response.json()["sources"]}
    source = sources["onboarding:source:poster"]
    assert source["title"] == "kurs-poster.png"
    assert source["kind_label"] == "Dalil"
    assert source["preview"] == "kurs-poster.png"
    assert source["summary"] == "Saqlangan dalil. O'qish natijalari tayyor bo'lganda shu yerda ko'rinadi."
    assert source["title"] != "Manba"


async def test_source_intake_controls_pause_resume_and_archive_existing_source(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:watchable-channel",
        fact_type="business_source_fact",
        entity_ref="workspace:source:telegram:channel:watchable",
        value={
            "kind": "telegram_channel",
            "label": "@watchable",
            "input": {"handle": "@watchable"},
            "processing": {"state": "completed", "source_unit_count": 2},
        },
        source_refs=["telegram:channel:watchable"],
    )

    pause_response = await client.post(
        "/api/business-brain/source-intake/actions",
        json={"source_ref": "telegram:channel:watchable", "action": "pause"},
        headers=auth_headers,
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["action"] == "pause"

    projection_response = await client.get("/api/business-brain/source-intake", headers=auth_headers)
    paused = {
        item["source_ref"]: item for item in projection_response.json()["sources"]
    }["telegram:channel:watchable"]
    assert paused["lifecycle"] == "snapshot"
    assert paused["can_pause"] is False
    assert paused["can_resume"] is True

    resume_response = await client.post(
        "/api/business-brain/source-intake/actions",
        json={"source_ref": "telegram:channel:watchable", "action": "resume"},
        headers=auth_headers,
    )
    assert resume_response.status_code == 200
    resumed_projection = await client.get("/api/business-brain/source-intake", headers=auth_headers)
    resumed = {
        item["source_ref"]: item for item in resumed_projection.json()["sources"]
    }["telegram:channel:watchable"]
    assert resumed["lifecycle"] == "live"
    assert resumed["can_pause"] is True

    archive_response = await client.post(
        "/api/business-brain/source-intake/actions",
        json={"source_ref": "telegram:channel:watchable", "action": "archive"},
        headers=auth_headers,
    )
    assert archive_response.status_code == 200
    archived_projection = await client.get("/api/business-brain/source-intake", headers=auth_headers)
    archived = {
        item["source_ref"]: item for item in archived_projection.json()["sources"]
    }["telegram:channel:watchable"]
    assert archived["lifecycle"] == "archived"
    assert archived["can_archive"] is False
