"""The records pass must run at a converged tail on BOTH reply-delivering
dispatcher paths — the normal talk-bundle path AND the generic-actions/handoff
path (which returns early, before the normal post-commit tail). A full
``dispatch_agent_turn`` integration test needs a DB/ORM stack that is impractical
as a pure unit test (mirrors the source-level pattern already used in
``test_media_perception_dispatch.py``), so we verify the wiring exists in source:
both delivering paths route through the single ``_records_pass_post_commit``
helper. End-to-end proof is the live verification in the plan."""

import inspect


def test_records_pass_helper_exists():
    import app.modules.agent_runtime_v2.dispatcher as d

    assert hasattr(d, "_records_pass_post_commit"), (
        "dispatcher must expose a module-level _records_pass_post_commit helper"
    )
    assert inspect.iscoroutinefunction(d._records_pass_post_commit)


def test_both_delivering_paths_call_records_pass():
    import app.modules.agent_runtime_v2.dispatcher as d

    src = inspect.getsource(d)
    # The normal talk-bundle tail AND the generic-actions/handoff early-return
    # path must each invoke the converged helper — at least two call sites.
    assert src.count("_records_pass_post_commit(") >= 2, (
        "both reply-delivering paths (normal ~585 and generic-actions ~371) must "
        "call _records_pass_post_commit so the records pass runs on either return path"
    )
    # The helper must funnel into run_records_pass (the renamed driver), not the
    # retired run_commercial_finalization.
    assert "run_records_pass" in src
    assert "run_commercial_finalization" not in src


def test_records_pass_helper_enqueues_and_does_not_await_inline():
    """The records pass must run OFF the reply lease: the helper enqueues a
    RecordsJob and returns; it must NOT await run_records_pass inline (the 2026-06-15
    outage was the inline await on the serial turn lease)."""
    import app.modules.agent_runtime_v2.dispatcher as d

    src = inspect.getsource(d._records_pass_post_commit)
    assert "enqueue_records_job(" in src, (
        "_records_pass_post_commit must enqueue the records job off the reply path"
    )
    assert "RecordsJob(" in src, "the helper must build a RecordsJob"
    assert "await run_records_pass(" not in src, (
        "the records pass must NOT be awaited inline on the dispatch path"
    )
    assert "run_commercial_finalization" not in src


def test_records_pass_helper_forwards_customer_id_and_agent_id():
    """The records-pass fan-out (handoff work-item, promoter opt-out) needs the
    customer/agent identity — the converged helper must accept and forward both
    customer_id and agent_id so run_records_pass can synthesize the work-item."""
    import app.modules.agent_runtime_v2.dispatcher as d

    src = inspect.getsource(d._records_pass_post_commit)
    assert "customer_id=" in src, (
        "_records_pass_post_commit must forward customer_id to run_records_pass"
    )
    assert "agent_id=" in src, (
        "_records_pass_post_commit must forward agent_id to run_records_pass"
    )
