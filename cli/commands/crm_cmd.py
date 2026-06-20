"""oqim crm — inspect + refresh the amoCRM schema (no reconnect)."""
import asyncio
import contextlib
import json

import typer

import cli.agentio as agentio
from cli._paths import ensure_backend_path
from cli.output import header

app = typer.Typer(no_args_is_help=True)


async def _active_connection(db, workspace: int):
    from sqlalchemy import select

    from app.models.crm_connection import CrmConnection

    return await db.scalar(
        select(CrmConnection)
        .where(CrmConnection.workspace_id == workspace, CrmConnection.status == "active")
        .limit(1)
    )


def _no_connection(workspace: int, *, json_mode: bool) -> None:
    """Emit the no-active-connection result (structured in JSON mode) + exit 1."""
    if json_mode:
        typer.echo(json.dumps({"workspace": workspace, "error": "no_active_connection"}))
    else:
        typer.echo(f"  No active CRM connection for workspace {workspace}.")
    raise typer.Exit(1)


@app.command("schema")
def schema(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Print OQIM's stored amoCRM schema (pipelines, stages, custom fields)."""
    asyncio.run(_schema_impl(workspace=workspace, json_mode=json_mode))


async def _schema_impl(*, workspace: int, json_mode: bool) -> None:
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON  # honor the root --json flag too
    from app.db.session import async_session

    async with async_session() as db:
        conn = await _active_connection(db, workspace)
        if conn is None:
            _no_connection(workspace, json_mode=json_mode)
        config = conn.pipeline_config or {}

    snap = config.get("snapshot") or {}
    if json_mode:
        typer.echo(json.dumps(snap, indent=2, ensure_ascii=False, default=str))
        return
    header(f"CRM Schema — workspace {workspace}")
    pipelines = snap.get("pipelines") or []
    typer.echo(f"\n  pipelines: {len(pipelines)}")
    for p in pipelines:
        typer.echo(f"   - {p.get('id')} \"{p.get('name')}\" ({len(p.get('statuses') or [])} stages)")
    for entity, fields in (snap.get("custom_fields") or {}).items():
        typer.echo(f"  {entity} custom fields: {len(fields)}")
    if not pipelines and config.get("pipeline_id"):
        typer.echo(
            f"  (legacy flat config, single pipeline {config['pipeline_id']} — "
            "run 'oqim crm rediscover' to upgrade)"
        )


@app.command("rediscover")
def rediscover(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Re-read the live amoCRM schema now (no reconnect) and refresh OQIM's config."""
    asyncio.run(_rediscover_impl(workspace=workspace, json_mode=json_mode))


async def _rediscover_impl(*, workspace: int, json_mode: bool) -> None:
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON  # honor the root --json flag too
    from app.db.session import async_session
    from app.modules.crm_connector.factory import provider_for
    from app.modules.crm_connector.rediscovery import rediscover_connection

    async with async_session() as db:
        conn = await _active_connection(db, workspace)
        if conn is None:
            _no_connection(workspace, json_mode=json_mode)
        changed = await rediscover_connection(db, conn, provider_for(conn.provider))

    if json_mode:
        typer.echo(json.dumps({"workspace": workspace, "changed": changed}, default=str))
        return
    header(f"CRM Re-discover — workspace {workspace}")
    typer.echo("")
    typer.echo("  schema refreshed (config changed)" if changed
               else "  no change (schema already current)")


route_app = typer.Typer(no_args_is_help=True)
app.add_typer(route_app, name="route", help="Inspect + set per-agent lead-routing config (no reconnect)")


async def _agent_in_workspace(db, workspace: int, agent: int):
    from app.models.agent import Agent

    a = await db.get(Agent, agent)
    if a is None or a.workspace_id != workspace:
        return None
    return a


@route_app.command("show")
def route_show(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    agent: int = typer.Option(..., "--agent", "-a", help="Agent ID (the seller)"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Print the agent's lead-routing config + flag any mapped id missing from the snapshot."""
    asyncio.run(_route_show_impl(workspace=workspace, agent=agent, json_mode=json_mode))


async def _route_show_impl(*, workspace: int, agent: int, json_mode: bool) -> None:
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session
    from app.modules.agent_runtime_v2.config_loader import resolve_crm_routing
    from app.modules.crm_connector.stage_map import validate_routing_pipeline_ids

    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        if a is None:
            if json_mode:
                typer.echo(json.dumps({"workspace": workspace, "agent": agent, "error": "no_agent"}))
            else:
                typer.echo(f"  No agent {agent} in workspace {workspace}.")
            raise typer.Exit(1)
        routing = resolve_crm_routing(a.channel_config or {})
        conn = await _active_connection(db, workspace)
        config = (conn.pipeline_config or {}) if conn is not None else {}

    # only validate ids against the snapshot when a connection actually exists —
    # otherwise every id would be falsely flagged "not in snapshot" (#8).
    stale = (validate_routing_pipeline_ids((routing or {}).get("pipelines"), config)
             if (routing and conn is not None) else [])
    if json_mode:
        typer.echo(json.dumps({"workspace": workspace, "agent": agent,
                               "routing": routing, "stale_ids": stale},
                              ensure_ascii=False, default=str))
        return
    header(f"CRM Routing — workspace {workspace}, agent {agent}")
    if not routing:
        typer.echo("\n  (no routing configured — every lead on the default pipeline)")
        return
    typer.echo(f"\n  default: {routing.get('default')}")
    for k, v in (routing.get("pipelines") or {}).items():
        flag = "  (!) not in snapshot" if v in stale else ""
        typer.echo(f"   - {k} -> {v}{flag}")
    typer.echo(f"  instructions: {routing.get('instructions') or '(none)'}")


@route_app.command("set")
def route_set(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    agent: int = typer.Option(..., "--agent", "-a", help="Agent ID (the seller)"),
    pipelines: str = typer.Option(..., "--map", help="key=pipeline_id,key=pipeline_id"),
    default: str = typer.Option(None, "--default", help="default logical key"),
    instructions: str = typer.Option("", "--instructions", help="routing guidance for the seller"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Set the agent's lead-routing config (validates every id against the snapshot)."""
    asyncio.run(_route_set_impl(workspace=workspace, agent=agent, pipelines=pipelines,
                                default=default, instructions=instructions, json_mode=json_mode))


async def _route_set_impl(*, workspace: int, agent: int, pipelines: str, default: str | None,
                          instructions: str, json_mode: bool) -> None:
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session
    from app.modules.crm_connector.stage_map import validate_routing_pipeline_ids

    pmap: dict[str, str] = {}
    for pair in (pipelines or "").split(","):
        pair = pair.strip()
        if pair and "=" in pair:
            k, v = pair.split("=", 1)
            pmap[k.strip()] = v.strip()

    if not pmap:
        typer.echo(json.dumps({"error": "empty_or_malformed_map"}) if json_mode
                   else "  Rejected — --map parsed no key=pipeline_id pairs (use key=id,key=id)")
        raise typer.Exit(1)
    if default and default not in pmap:
        typer.echo(json.dumps({"error": "default_not_in_map", "default": default}) if json_mode
                   else f"  Rejected — --default '{default}' is not a key in --map")
        raise typer.Exit(1)

    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        if a is None:
            typer.echo(json.dumps({"error": "no_agent"}) if json_mode
                       else f"  No agent {agent} in workspace {workspace}.")
            raise typer.Exit(1)
        conn = await _active_connection(db, workspace)
        if conn is None:
            typer.echo(json.dumps({"workspace": workspace, "error": "no_active_connection"})
                       if json_mode else f"  No active CRM connection for workspace {workspace}.")
            raise typer.Exit(1)
        config = conn.pipeline_config or {}
        stale = validate_routing_pipeline_ids(pmap, config)
        if stale:
            typer.echo(json.dumps({"error": "unknown_pipeline_ids", "ids": stale}) if json_mode
                       else f"  Rejected — pipeline ids not in the snapshot: {', '.join(stale)}")
            raise typer.Exit(1)
        cfg = dict(a.channel_config or {})
        crm = dict(cfg.get("crm") or {})
        crm["routing"] = {"pipelines": pmap, "default": default, "instructions": instructions}
        cfg["crm"] = crm
        a.channel_config = cfg
        await db.commit()

    if json_mode:
        typer.echo(json.dumps({"workspace": workspace, "agent": agent, "routing": crm["routing"]},
                              ensure_ascii=False, default=str))
        return
    header(f"CRM Routing set — workspace {workspace}, agent {agent}")
    typer.echo(f"\n  default: {default}")
    for k, v in pmap.items():
        typer.echo(f"   - {k} -> {v}")


# --------------------------------------------------------------------------- #
# S4: per-agent CRM custom fields + tags (owner config surface, no reconnect).
# --------------------------------------------------------------------------- #
fields_app = typer.Typer(no_args_is_help=True)
app.add_typer(fields_app, name="fields", help="Inspect + bless per-agent CRM custom fields (no reconnect)")


def _snapshot_field_ids(conn, entity: str = "leads") -> set[str]:
    """The discovered custom-field ids for ``entity`` ("leads"|"contacts") from the
    connection snapshot (S2). Logical "lead"/"contact" map to the plural keys."""
    plural = "contacts" if entity in ("contact", "contacts") else "leads"
    rows = (((conn.pipeline_config or {}).get("snapshot") or {})
            .get("custom_fields") or {}).get(plural) or []
    return {str(e.get("id")) for e in rows if e.get("id") is not None}


@fields_app.command("show")
def fields_show(
    workspace: int = typer.Option(1, "--workspace", "-w"),
    agent: int = typer.Option(..., "--agent", "-a"),
    json_mode: bool = typer.Option(False, "--json"),
):
    """List discovered custom fields + which keys the agent has blessed."""
    asyncio.run(_fields_show_impl(workspace=workspace, agent=agent, json_mode=json_mode))


async def _fields_show_impl(*, workspace: int, agent: int, json_mode: bool) -> None:
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session

    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        conn = await _active_connection(db, workspace)
        if conn is None:
            _no_connection(workspace, json_mode=json_mode)
        discovered = ((conn.pipeline_config or {}).get("snapshot") or {}).get("custom_fields") or {}
        blessed = ((a.channel_config or {}).get("crm") or {}).get("fields") or {} if a else {}
    if json_mode:
        typer.echo(json.dumps({"discovered": discovered, "blessed": blessed},
                              ensure_ascii=False, default=str))
        return
    header(f"CRM Fields — workspace {workspace}, agent {agent}")
    for ent in (discovered.get("leads") or []):
        typer.echo(f"   - {ent.get('id')} \"{ent.get('name')}\" ({ent.get('type')})")
    typer.echo(f"  blessed: {', '.join(blessed) or '(none)'}")


@fields_app.command("set")
def fields_set(
    workspace: int = typer.Option(1, "--workspace", "-w"),
    agent: int = typer.Option(..., "--agent", "-a"),
    key: str = typer.Option(..., "--key"),
    field_id: str = typer.Option(..., "--field-id"),
    label: str = typer.Option("", "--label"),
    ftype: str = typer.Option("text", "--type"),
    entity: str = typer.Option("lead", "--entity", help="lead|contact (which amoCRM entity holds the field)"),
    enum_map: str = typer.Option("", "--enum-map", help="Label=id,Label=id (select only)"),
    inject: bool = typer.Option(False, "--inject"),
    write: bool = typer.Option(False, "--write"),
    json_mode: bool = typer.Option(False, "--json"),
):
    """Bless one custom field for the agent (validates field_id against the snapshot)."""
    asyncio.run(_fields_set_impl(workspace=workspace, agent=agent, key=key, field_id=field_id,
                                 label=label, ftype=ftype, entity=entity, enum_map=enum_map,
                                 inject=inject, write=write, json_mode=json_mode))


async def _fields_set_impl(*, workspace, agent, key, field_id, label, ftype, entity, enum_map,
                           inject, write, json_mode):
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session

    emap: dict[str, int] = {}
    for pair in (enum_map or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            with contextlib.suppress(ValueError):
                emap[k.strip()] = int(v.strip())
    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        if a is None:
            typer.echo(json.dumps({"error": "no_agent"}) if json_mode
                       else f"  No agent {agent} in workspace {workspace}.")
            raise typer.Exit(1)
        conn = await _active_connection(db, workspace)
        if conn is None:
            _no_connection(workspace, json_mode=json_mode)
        ent = "contact" if entity in ("contact", "contacts") else "lead"
        if str(field_id) not in _snapshot_field_ids(conn, ent):
            typer.echo(json.dumps({"error": "unknown_field_id", "field_id": field_id, "entity": ent})
                       if json_mode
                       else f"  Rejected — field_id {field_id} not in the {ent} snapshot "
                            "(run rediscover first)")
            raise typer.Exit(1)
        cfg = dict(a.channel_config or {})
        crm = dict(cfg.get("crm") or {})
        fields = dict(crm.get("fields") or {})
        entry = {"field_id": str(field_id), "label": label or key, "type": ftype,
                 "entity": ent, "inject": inject, "write": write}
        if emap:
            entry["enum_map"] = emap
        fields[key] = entry
        crm["fields"] = fields
        cfg["crm"] = crm
        a.channel_config = cfg
        await db.commit()
    if json_mode:
        typer.echo(json.dumps({"ok": True, "key": key, "field_id": str(field_id)}))
        return
    typer.echo(f"  Blessed field '{key}' -> {field_id}")


tags_app = typer.Typer(no_args_is_help=True)
app.add_typer(tags_app, name="tags", help="Set the per-agent CRM tag vocabulary (no reconnect)")


@tags_app.command("set")
def tags_set(
    workspace: int = typer.Option(1, "--workspace", "-w"),
    agent: int = typer.Option(..., "--agent", "-a"),
    vocabulary: str = typer.Option(..., "--vocabulary", help="comma-separated tag keys"),
    namespace: str = typer.Option("", "--namespace", help="tag name prefix, e.g. oqim:"),
    json_mode: bool = typer.Option(False, "--json"),
):
    """Set the agent's owner-blessed tag vocabulary + namespace."""
    asyncio.run(_tags_set_impl(workspace=workspace, agent=agent, vocabulary=vocabulary,
                               namespace=namespace, json_mode=json_mode))


async def _tags_set_impl(*, workspace, agent, vocabulary, namespace, json_mode):
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session

    vocab = [v.strip() for v in (vocabulary or "").split(",") if v.strip()]
    if not vocab:
        typer.echo(json.dumps({"error": "empty_vocabulary"}) if json_mode
                   else "  Rejected — --vocabulary parsed no tag keys")
        raise typer.Exit(1)
    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        if a is None:
            typer.echo(json.dumps({"error": "no_agent"}) if json_mode
                       else f"  No agent {agent} in workspace {workspace}.")
            raise typer.Exit(1)
        cfg = dict(a.channel_config or {})
        crm = dict(cfg.get("crm") or {})
        crm["tags"] = {"vocabulary": vocab, "namespace": namespace}
        cfg["crm"] = crm
        a.channel_config = cfg
        await db.commit()
    if json_mode:
        typer.echo(json.dumps({"ok": True, "vocabulary": vocab, "namespace": namespace}))
        return
    typer.echo(f"  Tag vocabulary set: {', '.join(vocab)} (namespace '{namespace}')")


dnc_app = typer.Typer(no_args_is_help=True)
app.add_typer(dnc_app, name="dnc", help="Map the per-agent do-not-contact CRM field (no reconnect)")


def _parse_on_value(raw: str):
    """Coerce the ``--on-value`` string to the provider-native DNC 'on' value:
    'true'/'false' -> bool, an int-looking string -> int, else the raw string."""
    text = (raw or "").strip()
    low = text.lower()
    if low in ("true", "1", "yes", "on"):
        return True
    if low in ("false", "0", "no", "off"):
        return False
    try:
        return int(text)
    except ValueError:
        return text


@dnc_app.command("set")
def dnc_set(
    workspace: int = typer.Option(1, "--workspace", "-w"),
    agent: int = typer.Option(..., "--agent", "-a"),
    field_id: str = typer.Option(..., "--field-id"),
    on_value: str = typer.Option("true", "--on-value", help="the value that means do-not-contact"),
    entity: str = typer.Option("contact", "--entity", help="lead|contact (which amoCRM entity holds the DNC field)"),
    label: str = typer.Option("", "--label"),
    json_mode: bool = typer.Option(False, "--json"),
):
    """Map the agent's do-not-contact field (validates field_id against the snapshot)."""
    asyncio.run(_dnc_set_impl(workspace=workspace, agent=agent, field_id=field_id,
                              on_value=on_value, entity=entity, label=label, json_mode=json_mode))


async def _dnc_set_impl(*, workspace, agent, field_id, on_value, entity, label, json_mode):
    ensure_backend_path()
    json_mode = json_mode or agentio.OUTPUT_JSON
    from app.db.session import async_session

    coerced_on = _parse_on_value(on_value)
    async with async_session() as db:
        a = await _agent_in_workspace(db, workspace, agent)
        if a is None:
            typer.echo(json.dumps({"error": "no_agent"}) if json_mode
                       else f"  No agent {agent} in workspace {workspace}.")
            raise typer.Exit(1)
        conn = await _active_connection(db, workspace)
        if conn is None:
            _no_connection(workspace, json_mode=json_mode)
        ent = "contact" if entity in ("contact", "contacts") else "lead"
        if str(field_id) not in _snapshot_field_ids(conn, ent):
            typer.echo(json.dumps({"error": "unknown_field_id", "field_id": field_id, "entity": ent})
                       if json_mode
                       else f"  Rejected — field_id {field_id} not in the {ent} snapshot "
                            "(run rediscover first)")
            raise typer.Exit(1)
        cfg = dict(a.channel_config or {})
        crm = dict(cfg.get("crm") or {})
        crm["do_not_contact"] = {"field_id": str(field_id), "on_value": coerced_on,
                                 "entity": ent, "label": label}
        cfg["crm"] = crm
        a.channel_config = cfg
        await db.commit()
    if json_mode:
        typer.echo(json.dumps({"ok": True, "field_id": str(field_id), "on_value": coerced_on}))
        return
    typer.echo(f"  Do-not-contact mapped -> field {field_id} on_value={coerced_on}")
