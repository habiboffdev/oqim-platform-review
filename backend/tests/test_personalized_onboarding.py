from __future__ import annotations

from types import SimpleNamespace

from httpx import AsyncClient


async def test_complete_onboarding_persists_business_basics_and_preferences(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "name": "Nafis Liboslar",
        "category": "fashion",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "Ayollar kiyimi va aksessuarlar",
            "message_volume": "10_50",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {
            "reply_mode": "draft",
            "safe_autopilot": False,
            "escalation_destination": "in_app",
            "quiet_hours": {"enabled": True, "start": "22:00", "end": "09:00"},
            "add_phone_later": True,
            "invite_team_later": False,
        },
    }

    res = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )

    assert res.status_code == 200
    data = res.json()
    assert data["onboarding_completed"] is True
    assert data["type"] == "fashion"
    assert data["description"] == "Ayollar kiyimi va aksessuarlar"
    assert data["trust_mode"] == "disabled"
    assert data["onboarding_profile"] == {
        "schema_version": "personalized_onboarding.v1",
        "business_profile": payload["business_profile"],
        "preferences": payload["preferences"],
        "sources": {},
        "owner_rules": {},
    }

    me = await client.get("/api/auth/me", headers=auth_headers)

    assert me.status_code == 200
    assert me.json()["onboarding_profile"] == data["onboarding_profile"]


async def test_complete_onboarding_seeds_retrievable_business_brain_facts(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "name": "Nafis Liboslar",
        "category": "fashion",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "Ayollar kiyimi va aksessuarlar",
            "message_volume": "10_50",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {
            "reply_mode": "draft",
            "safe_autopilot": False,
            "escalation_destination": "in_app",
            "quiet_hours": {"enabled": False, "start": "22:00", "end": "09:00"},
            "add_phone_later": True,
            "invite_team_later": True,
        },
    }

    res = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )
    assert res.status_code == 200

    memory = await client.post(
        "/api/business-brain/memory/retrieve",
        json={
            "requested_fact_types": [
                "business_profile_fact",
                "operating_preference_fact",
            ],
            "entity_refs": ["workspace:profile"],
            "requested_slots": [
                "business_profile_fact",
                "operating_preference_fact",
            ],
        },
        headers=auth_headers,
    )

    assert memory.status_code == 200
    body = memory.json()
    candidates = {item["fact_type"]: item for item in body["candidates"]}
    assert candidates["business_profile_fact"]["value"] == payload["business_profile"]
    assert candidates["operating_preference_fact"]["value"] == payload["preferences"]
    assert candidates["business_profile_fact"]["source_refs"] == ["onboarding:personalized_profile"]
    assert body["missing_evidence"] == []


async def test_complete_onboarding_provisions_agentic_os(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "name": "SATStation",
        "category": "courses",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "SAT kurslari va mentorlik",
            "message_volume": "50_200",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {
            "reply_mode": "draft",
            "safe_autopilot": False,
            "permission_mode": "ask_always",
            "default_agents": ["seller", "support", "catalog_update", "follow_up", "bi"],
        },
    }

    res = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )
    assert res.status_code == 200

    agents = await client.get("/api/intelligence/agents", headers=auth_headers)
    assert agents.status_code == 200
    agent_items = agents.json()["items"]
    assert {agent["agent_type"] for agent in agent_items} >= {
        "seller",
        "support",
        "catalog_update",
        "follow_up",
        "bi",
    }
    assert all(agent["skill_count"] >= 1 for agent in agent_items)

    business_md = await client.get("/api/business-brain/business-md", headers=auth_headers)
    assert business_md.status_code == 200
    rendered = business_md.json()["rendered"]
    assert rendered["sections_used"] >= 5
    assert "SAT kurslari" in rendered["markdown"]

    grants = await client.get("/api/intelligence/tool-grants", headers=auth_headers)
    assert grants.status_code == 200
    grant_scopes = {grant["scope"] for grant in grants.json()["items"]}
    assert "telegram.read_messages" in grant_scopes
    assert "telegram.send_message" in grant_scopes

    seller = next(agent for agent in agent_items if agent["agent_type"] == "seller")
    triggers = await client.get(
        f"/api/intelligence/agents/{seller['id']}/triggers",
        headers=auth_headers,
    )
    assert triggers.status_code == 200
    assert triggers.json()["items"]


async def test_complete_onboarding_seeds_sources_and_owner_rules_as_brain_memory(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "name": "Nafis Liboslar",
        "category": "fashion",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "Ayollar kiyimi va aksessuarlar",
            "message_volume": "10_50",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {"reply_mode": "draft", "safe_autopilot": False},
        "sources": {
            "notes": "Telegram kanal: @nafis_shop, katalog PDF bor",
        },
        "owner_rules": {
            "notes": "Yetkazib berish so'ralsa, avval tuman va telefon so'ra.",
        },
    }

    res = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["onboarding_profile"]["sources"] == payload["sources"]
    assert res.json()["onboarding_profile"]["owner_rules"] == payload["owner_rules"]

    memory = await client.post(
        "/api/business-brain/memory/retrieve",
        json={
            "requested_fact_types": [
                "business_source_fact",
                "seller_rule_fact",
            ],
            "entity_refs": ["workspace:sources", "workspace:rules"],
            "requested_slots": [
                "business_source_fact",
                "seller_rule_fact",
            ],
        },
        headers=auth_headers,
    )

    assert memory.status_code == 200
    candidates = {item["fact_type"]: item for item in memory.json()["candidates"]}
    assert candidates["business_source_fact"]["entity_ref"] == "workspace:sources"
    assert candidates["business_source_fact"]["value"] == payload["sources"]
    assert candidates["business_source_fact"]["source_refs"] == ["onboarding:sources"]
    assert candidates["seller_rule_fact"]["entity_ref"] == "workspace:rules"
    assert candidates["seller_rule_fact"]["value"] == payload["owner_rules"]
    assert candidates["seller_rule_fact"]["source_refs"] == ["onboarding:owner_rules"]


async def test_complete_onboarding_seeds_structured_sources_as_processing_queue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    class _NoopSourceLearningRuntime:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def process_workspace_sources(self, **kwargs):
            return SimpleNamespace(
                processed_count=0,
                review_ready_count=0,
                retrying_count=0,
                failed_count=0,
            )

    monkeypatch.setattr(
        "app.api.routes.auth.OnboardingSourceLearningRuntimeService",
        _NoopSourceLearningRuntime,
    )

    payload = {
        "name": "Nafis Liboslar",
        "category": "fashion",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "Ayollar kiyimi va aksessuarlar",
            "message_volume": "10_50",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {"reply_mode": "draft", "safe_autopilot": False},
        "sources": {
            "items": [
                {
                    "kind": "website",
                    "label": "Asosiy sayt",
                    "url": "https://nafis.example/shop",
                },
                {
                    "kind": "telegram_channel",
                    "label": "Kanal",
                    "handle": "@nafis_shop",
                },
                {
                    "kind": "file",
                    "label": "Katalog PDF",
                    "file_name": "catalog.pdf",
                    "content_type": "application/pdf",
                },
                {
                    "kind": "voice_note",
                    "label": "Ovozli qoida",
                    "transcript": "Chegirma faqat qaytgan mijozlarga.",
                    "purpose": "agent_data",
                },
            ],
        },
    }

    res = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )
    assert res.status_code == 200

    memory = await client.post(
        "/api/business-brain/memory/retrieve",
        json={
            "requested_fact_types": ["business_source_fact"],
            "requested_slots": ["business_source_fact"],
            "limit": 20,
        },
        headers=auth_headers,
    )

    assert memory.status_code == 200
    source_items = [item for item in memory.json()["candidates"] if item["entity_ref"].startswith("workspace:source:")]
    assert [item["value"]["kind"] for item in source_items] == [
        "website",
        "telegram_channel",
        "file",
        "voice_note",
    ]
    assert {item["value"]["processing"]["state"] for item in source_items} == {"queued"}
    assert [item["value"]["purpose"] for item in source_items] == [
        "brain_data",
        "brain_data",
        "brain_data",
        "agent_data",
    ]
    assert source_items[0]["value"]["input"]["url"] == "https://nafis.example/shop"
    assert source_items[1]["value"]["input"]["handle"] == "@nafis_shop"
    assert source_items[2]["value"]["input"]["file_name"] == "catalog.pdf"
    assert source_items[3]["value"]["input"]["transcript"] == "Chegirma faqat qaytgan mijozlarga."
    assert source_items[0]["source_refs"] == ["onboarding:source:0"]
