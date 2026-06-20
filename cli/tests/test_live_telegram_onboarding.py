from cli.commands.test_cmd import (
    _build_onboarding_telegram_source_item,
    _live_telegram_onboarding_next_actions,
    _select_live_telegram_channel,
    _select_live_telegram_session,
)


def test_select_live_telegram_session_prefers_connected_workspace() -> None:
    sessions = [
        {"workspaceId": 3, "state": "disconnected"},
        {"workspaceId": 4, "state": "connected"},
    ]

    assert _select_live_telegram_session(sessions, workspace_id=None)["workspaceId"] == 4


def test_select_live_telegram_session_honors_explicit_workspace() -> None:
    sessions = [
        {"workspaceId": 3, "state": "connected"},
        {"workspaceId": 4, "state": "connected"},
    ]

    assert _select_live_telegram_session(sessions, workspace_id=3)["workspaceId"] == 3
    assert _select_live_telegram_session(sessions, workspace_id=99) is None


def test_select_live_telegram_channel_prefers_owned_and_matches_handle() -> None:
    channels = [
        {"id": "11", "name": "Other", "username": "other_shop", "is_own": False},
        {"id": "12", "name": "Main", "username": "main_shop", "is_own": True},
    ]

    assert _select_live_telegram_channel(channels, channel=None)["id"] == "12"
    assert _select_live_telegram_channel(channels, channel="@other_shop")["id"] == "11"


def test_build_onboarding_telegram_source_item_keeps_text_posts_only() -> None:
    source = _build_onboarding_telegram_source_item(
        selected_channel={"id": "12", "name": "Main", "username": "main_shop"},
        posts=[
            {"postId": 7, "date": "2026-05-09T10:00:00Z", "text": "Yangi narx 120 ming"},
            {"postId": 8, "date": "2026-05-09T10:01:00Z", "action": "joined"},
            {"id": 9, "message": "Yetkazib berish bor"},
        ],
    )

    assert source["kind"] == "telegram_channel"
    assert source["label"] == "Main"
    assert source["handle"] == "main_shop"
    assert [message["post_id"] for message in source["messages"]] == [7, 9]
    assert source["messages"][0]["text"] == "Yangi narx 120 ming"


def test_live_telegram_onboarding_next_actions_are_operator_ready() -> None:
    actions = _live_telegram_onboarding_next_actions(workspace_id=42)

    assert actions[0] == "Start the GramJS sidecar: oqim dev start --local"
    assert "Connect a real Telegram session" in actions[1]
    assert actions[2] == "Re-run: oqim test live-telegram-onboarding --workspace-id 42 --json"


def test_live_telegram_onboarding_next_actions_skip_sidecar_when_reachable() -> None:
    actions = _live_telegram_onboarding_next_actions(
        workspace_id=42,
        sidecar_reachable=True,
    )

    assert actions == [
        "Connect a real Telegram session in Onboarding or Settings.",
        "Re-run: oqim test live-telegram-onboarding --workspace-id 42 --json",
    ]
