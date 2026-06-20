import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.models.action_runtime import ActionRuntime  # noqa: F401
from app.models.agent import Agent  # noqa: F401
from app.models.commercial_action import (  # noqa: F401
    CommercialActionExecutionRecord,
    CommercialActionProposalRecord,
    CommercialDecisionTraceRecord,
)
from app.models.commercial_spine import (  # noqa: F401
    BusinessBrainFactRecord,
    BusinessBrainIndexRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
    CommercialEventRecord,
    LLMGatewayTraceRecord,
)
from app.models.conversation import Conversation  # noqa: F401
from app.models.conversation_turn_session import ConversationTurnSession  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.delivery_runtime import DeliveryRuntime  # noqa: F401
from app.models.learning_signal import LearningSignal  # noqa: F401
from app.models.media_runtime import MediaRuntime  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.message_insight import MessageInsight  # noqa: F401
from app.models.onboarding_runtime import OnboardingRuntime  # noqa: F401
from app.models.outreach import OutreachCampaign, OutreachTarget  # noqa: F401
from app.models.telegram_session import TelegramSession  # noqa: F401

# Import all models so autogenerate can detect them
from app.models.workspace import Workspace  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url with the app's DATABASE_URL
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    # Alembic 1.14+ creates version_num as VARCHAR(32) by default, but this
    # repo has historical revision ids longer than 32 chars.
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(128) NOT NULL PRIMARY KEY
            )
            """
        )
    )
    connection.execute(
        text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
    )
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
