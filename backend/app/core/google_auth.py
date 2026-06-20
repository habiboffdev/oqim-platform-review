from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import google.auth
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request
from google.genai import types
from google.oauth2 import service_account

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("core.google_auth")

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


@contextmanager
def _google_credentials_env_for_auth_source(auth_source: str):
    if auth_source != "adc":
        yield
        return

    existing = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    try:
        yield
    finally:
        if existing is not None:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = existing


def _wants_vertex_ai(settings: Any) -> bool:
    configured = getattr(settings, "google_genai_use_vertexai", None)
    if isinstance(configured, bool):
        return bool(configured)
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(slots=True)
class GoogleAuthStatus:
    genai_mode: str
    vertex_mode: str
    auth_source: str
    credentials_path: str | None
    impersonated_principal: str | None
    project: str | None
    location: str | None
    detail: str | None = None
    api_key_configured: bool = False
    vertex_forced: bool = False
    validated: bool | None = None
    validation_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GoogleAuthResolution:
    credentials: Credentials | None
    status: GoogleAuthStatus


def _load_service_account_credentials(
    credentials_path: str,
) -> tuple[Credentials | None, str | None]:
    path = Path(credentials_path).expanduser()
    if not path.exists():
        return None, "service_account_file_missing"

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return None, f"service_account_json_invalid:{type(exc).__name__}"

    if payload.get("type") != "service_account":
        return None, "google_application_credentials_not_service_account"

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=[_CLOUD_PLATFORM_SCOPE],
        )
    except Exception as exc:
        return None, f"service_account_load_failed:{type(exc).__name__}"

    return creds, None


def resolve_google_auth(
    settings: Any | None = None,
) -> GoogleAuthResolution:
    settings = settings or get_settings()

    service_account_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    project = settings.google_cloud_project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = settings.google_cloud_location
    auth_source = getattr(settings, "google_auth_source", "auto")
    if not isinstance(auth_source, str):
        auth_source = "auto"
    auth_source = auth_source.strip().lower()
    credentials: Credentials | None = None
    vertex_mode = "unavailable"
    detail: str | None = None
    normalized_path: str | None = None
    vertex_forced = _wants_vertex_ai(settings)
    impersonate_principal = getattr(settings, "google_impersonate_service_account", None)

    if service_account_path and auth_source != "adc":
        normalized_path = str(Path(service_account_path).expanduser())
        credentials, detail = _load_service_account_credentials(normalized_path)
        if credentials is not None:
            vertex_mode = "service_account"

    if credentials is None and auth_source != "service_account":
        try:
            with _google_credentials_env_for_auth_source(auth_source):
                default_credentials, detected_project = google.auth.default(
                    scopes=[_CLOUD_PLATFORM_SCOPE]
                )
            quota_project = project or detected_project
            credentials = default_credentials
            vertex_mode = "adc"
            if isinstance(impersonate_principal, str) and impersonate_principal.strip():
                credentials = impersonated_credentials.Credentials(
                    source_credentials=default_credentials,
                    target_principal=impersonate_principal.strip(),
                    target_scopes=[_CLOUD_PLATFORM_SCOPE],
                    lifetime=3600,
                )
                vertex_mode = "impersonated_service_account"
            elif quota_project and hasattr(default_credentials, "with_quota_project"):
                credentials = default_credentials.with_quota_project(quota_project)
            if not project:
                project = detected_project
        except DefaultCredentialsError as exc:
            if detail is None:
                detail = f"default_credentials_unavailable:{type(exc).__name__}"
        except Exception as exc:
            if detail is None:
                detail = f"default_credentials_failed:{type(exc).__name__}"
    elif credentials is None and auth_source == "service_account" and detail is None:
        detail = "service_account_credentials_unavailable"

    if settings.gemini_api_key and not vertex_forced:
        genai_mode = "api_key"
    elif vertex_mode == "service_account":
        genai_mode = "vertex_service_account"
    elif vertex_mode == "adc":
        genai_mode = "vertex_adc"
    elif vertex_mode == "impersonated_service_account":
        genai_mode = "vertex_impersonated_service_account"
    else:
        genai_mode = "vertex_unavailable"

    status = GoogleAuthStatus(
        genai_mode=genai_mode,
        vertex_mode=vertex_mode,
        auth_source=auth_source,
        credentials_path=normalized_path,
        impersonated_principal=(
            impersonate_principal.strip()
            if isinstance(impersonate_principal, str) and impersonate_principal.strip()
            else None
        ),
        project=project,
        location=location,
        detail=detail,
        api_key_configured=bool(settings.gemini_api_key),
        vertex_forced=vertex_forced,
    )
    return GoogleAuthResolution(credentials=credentials, status=status)


def build_genai_client_kwargs(settings: Any | None = None) -> tuple[dict[str, Any], GoogleAuthStatus]:
    settings = settings or get_settings()
    resolution = resolve_google_auth(settings)

    if settings.gemini_api_key and not _wants_vertex_ai(settings):
        return {"api_key": settings.gemini_api_key}, resolution.status

    kwargs: dict[str, Any] = {
        "vertexai": True,
        "project": resolution.status.project,
        "location": resolution.status.location,
        "http_options": types.HttpOptions(api_version="v1"),
    }
    if resolution.credentials is not None:
        kwargs["credentials"] = resolution.credentials
    return kwargs, resolution.status


def validate_google_auth(status: GoogleAuthStatus, credentials: Credentials | None) -> GoogleAuthStatus:
    validated = GoogleAuthStatus(**status.as_dict())

    if credentials is None:
        validated.validated = False
        validated.validation_error = "no_vertex_credentials"
        return validated

    try:
        credentials.refresh(Request())
        validated.validated = True
        validated.validation_error = None
        return validated
    except RefreshError as exc:
        validated.validated = False
        validated.validation_error = f"refresh_error:{type(exc).__name__}"
        validated.detail = validated.detail or str(exc)
        return validated
    except Exception as exc:
        validated.validated = False
        validated.validation_error = f"validation_failed:{type(exc).__name__}"
        validated.detail = validated.detail or str(exc)
        return validated


def log_google_auth_status(logger_obj, *, component: str, status: GoogleAuthStatus) -> None:
    logger_obj.info(
        "google_auth",
        extra={
            "component": component,
            "genai_mode": status.genai_mode,
            "vertex_mode": status.vertex_mode,
            "auth_source": status.auth_source,
            "project": status.project,
            "location": status.location,
            "credentials_path": status.credentials_path,
            "impersonated_principal": status.impersonated_principal,
            "detail": status.detail,
        },
    )
