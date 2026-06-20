from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OQIM Business application settings."""

    # Environment & app metadata
    environment: str = Field(default="development", alias="APP_ENV")
    api_prefix: str = "/api"
    project_name: str = "OQIM Business"
    version: str = "0.1.0"

    # Telegram / MTProto
    telegram_api_id: int | None = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: str | None = Field(default=None, alias="TELEGRAM_API_HASH")

    # GramJS sidecar
    sidecar_url: str = Field(default="http://localhost:3100", alias="SIDECAR_URL")
    sidecar_api_key: str = Field(default="", alias="SIDECAR_API_KEY")

    # Instagram Business API (Instagram Login path — no Facebook Page)
    instagram_app_id: str = Field(default="", alias="INSTAGRAM_APP_ID")
    instagram_app_secret: str = Field(default="", alias="INSTAGRAM_APP_SECRET")
    instagram_redirect_uri: str = Field(
        default="https://your-domain.example/api/instagram/auth/callback",
        alias="INSTAGRAM_REDIRECT_URI",
    )
    instagram_webhook_verify_token: str = Field(
        default="", alias="INSTAGRAM_WEBHOOK_VERIFY_TOKEN"
    )
    instagram_graph_base: str = Field(
        default="https://graph.instagram.com", alias="INSTAGRAM_GRAPH_BASE"
    )

    # amoCRM external integration (one OQIM-owned integration; per-workspace tokens)
    amocrm_client_id: str = Field(default="", alias="AMOCRM_CLIENT_ID")
    amocrm_client_secret: str = Field(default="", alias="AMOCRM_CLIENT_SECRET")
    amocrm_redirect_uri: str = Field(
        default="https://your-domain.example/api/amocrm/auth/callback",
        alias="AMOCRM_REDIRECT_URI",
    )

    # Databases & caches
    postgres_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5434/oqim_business",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6381/0", alias="REDIS_URL")

    # AI / LLM
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    google_genai_use_vertexai: bool | None = Field(
        default=None,
        alias="GOOGLE_GENAI_USE_VERTEXAI",
    )
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="global", alias="GOOGLE_CLOUD_LOCATION")
    google_auth_source: str = Field(default="auto", alias="GOOGLE_AUTH_SOURCE")
    discovery_engine_ranking_location: str = Field(
        default="global",
        alias="DISCOVERY_ENGINE_RANKING_LOCATION",
    )
    discovery_engine_ranking_config: str = Field(
        default="default_ranking_config",
        alias="DISCOVERY_ENGINE_RANKING_CONFIG",
    )
    discovery_engine_ranking_model: str = Field(
        default="semantic-ranker-default-v1",
        alias="DISCOVERY_ENGINE_RANKING_MODEL",
    )
    google_impersonate_service_account: str | None = Field(
        default=None,
        alias="GOOGLE_IMPERSONATE_SERVICE_ACCOUNT",
    )
    google_application_credentials: str | None = Field(
        default=None,
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )

    # Seller Agent models — override via AGENT_MODEL env var
    agent_model: str = Field(
        default="gemini-3-flash-preview", alias="AGENT_MODEL"
    )
    llm_usage_cost_micros_per_1k_tokens: str = Field(
        default=(
            "gemini:input:300,gemini:output:2500,"
            "vertex:input:300,vertex:output:2500,"
            "cerebras:input:600,cerebras:output:1200"
        ),
        alias="LLM_USAGE_COST_MICROS_PER_1K_TOKENS",
    )
    llm_daily_cost_budget_micros_per_workspace: int = Field(
        default=0,
        ge=0,
        alias="LLM_DAILY_COST_BUDGET_MICROS_PER_WORKSPACE",
    )

    # Cerebras / Qwen3
    cerebras_api_key: str | None = Field(default=None, alias="CEREBRAS_API_KEY")
    cerebras_base_url: str = Field(
        default="https://api.cerebras.ai/v1", alias="CEREBRAS_BASE_URL"
    )
    cerebras_default_model: str = Field(
        default="qwen-3-235b-a22b-instruct-2507", alias="CEREBRAS_DEFAULT_MODEL"
    )
    cerebras_timeout: float = Field(default=5.0, alias="CEREBRAS_TIMEOUT")

    # Rollback / safety gates
    rollback_hallucination_rate_threshold: float = Field(
        default=0.03, alias="ROLLBACK_HALLUCINATION_RATE_THRESHOLD"
    )
    rollback_complaint_rate_threshold: float = Field(
        default=0.05, alias="ROLLBACK_COMPLAINT_RATE_THRESHOLD"
    )
    rollback_delivery_failure_rate_threshold: float = Field(
        default=0.02, alias="ROLLBACK_DELIVERY_FAILURE_RATE_THRESHOLD"
    )
    rollback_severe_incorrect_fact_levels: str = Field(
        default="major,critical", alias="ROLLBACK_SEVERE_INCORRECT_FACT_LEVELS"
    )

    # Media storage
    media_dir: str = Field(
        default="./media", alias="MEDIA_DIR"
    )

    # Runtime workers
    # Off-lease records plane + turn-runner concurrency bounds (PRD #433). All four
    # are env-overridable so the pilot VM can dial them (e.g. dispatch concurrency
    # back to 1 to restore serial behavior) without a redeploy.
    records_queue_maxsize: int = Field(
        default=256, alias="RECORDS_QUEUE_MAXSIZE", ge=1
    )
    records_consumer_pool_size: int = Field(
        default=2, alias="RECORDS_CONSUMER_POOL_SIZE", ge=1
    )
    crm_schema_refresh_enabled: bool = Field(
        default=True, alias="CRM_SCHEMA_REFRESH_ENABLED"
    )
    crm_schema_refresh_interval_seconds: int = Field(
        default=21600, alias="CRM_SCHEMA_REFRESH_INTERVAL_SECONDS", ge=60
    )
    turn_runner_dispatch_concurrency: int = Field(
        default=4, alias="TURN_RUNNER_DISPATCH_CONCURRENCY", ge=1
    )
    turn_runner_max_per_workspace: int = Field(
        default=4, alias="TURN_RUNNER_MAX_PER_WORKSPACE", ge=1
    )
    event_spine_persist_consumer_enabled: bool = Field(
        default=True, alias="EVENT_SPINE_PERSIST_CONSUMER_ENABLED"
    )
    event_spine_persist_mode: str = Field(
        default="authoritative", alias="EVENT_SPINE_PERSIST_MODE"
    )
    delivery_reconciler_enabled: bool = Field(
        default=False, alias="DELIVERY_RECONCILER_ENABLED"
    )
    delivery_reconciler_poll_interval_seconds: float = Field(
        default=60.0, alias="DELIVERY_RECONCILER_POLL_INTERVAL_SECONDS"
    )
    action_runtime_worker_enabled: bool = Field(
        default=False, alias="ACTION_RUNTIME_WORKER_ENABLED"
    )
    action_runtime_worker_poll_interval_seconds: float = Field(
        default=2.0, alias="ACTION_RUNTIME_WORKER_POLL_INTERVAL_SECONDS"
    )
    action_runtime_worker_batch_size: int = Field(
        default=10,
        alias="ACTION_RUNTIME_WORKER_BATCH_SIZE",
        ge=1,
        le=50,
    )
    onboarding_runtime_worker_enabled: bool = Field(
        default=True, alias="ONBOARDING_RUNTIME_WORKER_ENABLED"
    )
    onboarding_runtime_worker_poll_interval_seconds: float = Field(
        default=2.0, alias="ONBOARDING_RUNTIME_WORKER_POLL_INTERVAL_SECONDS"
    )
    onboarding_runtime_worker_batch_size: int = Field(
        default=2,
        alias="ONBOARDING_RUNTIME_WORKER_BATCH_SIZE",
        ge=1,
        le=25,
    )
    onboarding_source_unit_embeddings_enabled: bool = Field(
        default=True,
        alias="ONBOARDING_SOURCE_UNIT_EMBEDDINGS_ENABLED",
    )
    onboarding_contextual_source_units_enabled: bool = Field(
        default=True,
        alias="ONBOARDING_CONTEXTUAL_SOURCE_UNITS_ENABLED",
    )
    onboarding_source_learning_concurrency: int = Field(
        default=4,
        alias="ONBOARDING_SOURCE_LEARNING_CONCURRENCY",
        ge=1,
        le=12,
    )
    source_learning_worker_enabled: bool = Field(
        default=True, alias="SOURCE_LEARNING_WORKER_ENABLED"
    )
    source_learning_worker_poll_interval_seconds: float = Field(
        default=2.0, alias="SOURCE_LEARNING_WORKER_POLL_INTERVAL_SECONDS"
    )
    source_learning_worker_batch_size: int = Field(
        default=8,
        alias="SOURCE_LEARNING_WORKER_BATCH_SIZE",
        ge=1,
        le=50,
    )
    conversation_hydration_worker_enabled: bool = Field(
        default=True, alias="CONVERSATION_HYDRATION_WORKER_ENABLED"
    )
    conversation_hydration_worker_poll_interval_seconds: float = Field(
        default=1.5, alias="CONVERSATION_HYDRATION_WORKER_POLL_INTERVAL_SECONDS"
    )
    conversation_hydration_worker_batch_size: int = Field(
        default=8,
        alias="CONVERSATION_HYDRATION_WORKER_BATCH_SIZE",
        ge=1,
        le=50,
    )
    telegram_auth_recovery_worker_enabled: bool = Field(
        default=True, alias="TELEGRAM_AUTH_RECOVERY_WORKER_ENABLED"
    )
    telegram_auth_recovery_worker_poll_interval_seconds: float = Field(
        default=3.0, alias="TELEGRAM_AUTH_RECOVERY_WORKER_POLL_INTERVAL_SECONDS"
    )
    telegram_auth_recovery_worker_batch_size: int = Field(
        default=10,
        alias="TELEGRAM_AUTH_RECOVERY_WORKER_BATCH_SIZE",
        ge=1,
        le=50,
    )
    telegram_chat_memory_ingestion_worker_enabled: bool = Field(
        default=True,
        alias="TELEGRAM_CHAT_MEMORY_INGESTION_WORKER_ENABLED",
    )
    telegram_chat_memory_ingestion_worker_poll_interval_seconds: float = Field(
        default=2.0,
        alias="TELEGRAM_CHAT_MEMORY_INGESTION_WORKER_POLL_INTERVAL_SECONDS",
    )
    telegram_chat_memory_ingestion_worker_batch_size: int = Field(
        default=100,
        alias="TELEGRAM_CHAT_MEMORY_INGESTION_WORKER_BATCH_SIZE",
        ge=1,
        le=500,
    )
    chat_memory_pair_index_worker_enabled: bool = Field(
        default=True,
        alias="CHAT_MEMORY_PAIR_INDEX_WORKER_ENABLED",
    )
    chat_memory_pair_index_worker_poll_interval_seconds: float = Field(
        default=2.0,
        alias="CHAT_MEMORY_PAIR_INDEX_WORKER_POLL_INTERVAL_SECONDS",
    )
    chat_memory_pair_index_worker_batch_size: int = Field(
        default=50,
        alias="CHAT_MEMORY_PAIR_INDEX_WORKER_BATCH_SIZE",
        ge=1,
        le=250,
    )
    chat_memory_extraction_worker_enabled: bool = Field(
        default=False,
        alias="CHAT_MEMORY_EXTRACTION_WORKER_ENABLED",
    )
    chat_memory_extraction_worker_poll_interval_seconds: float = Field(
        default=2.0,
        alias="CHAT_MEMORY_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS",
    )
    chat_memory_extraction_worker_batch_size: int = Field(
        default=25,
        alias="CHAT_MEMORY_EXTRACTION_WORKER_BATCH_SIZE",
        ge=1,
        le=250,
    )
    trigger_run_router_worker_enabled: bool = Field(
        default=True,
        alias="TRIGGER_RUN_ROUTER_WORKER_ENABLED",
    )
    trigger_run_router_worker_poll_interval_seconds: float = Field(
        default=1.0,
        alias="TRIGGER_RUN_ROUTER_WORKER_POLL_INTERVAL_SECONDS",
    )
    trigger_run_router_worker_batch_size: int = Field(
        default=25,
        alias="TRIGGER_RUN_ROUTER_WORKER_BATCH_SIZE",
        ge=1,
        le=250,
    )
    telegram_presence_online_enabled: bool = Field(
        default=True,
        alias="TELEGRAM_PRESENCE_ONLINE_ENABLED",
    )
    telegram_presence_read_enabled: bool = Field(
        default=True,
        alias="TELEGRAM_PRESENCE_READ_ENABLED",
    )
    telegram_control_bot_token: str | None = Field(
        default=None,
        alias="TELEGRAM_CONTROL_BOT_TOKEN",
    )
    telegram_control_bot_secret_token: str | None = Field(
        default=None,
        alias="TELEGRAM_CONTROL_BOT_SECRET_TOKEN",
    )
    owner_bind_token_ttl_seconds: int = Field(
        default=900,
        alias="OWNER_BIND_TOKEN_TTL_SECONDS",
    )

    # CORS
    cors_origins: str = Field(
        default="http://business.localhost,http://localhost:4200,http://localhost:3001",
        alias="CORS_ORIGINS",
    )

    def get_cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return [o for o in origins if o != "*"]

    def get_rollback_severe_levels(self) -> set[str]:
        return {
            level.strip().lower()
            for level in self.rollback_severe_incorrect_fact_levels.split(",")
            if level.strip()
        }

    def get_admin_workspace_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw in self.admin_workspace_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ids.add(int(raw))
            except ValueError:
                continue
        return ids

    def is_event_spine_authoritative(self) -> bool:
        return self.event_spine_persist_mode.strip().lower() == "authoritative"

    # Security
    secret_key: str = Field(
        default="dev-only-insecure-key-not-for-production", alias="SECRET_KEY"
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 days

    # Cookie settings
    cookie_domain: str = Field(default="", alias="COOKIE_DOMAIN")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    admin_workspace_ids: str = Field(default="", alias="ADMIN_WORKSPACE_IDS")

    # Telegram session encryption
    telegram_session_key: str = Field(default="", alias="TELEGRAM_SESSION_KEY")

    @field_validator(
        "telegram_api_hash",
        "gemini_api_key",
        "google_cloud_project",
        "google_impersonate_service_account",
        "google_application_credentials",
        "cerebras_api_key",
        "telegram_control_bot_token",
        "telegram_control_bot_secret_token",
        mode="before",
    )
    @classmethod
    def _blank_optional_strings_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    def model_post_init(self, _context):
        self._configure_sidecar_defaults()
        self._configure_google_genai_environment()
        self._normalize_postgres_url()
        self._validate_production_settings()

    def _configure_sidecar_defaults(self) -> None:
        if not self.sidecar_api_key and self.environment == "development":
            self.sidecar_api_key = "dev-sidecar-key"

    def _configure_google_genai_environment(self) -> None:
        import os

        if self.google_genai_use_vertexai is True:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            if self.google_cloud_project:
                os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
                os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        elif self.gemini_api_key and not os.environ.get("GOOGLE_API_KEY"):
            # Direct Gemini API key mode
            os.environ["GOOGLE_API_KEY"] = self.gemini_api_key
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
        elif not self.gemini_api_key and not os.environ.get("GOOGLE_API_KEY"):
            # Vertex AI mode — uses Application Default Credentials (gcloud auth)
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            if self.google_cloud_project:
                os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
                os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        if self.google_application_credentials and self.google_auth_source.strip().lower() != "adc":
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS",
                self.google_application_credentials,
            )

    def _normalize_postgres_url(self) -> None:
        if self.postgres_url.startswith("postgres://"):
            self.postgres_url = self.postgres_url.replace(
                "postgres://", "postgresql+asyncpg://", 1
            )
        elif (
            self.postgres_url.startswith("postgresql://")
            and "asyncpg" not in self.postgres_url
        ):
            self.postgres_url = self.postgres_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )

    def _validate_production_settings(self) -> None:
        if self.environment in ("staging", "production"):
            insecure = ["dev-only", "change-me", "insecure", "not-for-production"]
            if any(p in self.secret_key.lower() for p in insecure):
                raise ValueError(
                    "SECRET_KEY must be set to a secure value in production. "
                    "Generate with: openssl rand -hex 32"
                )
            if len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be at least 32 characters in production"
                )
            if not self.telegram_session_key:
                raise ValueError(
                    "TELEGRAM_SESSION_KEY must be set in production. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            # M9: Legacy transport key validation removed (Issue #69)
            # M25: Reject localhost-only CORS
            origins = self.get_cors_origins()
            if origins and all("localhost" in o or "127.0.0.1" in o for o in origins):
                raise ValueError(
                    "CORS_ORIGINS must include at least one non-localhost origin in production "
                    "(e.g., https://your-domain.example)"
                )
            # Require cookie domain and secure flag
            if not self.cookie_domain:
                raise ValueError(
                    "COOKIE_DOMAIN must be set in production (e.g., your-domain.example)"
                )
            if not self.cookie_secure:
                raise ValueError(
                    "COOKIE_SECURE must be true in production"
                )

    @property
    def database_url(self) -> str:
        return self.postgres_url

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
