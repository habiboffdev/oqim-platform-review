"""Pipeline-config shape: derive the default multi-pipeline mapping on connect,
and normalize either config shape for readers.

``default_mapping(schema)`` (the generalized successor of the old
``build_default_pipeline_config``) maps EVERY pipeline (not just ``is_main``) and
derives each pipeline's ``role_map`` from stage ``sort`` with a relaxed clamp
(new=first active, qualified=last active, negotiation=a middle stage). Won/Lost
come from the terminal ``kind``. The stored snapshot uses the neutral
``CrmPipelineStatus`` projection (``stage_id``/``kind``), not amoCRM's raw
``id``/``type`` — ``discover_schema`` already normalizes.

``resolve_pipeline_view`` is the backward-compat READ shim: it turns the live
pilot's legacy flat config OR the new nested config into one pipeline's flat view,
so the flat pilot config keeps syncing with no prod write.

Pure functions — no I/O.
"""
from __future__ import annotations

from app.modules.crm_connector.contracts import CrmAccountSchema, CrmPipeline

_ACTIVE_ROLES = ("new", "negotiation", "qualified")


def _role_map_for_pipeline(pipeline: CrmPipeline) -> dict:
    """new=first active, qualified=last active (last-before-terminal),
    negotiation=a middle stage by sort. Clamps gracefully for <3 active stages.
    Won/Lost taken from the terminal statuses by kind."""
    active = sorted((s for s in pipeline.statuses if s.kind == "active"), key=lambda s: s.sort)
    role_map: dict[str, dict] = {}
    if active:
        picks = {
            "new": active[0],
            "negotiation": active[len(active) // 2],
            "qualified": active[-1],
        }
        for role, status in picks.items():
            role_map[role] = {"stage_id": status.stage_id, "name": status.name, "sort": status.sort}
    for kind in ("won", "lost"):
        terminal = next((s for s in pipeline.statuses if s.kind == kind), None)
        if terminal is not None:
            role_map[kind] = {
                "stage_id": terminal.stage_id, "name": terminal.name, "sort": terminal.sort,
            }
    return role_map


# Snapshot lists are sorted into a CANONICAL order before storage so the
# rediscovery idempotency check (snapshot == snapshot) is stable regardless of the
# order amoCRM happens to return collections in — a quiet account stays a no-op.
def _snapshot_pipeline(p: CrmPipeline) -> dict:
    return {
        "id": p.pipeline_id,
        "name": p.name,
        "is_main": p.is_main,
        "statuses": [
            {"stage_id": s.stage_id, "name": s.name, "sort": s.sort, "kind": s.kind}
            for s in sorted(p.statuses, key=lambda s: (s.sort, str(s.stage_id)))
        ],
    }


def _snapshot_field(f) -> dict:
    return {
        "id": f.key_id, "code": f.code, "name": f.name, "type": f.type,
        "enums": [
            {"id": e.enum_id, "value": e.value}
            for e in sorted(f.enums, key=lambda e: str(e.enum_id))
        ],
    }


def _snapshot_extras(schema: CrmAccountSchema) -> dict:
    return {
        "custom_fields": {
            entity: [
                _snapshot_field(f) for f in sorted(fields, key=lambda f: str(f.key_id))
            ]
            for entity, fields in (schema.custom_fields or {}).items()
        },
        "users": [
            {"id": u.user_id, "name": u.name}
            for u in sorted(schema.users, key=lambda u: str(u.user_id))
        ],
        "task_types": [
            {"id": t.task_type_id, "name": t.name}
            for t in sorted(schema.task_types, key=lambda t: str(t.task_type_id))
        ],
    }


def default_mapping(schema: CrmAccountSchema) -> dict:
    """Pure: CRM Account Schema -> the owner-overridable CRM Mapping config.
    Maps ALL pipelines; derives each role_map from sort (relaxed clamp). Never
    reads ids from anywhere but ``schema``."""
    pipelines = list(schema.pipelines)
    if not pipelines:
        return {
            "schema_version": 2,
            "snapshot": {"pipelines": [], **_snapshot_extras(schema)},
            "mapping": {"default_pipeline_id": None, "pipelines": {}},
        }
    default = next((p for p in pipelines if p.is_main), pipelines[0])
    return {
        "schema_version": 2,
        "snapshot": {
            "pipelines": [
                _snapshot_pipeline(p)
                for p in sorted(pipelines, key=lambda p: str(p.pipeline_id))
            ],
            **_snapshot_extras(schema),
        },
        "mapping": {
            "default_pipeline_id": default.pipeline_id,
            "pipelines": {
                p.pipeline_id: {"name": p.name, "role_map": _role_map_for_pipeline(p)}
                for p in pipelines
            },
        },
    }


def resolve_pipeline_view(pipeline_config: dict | None, pipeline_id: str | None = None) -> dict:
    """READ SHIM. Normalize the legacy flat config OR the nested v2 config into one
    pipeline's flat view: ``{pipeline_id, stage_map, snapshot_statuses}``. A flat
    legacy config resolves byte-identically to what readers extracted before, so
    the pilot keeps syncing with no prod write."""
    config = pipeline_config or {}
    mapping = config.get("mapping")
    if isinstance(mapping, dict):  # nested v2
        pid = str(pipeline_id or mapping.get("default_pipeline_id") or "")
        pm = (mapping.get("pipelines") or {}).get(pid) or {}
        statuses: list = []
        for p in (config.get("snapshot") or {}).get("pipelines") or []:
            if str(p.get("id")) == pid:
                statuses = p.get("statuses") or []
                break
        return {"pipeline_id": pid, "stage_map": pm.get("role_map") or {}, "snapshot_statuses": statuses}
    # legacy flat ({pipeline_id, stage_map, pipeline_snapshot})
    return {
        "pipeline_id": str(config.get("pipeline_id") or ""),
        "stage_map": config.get("stage_map") or {},
        "snapshot_statuses": config.get("pipeline_snapshot") or [],
    }


def default_pipeline_id(pipeline_config: dict | None) -> str | None:
    """The connection's default pipeline id, across either config shape. ``None``
    when unset (an empty config / pre-connect)."""
    config = pipeline_config or {}
    mapping = config.get("mapping")
    if isinstance(mapping, dict):
        return (str(mapping.get("default_pipeline_id")) if mapping.get("default_pipeline_id") else None)
    return (str(config.get("pipeline_id") or "") or None)


def pipeline_id_for_stage(pipeline_config: dict | None, stage_id: str | None) -> str | None:
    """Which snapshot pipeline contains ``stage_id`` (nested v2 or legacy flat), or
    ``None`` if not found. Used to detect a re-home: the pipeline the lead was last
    synced into (its ``last_synced_stage_id``) vs the link's pinned ``pipeline_id``."""
    if not stage_id:
        return None
    config = pipeline_config or {}
    snap = (config.get("snapshot") or {}).get("pipelines") or []
    if snap:
        for p in snap:
            for s in p.get("statuses") or []:
                if str(s.get("stage_id")) == str(stage_id):
                    return str(p.get("id"))
        return None
    for s in config.get("pipeline_snapshot") or []:
        if str(s.get("stage_id")) == str(stage_id):
            return str(config.get("pipeline_id") or "") or None
    return None


def snapshot_pipeline_ids(pipeline_config: dict | None) -> set[str]:
    """The set of pipeline ids in the stored snapshot (nested v2) or the legacy flat
    config — for validating a routing target exists before re-homing a lead."""
    config = pipeline_config or {}
    snap = (config.get("snapshot") or {}).get("pipelines") or []
    if snap:
        return {str(p.get("id")) for p in snap if p.get("id") is not None}
    pid = config.get("pipeline_id")
    return {str(pid)} if pid else set()


def validate_routing_pipeline_ids(
    pipelines: dict | None, pipeline_config: dict | None
) -> list[str]:
    """The pipeline ids in a routing map that are NOT present in the snapshot
    (sorted). Empty list = all valid."""
    known = snapshot_pipeline_ids(pipeline_config)
    return sorted({str(v) for v in (pipelines or {}).values()} - known)
