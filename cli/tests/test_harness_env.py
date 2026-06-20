from cli.harness_env import (
    build_backend_pytest_env,
    make_harness_db_suffix,
    sanitize_test_db_suffix,
)


def test_sanitize_test_db_suffix_keeps_postgres_identifier_safe() -> None:
    assert sanitize_test_db_suffix("delivery-chaos/worker 1") == "delivery_chaos_worker_1"
    assert sanitize_test_db_suffix("!!!") == "isolated"


def test_build_backend_pytest_env_sets_isolated_database_suffix() -> None:
    env = build_backend_pytest_env(
        db_suffix="media-chaos",
        drop_db_at_end=True,
        base_env={"DATABASE_URL": "keep-out", "OTHER": "value"},
    )

    assert env["OQIM_TEST_DB_SUFFIX"] == "media_chaos"
    assert env["OQIM_TEST_DB_DROP_AT_END"] == "1"
    assert env["OTHER"] == "value"
    assert env["DATABASE_URL"] == "keep-out"


def test_make_harness_db_suffix_is_unique_and_traceable() -> None:
    first = make_harness_db_suffix("delivery-chaos", sequence=1, pid=123)
    second = make_harness_db_suffix("delivery-chaos", sequence=2, pid=123)

    assert first == "delivery_chaos_123_1"
    assert second == "delivery_chaos_123_2"
    assert first != second
