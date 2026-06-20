"""Multi-pipeline default mapping + the flat<->nested read shim (S1 #437)."""
from __future__ import annotations

from app.modules.crm_connector.contracts import (
    CrmAccountSchema,
    CrmPipeline,
    CrmPipelineStatus,
)
from app.modules.crm_connector.stage_map import (
    default_mapping,
    default_pipeline_id,
    resolve_pipeline_view,
)


def _st(sid, sort, kind, name="s"):
    return CrmPipelineStatus(stage_id=sid, name=name, sort=sort, kind=kind)


def _pipeline(pid, is_main, statuses, name=None):
    return CrmPipeline(pipeline_id=pid, name=name or ("P" + pid), is_main=is_main, statuses=statuses)


def _schema(*pipelines):
    return CrmAccountSchema(pipelines=list(pipelines))


def test_default_mapping_maps_all_pipelines_with_main_default():
    main = _pipeline("111", True, [
        _st("201", 10, "active", "Yangi"), _st("202", 20, "active"),
        _st("203", 30, "active"), _st("142", 10000, "won"), _st("143", 11000, "lost"),
    ])
    other = _pipeline("222", False, [_st("301", 5, "active"), _st("142", 9, "won")])
    cfg = default_mapping(_schema(other, main))
    assert cfg["schema_version"] == 2
    m = cfg["mapping"]
    assert m["default_pipeline_id"] == "111"            # the is_main pipeline
    assert set(m["pipelines"]) == {"111", "222"}        # ALL pipelines, not just main
    rm = m["pipelines"]["111"]["role_map"]
    assert rm["new"]["stage_id"] == "201"
    assert rm["qualified"]["stage_id"] == "203"
    assert rm["won"]["stage_id"] == "142"
    assert rm["lost"]["stage_id"] == "143"
    snap = {p["id"]: p for p in cfg["snapshot"]["pipelines"]}
    assert len(snap["111"]["statuses"]) == 5            # full ladder kept in the snapshot
    assert snap["111"]["statuses"][0]["stage_id"] == "201"
    assert snap["111"]["statuses"][0]["kind"] == "active"


def test_default_mapping_three_active_is_back_compatible_with_old_clamp():
    # The pilot has 3 active stages — its role_map must be unchanged from the old clamp.
    p = _pipeline("111", True, [
        _st("201", 10, "active"), _st("202", 20, "active"), _st("203", 30, "active"),
        _st("142", 10000, "won"), _st("143", 11000, "lost"),
    ])
    rm = default_mapping(_schema(p))["mapping"]["pipelines"]["111"]["role_map"]
    assert rm["new"]["stage_id"] == "201"
    assert rm["negotiation"]["stage_id"] == "202"
    assert rm["qualified"]["stage_id"] == "203"


def test_default_mapping_relaxed_clamp_keeps_middle_and_last_for_six_stages():
    # 6 active: new=first, qualified=LAST active (not the 3rd), negotiation=a real middle.
    actives = [_st(str(200 + i), (i + 1) * 10, "active", f"S{i}") for i in range(6)]
    p = _pipeline("111", True, [*actives, _st("142", 99999, "won")])
    rm = default_mapping(_schema(p))["mapping"]["pipelines"]["111"]["role_map"]
    assert rm["new"]["stage_id"] == "200"              # first active
    assert rm["qualified"]["stage_id"] == "205"        # last active (last-before-terminal)
    assert rm["negotiation"]["stage_id"] == "203"      # active[len//2], a middle stage
    assert rm["negotiation"]["stage_id"] not in ("200", "205")


def test_default_mapping_two_active_clamps_negotiation_onto_qualified():
    p = _pipeline("111", True, [
        _st("201", 10, "active"), _st("202", 20, "active"), _st("142", 999, "won"),
    ])
    rm = default_mapping(_schema(p))["mapping"]["pipelines"]["111"]["role_map"]
    assert rm["new"]["stage_id"] == "201"
    assert rm["negotiation"]["stage_id"] == "202"
    assert rm["qualified"]["stage_id"] == "202"         # clamped onto the last active


def test_default_mapping_falls_back_to_first_when_no_main():
    p = _pipeline("777", False, [_st("301", 1, "active"), _st("142", 100, "won")])
    assert default_mapping(_schema(p))["mapping"]["default_pipeline_id"] == "777"


def test_default_mapping_empty_schema_is_skeleton():
    cfg = default_mapping(_schema())
    assert cfg["schema_version"] == 2
    assert cfg["snapshot"]["pipelines"] == []
    assert cfg["mapping"] == {"default_pipeline_id": None, "pipelines": {}}


def test_resolve_pipeline_view_legacy_flat():
    flat = {
        "pipeline_id": "777",
        "stage_map": {"new": {"stage_id": "1001", "sort": 10}},
        "pipeline_snapshot": [{"stage_id": "1001", "name": "New", "sort": 10, "kind": "active"}],
    }
    view = resolve_pipeline_view(flat)
    assert view["pipeline_id"] == "777"
    assert view["stage_map"]["new"]["stage_id"] == "1001"
    assert view["snapshot_statuses"][0]["name"] == "New"


def test_resolve_pipeline_view_nested_default_and_explicit():
    nested = {
        "schema_version": 2,
        "snapshot": {"pipelines": [
            {"id": "111", "name": "A", "statuses": [
                {"stage_id": "201", "name": "Yangi", "sort": 10, "kind": "active"}]},
            {"id": "222", "name": "B", "statuses": [
                {"stage_id": "301", "name": "Boshlash", "sort": 10, "kind": "active"}]},
        ]},
        "mapping": {
            "default_pipeline_id": "111",
            "pipelines": {
                "111": {"name": "A", "role_map": {"new": {"stage_id": "201", "sort": 10}}},
                "222": {"name": "B", "role_map": {"new": {"stage_id": "301", "sort": 10}}},
            },
        },
    }
    d = resolve_pipeline_view(nested)                    # default pipeline
    assert d["pipeline_id"] == "111"
    assert d["stage_map"]["new"]["stage_id"] == "201"
    assert d["snapshot_statuses"][0]["name"] == "Yangi"
    e = resolve_pipeline_view(nested, "222")             # explicit non-default
    assert e["pipeline_id"] == "222"
    assert e["stage_map"]["new"]["stage_id"] == "301"
    assert e["snapshot_statuses"][0]["name"] == "Boshlash"


def test_default_pipeline_id_flat_and_nested():
    assert default_pipeline_id({"pipeline_id": "777", "stage_map": {}}) == "777"
    assert default_pipeline_id({"mapping": {"default_pipeline_id": "111", "pipelines": {}}}) == "111"
    assert default_pipeline_id({}) is None
    assert default_pipeline_id(None) is None


def test_default_mapping_projects_custom_fields_users_task_types():
    from app.modules.crm_connector.contracts import (
        CrmAccountSchema,
        CrmFieldDef,
        CrmFieldEnum,
        CrmTaskType,
        CrmUser,
    )
    schema = CrmAccountSchema(
        pipelines=[_pipeline("111", True, [_st("201", 10, "active")])],
        custom_fields={"leads": [CrmFieldDef(
            key_id="600124", code=None, name="Manba", type="select",
            enums=(CrmFieldEnum(enum_id="9001", value="Instagram"),))]},
        users=[CrmUser(user_id="55001", name="Aziz")],
        task_types=[CrmTaskType(task_type_id="1", name="Aloqa")],
    )
    snap = default_mapping(schema)["snapshot"]
    assert snap["custom_fields"]["leads"][0] == {
        "id": "600124", "code": None, "name": "Manba", "type": "select",
        "enums": [{"id": "9001", "value": "Instagram"}]}
    assert snap["users"] == [{"id": "55001", "name": "Aziz"}]
    assert snap["task_types"] == [{"id": "1", "name": "Aloqa"}]


def test_default_mapping_snapshot_is_order_canonical():
    """Idempotency guard: the same schema in a DIFFERENT source order (amoCRM does
    not guarantee list ordering) must produce a byte-identical snapshot, so the 6h
    poll stays a no-op on a quiet account."""
    from app.modules.crm_connector.contracts import (
        CrmAccountSchema,
        CrmFieldDef,
        CrmFieldEnum,
        CrmTaskType,
        CrmUser,
    )

    def mk(reverse):
        p1 = _pipeline("111", True, [_st("201", 10, "active", "A"), _st("202", 20, "active", "B")])
        p2 = _pipeline("222", False, [_st("301", 10, "active", "C")])
        fields = [
            CrmFieldDef(key_id="600124", code=None, name="X", type="select",
                        enums=(CrmFieldEnum(enum_id="9", value="i"), CrmFieldEnum(enum_id="1", value="j"))),
            CrmFieldDef(key_id="600100", code=None, name="Y", type="text"),
        ]
        users = [CrmUser(user_id="5", name="E"), CrmUser(user_id="3", name="C")]
        tts = [CrmTaskType(task_type_id="9", name="Z"), CrmTaskType(task_type_id="1", name="A")]
        pipes = [p1, p2]
        if reverse:
            pipes = list(reversed(pipes))
            fields = list(reversed(fields))
            users = list(reversed(users))
            tts = list(reversed(tts))
        return CrmAccountSchema(
            pipelines=pipes, custom_fields={"leads": fields}, users=users, task_types=tts)

    assert default_mapping(mk(False))["snapshot"] == default_mapping(mk(True))["snapshot"]


def test_snapshot_pipeline_ids_and_routing_validation():
    from app.modules.crm_connector.stage_map import (
        snapshot_pipeline_ids,
        validate_routing_pipeline_ids,
    )
    nested = {"snapshot": {"pipelines": [{"id": "111"}, {"id": "222"}]}}
    assert snapshot_pipeline_ids(nested) == {"111", "222"}
    assert snapshot_pipeline_ids({"pipeline_id": "111"}) == {"111"}   # legacy flat
    assert snapshot_pipeline_ids({}) == set()
    assert validate_routing_pipeline_ids({"sales": "111", "x": "999"}, nested) == ["999"]
    assert validate_routing_pipeline_ids({"sales": "111"}, nested) == []


def test_pipeline_id_for_stage_and_default_str_coercion():
    from app.modules.crm_connector.stage_map import (
        default_pipeline_id,
        pipeline_id_for_stage,
    )
    nested = {
        "snapshot": {"pipelines": [
            {"id": "111", "statuses": [{"stage_id": "201"}, {"stage_id": "202"}]},
            {"id": "222", "statuses": [{"stage_id": "301"}]}]},
        "mapping": {"default_pipeline_id": 111, "pipelines": {}},  # int default
    }
    assert pipeline_id_for_stage(nested, "202") == "111"
    assert pipeline_id_for_stage(nested, "301") == "222"
    assert pipeline_id_for_stage(nested, "999") is None
    assert pipeline_id_for_stage(nested, None) is None
    # legacy flat
    flat = {"pipeline_id": "111", "pipeline_snapshot": [{"stage_id": "201"}]}
    assert pipeline_id_for_stage(flat, "201") == "111"
    # #2: default coerced to str even when stored as int
    assert default_pipeline_id(nested) == "111"
