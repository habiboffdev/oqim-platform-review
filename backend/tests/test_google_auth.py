from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.auth.exceptions import DefaultCredentialsError


def test_resolve_google_auth_prefers_service_account_file(tmp_path: Path, monkeypatch):
    from app.core.google_auth import resolve_google_auth

    creds_path = tmp_path / "service-account.json"
    creds_path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "svc-project",
                "private_key_id": "abc",
                "private_key": "-----BEGIN PRIVATE KEY-----\nABC\n-----END PRIVATE KEY-----\n",
                "client_email": "svc@example.com",
                "client_id": "123",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds_path))

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.google_auth_source = "auto"
    mock_settings.google_cloud_project = "oqim-business"
    mock_settings.google_cloud_location = "global"

    fake_credentials = MagicMock()
    with patch(
        "app.core.google_auth.service_account.Credentials.from_service_account_file",
        return_value=fake_credentials,
    ) as mock_from_file:
        resolution = resolve_google_auth(mock_settings)

    mock_from_file.assert_called_once()
    assert resolution.credentials is fake_credentials
    assert resolution.status.vertex_mode == "service_account"
    assert resolution.status.genai_mode == "vertex_service_account"
    assert resolution.status.credentials_path == str(creds_path)


def test_resolve_google_auth_falls_back_to_adc(monkeypatch):
    from app.core.google_auth import resolve_google_auth

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.google_auth_source = "auto"
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    fake_credentials = MagicMock()
    fake_credentials.with_quota_project.return_value = fake_credentials
    with patch(
        "app.core.google_auth.google.auth.default",
        return_value=(fake_credentials, "adc-project"),
    ):
        resolution = resolve_google_auth(mock_settings)

    assert resolution.credentials is fake_credentials
    assert resolution.status.vertex_mode == "adc"
    assert resolution.status.genai_mode == "vertex_adc"
    assert resolution.status.project == "adc-project"


def test_resolve_google_auth_reports_api_key_mode_when_configured(monkeypatch):
    from app.core.google_auth import resolve_google_auth

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.google_auth_source = "auto"
    mock_settings.google_cloud_project = "oqim-business"
    mock_settings.google_cloud_location = "global"

    with patch(
        "app.core.google_auth.google.auth.default",
        side_effect=DefaultCredentialsError("no adc"),
    ):
        resolution = resolve_google_auth(mock_settings)

    assert resolution.status.genai_mode == "api_key"
    assert resolution.status.vertex_mode == "unavailable"
    assert resolution.status.api_key_configured is True


def test_build_genai_client_kwargs_prefers_vertex_when_explicitly_enabled(monkeypatch):
    from app.core.google_auth import build_genai_client_kwargs

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "stale-key-that-should-not-win"
    mock_settings.google_genai_use_vertexai = True
    mock_settings.google_auth_source = "auto"
    mock_settings.google_cloud_project = "oqim-business"
    mock_settings.google_cloud_location = "global"

    fake_credentials = MagicMock()
    fake_credentials.with_quota_project.return_value = fake_credentials
    with patch(
        "app.core.google_auth.google.auth.default",
        return_value=(fake_credentials, "adc-project"),
    ):
        kwargs, status = build_genai_client_kwargs(mock_settings)

    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "oqim-business"
    assert kwargs["location"] == "global"
    assert kwargs["credentials"] is fake_credentials
    assert kwargs["http_options"].api_version == "v1"
    assert status.genai_mode == "vertex_adc"
    assert status.vertex_forced is True
    assert status.api_key_configured is True


def test_build_genai_client_kwargs_keeps_api_key_when_vertex_not_enabled(monkeypatch):
    from app.core.google_auth import build_genai_client_kwargs

    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.google_genai_use_vertexai = None
    mock_settings.google_auth_source = "auto"
    mock_settings.google_cloud_project = "oqim-business"
    mock_settings.google_cloud_location = "global"

    with patch(
        "app.core.google_auth.google.auth.default",
        side_effect=DefaultCredentialsError("no adc"),
    ):
        kwargs, status = build_genai_client_kwargs(mock_settings)

    assert kwargs == {"api_key": "test-key"}
    assert status.genai_mode == "api_key"
    assert status.vertex_forced is False


def test_resolve_google_auth_can_force_adc_over_service_account(tmp_path: Path, monkeypatch):
    from app.core.google_auth import resolve_google_auth

    creds_path = tmp_path / "broken-service-account.json"
    creds_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds_path))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.google_auth_source = "adc"
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    fake_credentials = MagicMock()
    fake_credentials.with_quota_project.return_value = fake_credentials
    with patch(
        "app.core.google_auth.google.auth.default",
        return_value=(fake_credentials, "gcloud-project"),
    ):
        resolution = resolve_google_auth(mock_settings)

    assert resolution.credentials is fake_credentials
    assert resolution.status.vertex_mode == "adc"
    assert resolution.status.auth_source == "adc"
    assert resolution.status.project == "gcloud-project"
    assert resolution.status.credentials_path is None


def test_resolve_google_auth_can_impersonate_service_account(monkeypatch):
    from app.core.google_auth import resolve_google_auth

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.google_auth_source = "adc"
    mock_settings.google_cloud_project = "oqim-494421"
    mock_settings.google_cloud_location = "global"
    mock_settings.google_impersonate_service_account = (
        "vertex-express@oqim-494421.iam.gserviceaccount.com"
    )

    source_credentials = MagicMock()
    impersonated = MagicMock()
    with patch(
        "app.core.google_auth.google.auth.default",
        return_value=(source_credentials, "adc-project"),
    ):
        with patch(
            "app.core.google_auth.impersonated_credentials.Credentials",
            return_value=impersonated,
        ) as mock_impersonated:
            resolution = resolve_google_auth(mock_settings)

    mock_impersonated.assert_called_once_with(
        source_credentials=source_credentials,
        target_principal="vertex-express@oqim-494421.iam.gserviceaccount.com",
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=3600,
    )
    source_credentials.with_quota_project.assert_not_called()
    assert resolution.credentials is impersonated
    assert resolution.status.vertex_mode == "impersonated_service_account"
    assert resolution.status.genai_mode == "vertex_impersonated_service_account"
    assert (
        resolution.status.impersonated_principal
        == "vertex-express@oqim-494421.iam.gserviceaccount.com"
    )


def test_validate_google_auth_marks_missing_credentials_invalid():
    from app.core.google_auth import GoogleAuthStatus, validate_google_auth

    status = GoogleAuthStatus(
        genai_mode="vertex_unavailable",
        vertex_mode="unavailable",
        auth_source="auto",
        credentials_path=None,
        impersonated_principal=None,
        project="oqim-business",
        location="global",
    )

    validated = validate_google_auth(status, None)

    assert validated.validated is False
    assert validated.validation_error == "no_vertex_credentials"


def test_reranker_uses_resolved_service_account_credentials():
    from app.brain import reranker
    from app.core.google_auth import GoogleAuthResolution, GoogleAuthStatus

    fake_credentials = MagicMock()
    status = GoogleAuthStatus(
        genai_mode="api_key",
        vertex_mode="service_account",
        auth_source="auto",
        credentials_path="/tmp/service-account.json",
        impersonated_principal=None,
        project="oqim-business",
        location="global",
    )

    reranker._client = None
    try:
        with patch(
            "app.brain.reranker.resolve_google_auth",
            return_value=GoogleAuthResolution(credentials=fake_credentials, status=status),
        ):
            with patch("app.brain.reranker.log_google_auth_status"):
                with patch("app.brain.reranker.de.RankServiceClient") as mock_rank_client:
                    reranker._get_rank_client()
                    mock_rank_client.assert_called_once_with(credentials=fake_credentials)
    finally:
        reranker._client = None
