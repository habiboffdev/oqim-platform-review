"""No-reconnect schema re-discovery: re-read the live schema with the stored
token and refresh pipeline_config in place.

Invariants this module guards (from the S2 cross-seam review):
- PRESERVE the existing per-pipeline role->stage mapping on refresh. A pipeline
  OQIM is already syncing keeps its current role_map (only roles whose stage was
  deleted fall back to the freshly-derived default), so a refresh NEVER silently
  re-routes live leads — incl. upgrading the pilot's legacy FLAT config to nested.
- PRESERVE the webhook registration across refresh.
- NEVER overwrite a good config with an empty/partial discovery (a transient 204 /
  rate-limited body) — treat empty pipelines as a skip, not a schema change.
- Idempotent on an unchanged snapshot (no write, no card).
- A removed pipeline/stage a live lead references -> idempotent owner card, with
  drift computed shape-agnostically (the prod config is still legacy FLAT) and on
  namespaced ids (a pipeline id never masks a same-numbered stage id).
- The CONFIG refresh commits FIRST (it is the primary goal — a stale config breaks
  sync); the drift owner card is then a SEPARATE best-effort write that rolls itself
  back on failure, so a card-write failure can never undo the refresh nor poison the
  session for the next connection. (Tradeoff: a card lost to a *transient* failure
  is not retried, since the next poll sees an unchanged snapshot — the owner can
  still see the schema via `oqim crm schema`. Matches CrmTokenRefresher's
  best-effort-card model.)

KNOWN tail (shared with CrmTokenRefresher, single-pilot blast radius 1): if the
CONFIG commit itself fails mid-flush we roll back + re-raise so the session is clean
for the caller, but a DB read error inside one connection's processing could still
leave the shared per-tick session unusable for later connections. Per-connection
sessions are the deferred hardening for multi-tenant CRM load.

Shared by CrmSchemaRefresher and the `oqim crm rediscover` CLI."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.crm_connector.owner_cards import queue_crm_owner_notification
from app.modules.crm_connector.provider import CrmProvider
from app.modules.crm_connector.stage_map import default_mapping

logger = get_logger("crm.rediscovery")


def _existing_role_maps(old: dict | None) -> dict[str, dict]:
    """The connection's current effective role->stage mapping per pipeline, across
    BOTH config shapes: nested v2 (mapping.pipelines[pid].role_map) or legacy flat
    ({pipeline_id, stage_map}). Returns {pipeline_id: {role: {stage_id, ...}}}."""
    old = old or {}
    mapping = old.get("mapping")
    if isinstance(mapping, dict):
        return {
            str(pid): (pdata.get("role_map") or {})
            for pid, pdata in (mapping.get("pipelines") or {}).items()
        }
    pid = old.get("pipeline_id")
    if pid and old.get("stage_map"):
        return {str(pid): old["stage_map"]}
    return {}


def merge_rediscovered_config(old: dict | None, rebuilt: dict) -> dict:
    """rebuilt = default_mapping(fresh schema). Carry operational state + owner
    intent forward from the old config:
    - the webhook registration;
    - the existing per-pipeline role_map: for each pipeline present in BOTH old and
      new, keep the OLD role->stage assignment (refreshing the entry's name/sort
      from the new snapshot); a role whose old stage no longer exists falls back to
      the freshly-derived default. New pipelines keep their derived role_map.
    This is the S2 form of the spec's "preserve owner-edited mapping, refresh the
    raw snapshot + un-edited defaults" merge."""
    merged = dict(rebuilt)
    old = old or {}
    if old.get("webhook"):
        merged["webhook"] = old["webhook"]

    existing = _existing_role_maps(old)
    pipelines = (merged.get("mapping") or {}).get("pipelines") or {}
    snap_by_pid = {
        str(p.get("id")): p for p in (merged.get("snapshot") or {}).get("pipelines") or []
    }
    for pid, pdata in pipelines.items():
        old_rm = existing.get(str(pid))
        if not old_rm:
            continue
        by_stage = {
            str(s.get("stage_id")): {
                "stage_id": s.get("stage_id"), "name": s.get("name"), "sort": s.get("sort"),
            }
            for s in (snap_by_pid.get(str(pid), {}).get("statuses") or [])
        }
        preserved = dict(pdata.get("role_map") or {})
        for role, entry in old_rm.items():
            entry = entry if isinstance(entry, dict) else {}
            sid = str(entry.get("stage_id") or "")
            if sid in by_stage:
                # keep role->stage + any owner-set keys (S3 forward-seam), refresh
                # name/sort from the current snapshot.
                preserved[role] = {**entry, **by_stage[sid]}
        pdata["role_map"] = preserved
    return merged


def _all_ids(config: dict | None) -> set[str]:
    """Namespaced pipeline + stage ids in a config, shape-agnostic (nested v2 OR
    legacy flat). Namespacing ('pipe:'/'stage:') keeps a pipeline id from masking a
    same-numbered stage id in the drift set math."""
    config = config or {}
    ids: set[str] = set()
    snapshot_pipelines = ((config.get("snapshot") or {}).get("pipelines")) or []
    if snapshot_pipelines:  # nested v2
        for p in snapshot_pipelines:
            ids.add(f"pipe:{p.get('id')}")
            for s in p.get("statuses") or []:
                ids.add(f"stage:{s.get('stage_id')}")
        return ids
    # legacy flat ({pipeline_id, pipeline_snapshot})
    if config.get("pipeline_id"):
        ids.add(f"pipe:{config['pipeline_id']}")
    for s in config.get("pipeline_snapshot") or []:
        ids.add(f"stage:{s.get('stage_id')}")
    return ids


def _config_has_pipelines(config: dict | None) -> bool:
    """Does the stored config already describe at least one pipeline (either shape)?
    Used to refuse replacing a populated config with an empty discovery."""
    config = config or {}
    if ((config.get("snapshot") or {}).get("pipelines")):
        return True
    return bool(config.get("pipeline_snapshot")) or bool(config.get("pipeline_id"))


def schema_drift(old: dict | None, new: dict, *, referenced_ids: set[str]) -> list[str]:
    """Namespaced ids that existed before, are GONE now, and a live lead references."""
    return sorted((_all_ids(old) - _all_ids(new)) & referenced_ids)


async def _referenced_ids(session: AsyncSession, connection_id: int) -> set[str]:
    """Namespaced pipeline/stage ids referenced by this connection's lead links.

    NOTE: this counts EVERY lead-link row (there is no liveness/closed flag in the
    model yet), so deleting a stage only long-closed leads ever touched can still
    card the owner. That over-cards (never under-cards) — a safe bias for a safety
    signal; a precise liveness filter is a deferred follow-up."""
    rows = (await session.execute(
        select(CrmLeadLink.pipeline_id, CrmLeadLink.last_synced_stage_id)
        .where(CrmLeadLink.connection_id == connection_id)
    )).all()
    out: set[str] = set()
    for pid, sid in rows:
        if pid:
            out.add(f"pipe:{pid}")
        if sid:
            out.add(f"stage:{sid}")
    return out


async def rediscover_connection(
    session: AsyncSession, conn: CrmConnection, provider: CrmProvider
) -> bool:
    """Re-read the live schema and refresh conn.pipeline_config. Returns True when
    the config changed. No-op (no write, no card) on an unchanged snapshot or an
    empty/transient discovery. The config write + any drift card commit in ONE
    transaction; a commit failure propagates (the caller rolls back + isolates)."""
    fresh = await provider.discover_schema(conn)
    old_config = conn.pipeline_config or {}

    # Refuse to clobber a populated config with an empty discovery (transient
    # 204 / rate-limited / truncated body). Matches the "skip this tick, no
    # degrade" contract — the next poll re-reads the real schema.
    if not fresh.pipelines and _config_has_pipelines(old_config):
        logger.warning("crm.rediscovery.empty_discovery_skipped conn=%s", conn.id)
        return False

    rebuilt = default_mapping(fresh)
    merged = merge_rediscovered_config(old_config, rebuilt)
    if (merged.get("snapshot") or {}) == (old_config.get("snapshot") or {}):
        return False

    referenced = await _referenced_ids(session, conn.id)
    drift = schema_drift(old_config, merged, referenced_ids=referenced)
    conn.pipeline_config = merged  # reassign so SQLAlchemy marks the JSONB dirty
    try:
        await session.commit()  # the config refresh is the primary goal — land it first
    except Exception:
        await session.rollback()  # keep the session usable for the next connection
        raise
    if drift:
        await _queue_drift_card(session, conn, drift)
    return True


async def _queue_drift_card(session: AsyncSession, conn: CrmConnection, drift: list[str]) -> None:
    """Best-effort owner card AFTER the config refresh already committed: a card
    failure rolls itself back (so it never undoes the refresh nor poisons the
    session) and is swallowed. Idempotent on the drift key."""
    try:
        await queue_crm_owner_notification(
            session,
            workspace_id=conn.workspace_id,
            title="amoCRM sxemasi o'zgardi",
            summary="OQIM ishlatayotgan bosqich yoki voronka amoCRM dan o'chirilgan.",
            recommended_action="Integratsiyalar > amoCRM bo'limidagi sozlamalarni tekshiring.",
            idempotency_key=f"crm_schema_drift:{conn.id}:{':'.join(drift)}",
        )
        await session.commit()
    except Exception as card_exc:
        await session.rollback()
        logger.error("crm.rediscovery.card_failed conn=%s error=%s",
                     conn.id, type(card_exc).__name__)
