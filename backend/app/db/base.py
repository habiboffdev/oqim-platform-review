from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


def utc_now_naive() -> datetime:
    """Return current UTC time as naive datetime (for TIMESTAMP WITHOUT TIME ZONE columns)."""
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


from app.models.action_runtime import ActionRuntime  # noqa: F401, E402
from app.models.agent_document import AgentDocumentSection  # noqa: F401, E402
from app.models.agent_session import AgentSession, AgentSessionEvent  # noqa: F401, E402
from app.models.agent_skill import AgentSkill  # noqa: F401, E402
from app.models.commerce_catalog import (  # noqa: F401, E402
    CatalogConflictRecord,
    CatalogMediaRecord,
    CatalogMissingFieldRecord,
    CatalogOfferRecord,
    CatalogProductRecord,
    CatalogSourceFactRecord,
    CatalogVariantRecord,
)
from app.models.commercial_action import (  # noqa: F401, E402
    CommercialActionExecutionRecord,
    CommercialActionProposalRecord,
    CommercialDecisionTraceRecord,
)
from app.models.commercial_spine import (  # noqa: F401, E402
    BusinessBrainFactRecord,
    BusinessBrainIndexRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
    CommercialEventRecord,
    LLMGatewayTraceRecord,
)
from app.models.delivery_runtime import DeliveryRuntime  # noqa: F401, E402
from app.models.hermes_session import HermesSessionMessageRecord, HermesSessionRecord  # noqa: F401, E402
from app.models.knowledge_mcp import (  # noqa: F401, E402
    KnowledgeCandidateRecord,
    KnowledgeChunkRecord,
    KnowledgeCollectionRecord,
    KnowledgeItemRecord,
    KnowledgeSourceRecord,
)
from app.models.media_runtime import MediaRuntime  # noqa: F401, E402
from app.models.message_insight import MessageInsight  # noqa: F401, E402
from app.models.onboarding_runtime import OnboardingRuntime  # noqa: F401, E402
from app.models.telegram_auth_attempt import TelegramAuthAttempt  # noqa: F401, E402
from app.models.telegram_session import TelegramSession  # noqa: F401, E402
from app.models.workspace_budget import WorkspaceBudget  # noqa: F401, E402
