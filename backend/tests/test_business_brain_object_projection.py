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
            "correlation_id": f"object-projection:{fact_id}",
            "idempotency_key": f"object-projection:{fact_id}",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200


async def test_business_brain_objects_are_object_first_and_hide_raw_source_refs(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="knowledge:delivery",
        fact_type="knowledge_fact",
        entity_ref="knowledge:delivery",
        value={
            "topic": "Yetkazish hududi",
            "answer": "Toshkent bo‘yicha yetkazish 1 kun ichida.",
        },
        source_refs=["telegram:channel:delivery-post:42"],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="rule:payment",
        fact_type="seller_rule_fact",
        entity_ref="rules:payment",
        value={"rule": "To‘lov cheki ko‘rinmasa, qayta yuborishni so‘rang."},
        source_refs=["owner:manual:payment-rule"],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:price-pdf",
        fact_type="business_source_fact",
        entity_ref="workspace:source:price-pdf",
        value={
            "kind": "file",
            "label": "Narxlar PDF",
            "processing": {"state": "failed"},
        },
        source_refs=["brain:source:file:price-pdf"],
        # Confirmed facts remain active in the write contract; the projection
        # derives degraded source health from processing.state.
        status="active",
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:manual-note",
        fact_type="business_source_fact",
        entity_ref="workspace:source:onboarding:source:manual-note",
        value={
            "kind": "text",
            "label": "Manba",
            "input": {"text": "Narxni taxmin qilmang, avval modelini aniqlang."},
            "processing": {"state": "learned"},
        },
        source_refs=["onboarding:source:manual-note"],
        status="active",
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="knowledge:model-first",
        fact_type="knowledge_fact",
        entity_ref="knowledge:model-first",
        value={
            "topic": "Modelni aniqlash",
            "answer": "Narx aytishdan oldin mijozdan modelni aniqlang.",
        },
        source_refs=["source_unit:onboarding:source:manual-note:000"],
    )

    response = await client.get("/api/business-brain/objects", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "brain_object_projection.v1"
    objects = {item["object_id"]: item for item in body["objects"]}
    assert objects["knowledge:delivery"]["domain"] == "knowledge"
    assert objects["knowledge:delivery"]["title"] == "Yetkazish hududi"
    assert objects["knowledge:delivery"]["summary"] == "Toshkent bo‘yicha yetkazish 1 kun ichida."
    assert objects["knowledge:delivery"]["evidence"][0]["label"] == "Telegram: delivery-post"
    assert objects["knowledge:delivery"]["evidence"][0]["detail"] == "Kanal yoki chatdan olingan dalil."
    assert "telegram:channel" not in objects["knowledge:delivery"]["evidence"][0]["label"]

    assert objects["rule:payment"]["domain"] == "rules"
    assert objects["rule:payment"]["status_label"] == "Agentga tayyor"

    source = objects["source:price-pdf"]
    assert source["domain"] == "sources"
    assert source["source_lifecycle"] == "failed"
    assert source["status_label"] == "Yordam kerak"
    assert source["needs_review"] is True

    manual_source = objects["source:manual-note"]
    assert manual_source["title"] == "Qo‘lda yozilgan ma’lumot"
    assert manual_source["summary"] == "Narxni taxmin qilmang, avval modelini aniqlang."
    model_evidence = objects["knowledge:model-first"]["evidence"][0]
    assert model_evidence["label"] == "Qo‘lda yozilgan ma’lumot"
    assert model_evidence["unit_label"] == "matn bo‘lagi"
    assert model_evidence["detail"] == "Narxni taxmin qilmang, avval modelini aniqlang."
    assert model_evidence["label"] != "Manba"
    assert "source_unit" not in model_evidence["label"]

    assert body["counts"]["knowledge"] == 2
    assert body["counts"]["rules"] == 1
    assert body["counts"]["sources"] == 2
    assert body["issues_count"] >= 1


async def test_business_brain_objects_resolve_ingested_source_unit_evidence_labels(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="business_source:onboarding:source:satstation:ingested",
        fact_type="business_source_fact",
        entity_ref="workspace:source:onboarding:source:satstation",
        value={
            "kind": "website",
            "input": {"url": "https://satstation.io"},
            "text_preview": "SAT Station kurslari va xizmatlari.",
            "processing": {"state": "indexed", "source_unit_count": 1},
        },
        source_refs=["onboarding:source:satstation"],
        status="active",
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="knowledge:satstation-offer",
        fact_type="knowledge_fact",
        entity_ref="knowledge:satstation-offer",
        value={
            "topic": "SAT Station",
            "answer": "SAT Station SAT va IELTS tayyorgarligini taklif qiladi.",
        },
        source_refs=[
            "source_unit:business_source:onboarding:source:satstation:ingested:000"
        ],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="knowledge:orphan-source",
        fact_type="knowledge_fact",
        entity_ref="knowledge:orphan-source",
        value={
            "topic": "Orphan source",
            "answer": "Bu bilimda hali source fact yo‘q.",
        },
        source_refs=["source_unit:business_source:onboarding:source:orphan-unit:ingested:000"],
    )

    response = await client.get("/api/business-brain/objects", headers=auth_headers)

    assert response.status_code == 200
    objects = {item["object_id"]: item for item in response.json()["objects"]}
    evidence = objects["knowledge:satstation-offer"]["evidence"][0]
    assert evidence["label"] == "Sayt: satstation.io"
    assert evidence["detail"] == "O‘qildi: 1 ta matn bo‘lagi topildi."
    assert evidence["unit_label"] == "matn bo‘lagi"
    assert "source_unit" not in evidence["label"]

    fallback_evidence = objects["knowledge:orphan-source"]["evidence"][0]
    assert fallback_evidence["label"] == "Onboarding dalili: orphan unit"
    assert fallback_evidence["label"] != "Manba"
    assert fallback_evidence["unit_label"] == "matn bo‘lagi"


async def test_catalog_objects_resolve_source_identity_even_when_page_limit_excludes_source_fact(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="business_source:brain:source:satstation:ingested",
        fact_type="business_source_fact",
        entity_ref="workspace:source:brain:source:satstation",
        value={
            "kind": "website",
            "input": {"url": "https://satstation.io"},
            "processing": {"state": "completed", "source_unit_count": 3},
        },
        source_refs=["brain:source:satstation"],
        status="active",
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="catalog_product:sat-course",
        fact_type="catalog_product",
        entity_ref="catalog_product:sat-course",
        value={
            "title": "SAT kurs",
            "identity_ref": "catalog_product:sat-course",
            "description": "SAT tayyorgarlik kursi.",
        },
        source_refs=["source_unit:business_source:brain:source:satstation:ingested:000"],
        status="active",
    )

    response = await client.get("/api/business-brain/objects?limit=1", headers=auth_headers)

    assert response.status_code == 200
    objects = {item["object_id"]: item for item in response.json()["objects"]}
    evidence = objects["catalog_product:sat-course"]["evidence"][0]
    assert evidence["label"] == "Sayt: satstation.io"
    assert evidence["detail"] == "O‘qildi: 3 ta matn bo‘lagi topildi."
    assert evidence["unit_label"] == "matn bo‘lagi"


async def test_business_brain_sources_do_not_render_generic_manba_labels(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="source:unknown",
        fact_type="business_source_fact",
        entity_ref="workspace:source:onboarding:source:poster",
        value={
            "kind": "source",
            "label": "Manba",
            "input": {"file_name": "kurs-poster.png"},
            "processing": {"state": "completed", "source_unit_count": 2},
        },
        source_refs=["source_unit:business_source:onboarding:source:poster:ingested:000"],
        status="active",
    )

    response = await client.get("/api/business-brain/objects", headers=auth_headers)

    assert response.status_code == 200
    objects = {item["object_id"]: item for item in response.json()["objects"]}
    source = objects["source:unknown"]
    assert source["title"] == "Rasm: kurs-poster.png"
    assert source["summary"] == "O‘qildi: 2 ta matn bo‘lagi topildi."
    assert source["evidence"][0]["label"] == "Rasm: kurs-poster.png"
    assert source["evidence"][0]["label"] != "Manba"


async def test_business_brain_objects_can_filter_by_domain(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    await _write_fact(
        client,
        auth_headers,
        fact_id="knowledge:faq",
        fact_type="knowledge_fact",
        entity_ref="knowledge:faq",
        value={"topic": "FAQ", "answer": "Savollarga qisqa javob beriladi."},
        source_refs=["manual:faq"],
    )
    await _write_fact(
        client,
        auth_headers,
        fact_id="voice:greeting",
        fact_type="voice_style_fact",
        entity_ref="voice:greeting",
        value={"title": "Salomlashish", "summary": "Qisqa va iliq salom beradi."},
        source_refs=["conversation:example:1"],
    )

    response = await client.get(
        "/api/business-brain/objects?domain=voice",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["object_id"] for item in body["objects"]] == ["voice:greeting"]
    assert body["objects"][0]["domain"] == "voice"
