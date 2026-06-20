from __future__ import annotations

from app.schemas.agent import AgentCreate


def test_default_enabled_tools_use_real_catalog_tool():
    schema = AgentCreate(name="Sotuvchi")
    enabled = schema.tools_config["enabled_tools"]
    assert "catalog_core" not in enabled
    assert "knowledge_search_catalog" in enabled
