from cli.runtime_audit import runtime_audit_report


def test_runtime_audit_report_marks_all_proof_gates_implemented() -> None:
    report = runtime_audit_report()

    assert report["passed"] is True
    assert report["summary"]["implemented"] >= 8
    assert report["summary"]["partial"] == 0
    assert report["blockers"] == []


def test_runtime_audit_uses_seller_agent_reply_language() -> None:
    report = runtime_audit_report()
    combined = " ".join(
        str(gate.get(field, ""))
        for gate in report["gates"]
        for field in ("plane", "command", "purpose", "remaining_gap")
    ).lower()

    assert "draft" not in combined
    assert "seller agent reply" in combined


def test_runtime_audit_report_tracks_tenant_and_adapter_gates() -> None:
    report = runtime_audit_report()
    gates_by_plane = {gate["plane"]: gate for gate in report["gates"]}

    assert gates_by_plane["local-reality"]["command"] == "oqim test local-reality"
    assert gates_by_plane["local-reality"]["status"] == "implemented"
    assert gates_by_plane["live-chat-truth"]["command"] == "oqim test live-chat-truth --workspace-id <id>"
    assert gates_by_plane["live-chat-truth"]["status"] == "implemented"
    assert gates_by_plane["telegram-intake"]["command"] == "oqim test telegram-intake --browser"
    assert gates_by_plane["telegram-intake"]["status"] == "implemented"
    assert gates_by_plane["harness-truth"]["command"] == "oqim test harness-parallel"
    assert gates_by_plane["harness-truth"]["status"] == "implemented"
    assert gates_by_plane["multi-tenant"]["command"] == "oqim test tenants --workspaces 1000"
    assert gates_by_plane["multi-tenant"]["status"] == "implemented"
    assert gates_by_plane["sales-crm"]["command"] == "oqim eval sales"
    assert gates_by_plane["sales-crm"]["status"] == "implemented"
    retired_plane = "auto" + "crm-extraction"
    assert retired_plane not in gates_by_plane
    assert (
        gates_by_plane["buyer-intent-extraction"]["command"]
        == "oqim eval buyer-intent --live --concurrency 2 --max-p95-ms 45000"
    )
    assert gates_by_plane["buyer-intent-extraction"]["status"] == "implemented"
    assert (
        gates_by_plane["retrieval-core-quality"]["command"]
        == "oqim eval retrieval-core --max-p95-ms 5000"
    )
    assert gates_by_plane["retrieval-core-quality"]["status"] == "implemented"
    assert (
        gates_by_plane["retrieval-rerank-provider"]["command"]
        == "oqim eval retrieval-core --live-rerank-provider --max-p95-ms 5000"
    )
    assert gates_by_plane["retrieval-rerank-provider"]["status"] == "implemented"
    assert (
        gates_by_plane["company-brain-source-quality"]["command"]
        == "oqim eval company-brain --max-p95-ms 10000"
    )
    assert gates_by_plane["company-brain-source-quality"]["status"] == "implemented"
    assert (
        gates_by_plane["company-brain-live-source-quality"]["command"]
        == "oqim eval company-brain --live --semantic --contextual-source-units --max-p95-ms 60000"
    )
    assert gates_by_plane["company-brain-live-source-quality"]["status"] == "implemented"
    assert "PDF Files API" in gates_by_plane["onboarding"]["purpose"]
    assert (
        gates_by_plane["reply-quality"]["command"]
        == "oqim eval replies --seed-workspace --concurrency 2 --max-p95-ms 45000"
    )
    assert gates_by_plane["reply-quality"]["status"] == "implemented"
    assert gates_by_plane["adapter-parity"]["command"] == "oqim test adapter-contract"
    assert gates_by_plane["adapter-parity"]["status"] == "implemented"
