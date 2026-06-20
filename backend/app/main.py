from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select

from app.api.middleware.csrf import CSRFMiddleware
from app.api.middleware.rate_limit import rate_limit_exceeded_handler
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.routes import (
    action_runtime,
    agent_control,
    agent_runtime,
    agents,
    amocrm_auth,
    auth,
    bi_promoter,
    brain_agent_documents,
    brain_documents,
    brain_skills,
    business_brain,
    commercial_spine,
    conversation_commands,
    conversations,
    customers,
    health,
    instagram_auth,
    intelligence,
    media,
    messages,
    onboarding,
    sync,
    telegram_auth,
    webhook,
    webhook_amocrm,
    webhook_instagram,
    workspace_os,
    ws,
)
from app.brain.token_tracker import init_token_tracker
from app.core.config import get_settings
from app.core.correlation import CorrelationIdMiddleware
from app.core.event_spine import EventSpine
from app.core.logging import get_logger
from app.models.workspace import Workspace
from app.modules.action_runtime.worker import ActionRuntimeWorker
from app.modules.conversation_turns.runner import ConversationTurnRunner
from app.modules.triggers.run_router import TriggerRunRouterWorker
from app.services.brain_index_reconciler import BrainIndexReconciler
from app.services.channel_conversation_sync import ChannelConversationSync
from app.services.chat_memory_extraction_worker import ChatMemoryExtractionWorker
from app.services.chat_memory_pair_index_worker import ChatMemoryPairIndexWorker
from app.services.consumer_supervisor import ConsumerSupervisor
from app.services.conversation_hydration_worker import ConversationHydrationWorker
from app.services.delivery import DeliveryService
from app.services.event_spine_diff_consumer import EventSpineDiffConsumer
from app.services.event_spine_persist_consumer import EventSpinePersistConsumer
from app.services.media_hydration_worker import MediaHydrationWorker
from app.services.onboarding_runtime import OnboardingRuntimeWorker
from app.services.source_learning_worker import SourceLearningWorker
from app.services.telegram_auth_recovery import TelegramAuthRecoveryWorker
from app.services.telegram_chat_memory_ingestion_worker import TelegramChatMemoryIngestionWorker

settings = get_settings()
logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: C901 (flat startup orchestration — registers ~20 supervised workers in sequence; splitting adds indirection without reducing real risk)
    logger.info("OQIM Business API starting...")

    # App Redis client (db=0) — for token tracking and other app-level caching
    app_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.app_redis = app_redis
    init_token_tracker(app_redis)

    # Pub/sub Redis for cross-worker WebSocket broadcast (must use decode_responses=False)
    pubsub_redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    app.state.pubsub_redis = pubsub_redis

    from app.api.routes.ws import manager as ws_manager

    ws_manager.set_redis(app_redis)
    ws_manager.set_pubsub_redis(pubsub_redis)
    await ws_manager.start_subscriber()

    from app.db.session import async_session

    def _event_spine_db_factory():
        return async_session()

    # Event spine (RFC #202) — durable append-only log of canonical message
    # events. DeliveryService depends on it for send/confirm correlation.
    app.state.event_spine = EventSpine(
        app_redis,
        db_factory=_event_spine_db_factory,
    )

    # Unified delivery service — sends via GramJS sidecar with typing + delay + retry.
    app.state.delivery = DeliveryService(
        sidecar_url=settings.sidecar_url,
        sidecar_api_key=settings.sidecar_api_key,
        event_spine=app.state.event_spine,
    )

    # Start consumers via supervisor (auto-restart on crash, liveness tracking)
    supervisor = ConsumerSupervisor()

    conversation_turn_runner = ConversationTurnRunner(
        redis_url=settings.redis_url,
        delivery=app.state.delivery,
        dispatch_concurrency=settings.turn_runner_dispatch_concurrency,
        max_per_workspace=settings.turn_runner_max_per_workspace,
    )
    # Concurrent dispatch (PRD #433) beats once per lease at semaphore acquisition,
    # then gathers the whole batch; a legitimately slow reply LLM call can exceed the
    # default 15s heartbeat window and flap the health report to unhealthy. A wider
    # window keeps a slow-but-live concurrent batch from reading as stale.
    supervisor.register(
        "conversation_turn_runner",
        conversation_turn_runner,
        heartbeat_timeout_seconds=60.0,
    )
    app.state.conversation_turn_runner = conversation_turn_runner

    # Off-lease records plane (PRD #433): the dispatcher enqueues a RecordsJob onto
    # this process-singleton queue; a supervised pool drains it into run_records_pass
    # so the records pass never blocks the reply lease. No-ops with an empty queue.
    from app.modules.agent_runtime_v2.records_queue import (
        RecordsConsumer,
        RecordsQueue,
        set_records_queue,
    )

    records_queue = RecordsQueue(maxsize=settings.records_queue_maxsize)
    set_records_queue(records_queue)
    records_consumer = RecordsConsumer(
        queue=records_queue,
        pool_size=settings.records_consumer_pool_size,
    )
    supervisor.register(
        "records_consumer",
        records_consumer,
        heartbeat_timeout_seconds=60.0,
    )
    app.state.records_queue = records_queue
    app.state.records_consumer = records_consumer

    # Event spine diff consumer (RFC #202 Phase 1, observability-only).
    # Supervised; auto-restart on crash with exponential backoff. Reads
    # oqim:events:* streams, compares to DB, emits divergence metrics.
    media_hydration_worker = MediaHydrationWorker(
        db_factory=_event_spine_db_factory,
        lease_owner="media_hydration_worker",
        agent_turn_wakeup=lambda workspace_id, conversation_id: conversation_turn_runner.enqueue_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        ),
        media_event_append=app.state.event_spine.append,
    )
    supervisor.register(
        "media_hydration",
        media_hydration_worker,
        heartbeat_timeout_seconds=30.0,
    )
    app.state.media_hydration_worker = media_hydration_worker

    async def _workspace_ids_provider() -> list[int]:
        async with async_session() as session:
            result = await session.execute(select(Workspace.id))
            return [int(row[0]) for row in result.all()]

    event_spine_diff = EventSpineDiffConsumer(
        redis=app_redis,
        db_factory=_event_spine_db_factory,
        workspace_ids_provider=_workspace_ids_provider,
    )
    supervisor.register(
        "event_spine_diff",
        event_spine_diff,
        heartbeat_timeout_seconds=30.0,
    )
    app.state.event_spine_diff_consumer = event_spine_diff

    brain_index_reconciler = BrainIndexReconciler(db_factory=_event_spine_db_factory)
    supervisor.register(
        "brain_index_reconciler",
        brain_index_reconciler,
        heartbeat_timeout_seconds=30.0,
    )
    app.state.brain_index_reconciler = brain_index_reconciler

    from app.services.instagram_token_refresher import InstagramTokenRefresher

    instagram_token_refresher = InstagramTokenRefresher(db_factory=_event_spine_db_factory)
    supervisor.register(
        "instagram_token_refresher",
        instagram_token_refresher,
        heartbeat_timeout_seconds=120.0,
    )
    app.state.instagram_token_refresher = instagram_token_refresher

    # CRM sync reconciler: drains desired lead state in crm_lead_links to the
    # connected CRM (amoCRM). All CRM HTTP lives here; no-ops with zero
    # connections, so it is safe even before any workspace connects a CRM.
    from app.modules.crm_connector.sync_worker import CrmSyncWorker

    crm_sync_worker = CrmSyncWorker(db_factory=_event_spine_db_factory)
    supervisor.register(
        "crm_sync_worker",
        crm_sync_worker,
        heartbeat_timeout_seconds=60.0,
    )
    app.state.crm_sync_worker = crm_sync_worker

    # CRM token refresher: rotates near-expiry amoCRM tokens (single-use safe via
    # the row-locked refresh). No-ops with zero connections.
    from app.modules.crm_connector.token_refresher import CrmTokenRefresher

    crm_token_refresher = CrmTokenRefresher(db_factory=_event_spine_db_factory)
    supervisor.register(
        "crm_token_refresher",
        crm_token_refresher,
        heartbeat_timeout_seconds=120.0,
    )
    app.state.crm_token_refresher = crm_token_refresher

    # CRM schema refresher: re-reads the amoCRM schema (no reconnect) so OQIM stays
    # in sync as the owner changes pipelines/stages/fields. No-ops with zero conns.
    if settings.crm_schema_refresh_enabled:
        from app.modules.crm_connector.schema_refresher import CrmSchemaRefresher

        crm_schema_refresher = CrmSchemaRefresher(
            db_factory=_event_spine_db_factory,
            interval_seconds=settings.crm_schema_refresh_interval_seconds,
        )
        supervisor.register(
            # generous timeout: one connection's discovery is 5 sequential amoCRM
            # reads (~up to 5x the 20s client timeout) between heartbeats.
            "crm_schema_refresher", crm_schema_refresher, heartbeat_timeout_seconds=300.0,
        )
        app.state.crm_schema_refresher = crm_schema_refresher

    # Owner control bot: polls Bot API updates (binding + Approve/Reject) and
    # flushes agent-created owner notifications/approval cards. Tokens are
    # per-workspace (self-provisioned via BotFather — provisioner.py), with the
    # env token as a global fallback; without any token the worker idles and
    # owner items stay inspectable as backend records (#405).
    from app.modules.telegram_control_bot.service import HermesTelegramBotGatewayClient
    from app.modules.telegram_control_bot.worker import OwnerControlBotWorker

    owner_control_bot = OwnerControlBotWorker(
        db_factory=_event_spine_db_factory,
        client=(
            HermesTelegramBotGatewayClient(token=settings.telegram_control_bot_token)
            if settings.telegram_control_bot_token
            else None
        ),
        client_factory=lambda token: HermesTelegramBotGatewayClient(token=token),
    )
    supervisor.register(
        "owner_control_bot",
        owner_control_bot,
        heartbeat_timeout_seconds=60.0,
    )
    app.state.owner_control_bot = owner_control_bot

    # Promoter warm drip: re-opens existing dialogs for running outreach
    # campaigns under hard pacing caps (working hours, jitter, batch of 1).
    # No-ops with zero running campaigns. Cold resolution is Slice C.
    from app.modules.bi_promoter.drip_worker import PromoterDripWorker

    promoter_drip_worker = PromoterDripWorker(
        db_factory=_event_spine_db_factory,
        delivery=app.state.delivery,
    )
    supervisor.register(
        "promoter_drip_worker",
        promoter_drip_worker,
        heartbeat_timeout_seconds=180.0,
    )
    app.state.promoter_drip_worker = promoter_drip_worker

    if settings.event_spine_persist_consumer_enabled:
        event_spine_persist = EventSpinePersistConsumer(
            redis=app_redis,
            db_factory=_event_spine_db_factory,
            workspace_ids_provider=_workspace_ids_provider,
            conversation_turn_runner=conversation_turn_runner,
            mode=settings.event_spine_persist_mode,
        )
        supervisor.register(
            "event_spine_persist",
            event_spine_persist,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.event_spine_persist_consumer = event_spine_persist

    if settings.delivery_reconciler_enabled:
        from app.modules.channel_layer.reconciler_worker import DeliveryReconcilerWorker

        delivery_reconciler_worker = DeliveryReconcilerWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=settings.delivery_reconciler_poll_interval_seconds,
        )
        supervisor.register(
            "delivery_reconciler",
            delivery_reconciler_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.delivery_reconciler_worker = delivery_reconciler_worker

    if settings.action_runtime_worker_enabled:
        action_runtime_worker = ActionRuntimeWorker(
            db_factory=_event_spine_db_factory,
            delivery=app.state.delivery,
            redis=app_redis,
            poll_interval_seconds=(settings.action_runtime_worker_poll_interval_seconds),
            batch_size=settings.action_runtime_worker_batch_size,
        )
        supervisor.register(
            "action_runtime",
            action_runtime_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.action_runtime_worker = action_runtime_worker

    if settings.trigger_run_router_worker_enabled:
        trigger_run_router_worker = TriggerRunRouterWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=(
                settings.trigger_run_router_worker_poll_interval_seconds
            ),
            batch_size=settings.trigger_run_router_worker_batch_size,
        )
        supervisor.register(
            "trigger_run_router",
            trigger_run_router_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.trigger_run_router_worker = trigger_run_router_worker

    if settings.onboarding_runtime_worker_enabled:
        onboarding_runtime_worker = OnboardingRuntimeWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=settings.onboarding_runtime_worker_poll_interval_seconds,
            batch_size=settings.onboarding_runtime_worker_batch_size,
            sync_factory=lambda: ChannelConversationSync(
                event_append=app.state.event_spine.append,
            ),
        )
        supervisor.register(
            "onboarding_runtime",
            onboarding_runtime_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.onboarding_runtime_worker = onboarding_runtime_worker

    if settings.source_learning_worker_enabled:
        source_learning_worker = SourceLearningWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=settings.source_learning_worker_poll_interval_seconds,
            batch_size=settings.source_learning_worker_batch_size,
            max_parallelism=settings.onboarding_source_learning_concurrency,
        )
        supervisor.register(
            "source_learning",
            source_learning_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.source_learning_worker = source_learning_worker

    if settings.conversation_hydration_worker_enabled:
        conversation_hydration_worker = ConversationHydrationWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=settings.conversation_hydration_worker_poll_interval_seconds,
            batch_size=settings.conversation_hydration_worker_batch_size,
            sync_service=ChannelConversationSync(event_append=app.state.event_spine.append),
        )
        supervisor.register(
            "conversation_hydration",
            conversation_hydration_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.conversation_hydration_worker = conversation_hydration_worker

    if settings.telegram_auth_recovery_worker_enabled:
        telegram_auth_recovery_worker = TelegramAuthRecoveryWorker(
            db_factory=_event_spine_db_factory,
            redis=app_redis,
            poll_interval_seconds=settings.telegram_auth_recovery_worker_poll_interval_seconds,
            batch_size=settings.telegram_auth_recovery_worker_batch_size,
        )
        supervisor.register(
            "telegram_auth_recovery",
            telegram_auth_recovery_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.telegram_auth_recovery_worker = telegram_auth_recovery_worker

    if settings.telegram_chat_memory_ingestion_worker_enabled:
        telegram_chat_memory_ingestion_worker = TelegramChatMemoryIngestionWorker(
            db_factory=_event_spine_db_factory,
            workspace_ids_provider=_workspace_ids_provider,
            redis=app_redis,
            poll_interval_seconds=(
                settings.telegram_chat_memory_ingestion_worker_poll_interval_seconds
            ),
            batch_size=settings.telegram_chat_memory_ingestion_worker_batch_size,
        )
        supervisor.register(
            "telegram_chat_memory_ingestion",
            telegram_chat_memory_ingestion_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.telegram_chat_memory_ingestion_worker = telegram_chat_memory_ingestion_worker

    if settings.chat_memory_pair_index_worker_enabled:
        chat_memory_pair_index_worker = ChatMemoryPairIndexWorker(
            db_factory=_event_spine_db_factory,
            workspace_ids_provider=_workspace_ids_provider,
            redis=app_redis,
            poll_interval_seconds=settings.chat_memory_pair_index_worker_poll_interval_seconds,
            batch_size=settings.chat_memory_pair_index_worker_batch_size,
        )
        supervisor.register(
            "chat_memory_pair_index",
            chat_memory_pair_index_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.chat_memory_pair_index_worker = chat_memory_pair_index_worker

    if settings.chat_memory_extraction_worker_enabled:
        chat_memory_extraction_worker = ChatMemoryExtractionWorker(
            db_factory=_event_spine_db_factory,
            workspace_ids_provider=_workspace_ids_provider,
            redis=app_redis,
            poll_interval_seconds=(
                settings.chat_memory_extraction_worker_poll_interval_seconds
            ),
            batch_size=settings.chat_memory_extraction_worker_batch_size,
        )
        supervisor.register(
            "chat_memory_extraction",
            chat_memory_extraction_worker,
            heartbeat_timeout_seconds=30.0,
        )
        app.state.chat_memory_extraction_worker = chat_memory_extraction_worker

    await supervisor.start_all()
    app.state.supervisor = supervisor

    yield

    # Shutdown
    logger.info("OQIM Business API shutting down...")
    await ws_manager.stop_subscriber()
    # Drain pending event spine publishes before tearing down Redis.
    try:
        await app.state.event_spine.drain(timeout=5.0)
    except Exception as exc:
        logger.warning("event_spine drain on shutdown failed: %s", exc)
    await supervisor.stop_all()
    await pubsub_redis.aclose()
    await app_redis.aclose()
    logger.info("OQIM Business API shut down cleanly")


app = FastAPI(
    title=settings.project_name,
    version=settings.version,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CSRFMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Correlation ID wraps everything below it — must be added LAST so it runs
# FIRST in the request chain, giving downstream middleware + handlers a cid.
app.add_middleware(CorrelationIdMiddleware)

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# Routes — health at root, everything else under /api
app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(agents.router, prefix=settings.api_prefix)
app.include_router(conversations.router, prefix=settings.api_prefix)
app.include_router(conversation_commands.router, prefix=settings.api_prefix)
app.include_router(customers.router, prefix=settings.api_prefix)
app.include_router(bi_promoter.router, prefix=settings.api_prefix)
app.include_router(media.router, prefix=settings.api_prefix)
app.include_router(brain_documents.router, prefix=settings.api_prefix)
app.include_router(brain_agent_documents.router, prefix=settings.api_prefix)
app.include_router(brain_skills.router, prefix=settings.api_prefix)
app.include_router(business_brain.router, prefix=settings.api_prefix)
app.include_router(intelligence.router, prefix=settings.api_prefix)
app.include_router(commercial_spine.router, prefix=settings.api_prefix)
app.include_router(action_runtime.router, prefix=settings.api_prefix)
app.include_router(agent_runtime.router, prefix=settings.api_prefix)
app.include_router(agent_control.router, prefix=settings.api_prefix)
app.include_router(onboarding.router, prefix=settings.api_prefix)
app.include_router(workspace_os.router, prefix=settings.api_prefix)
app.include_router(messages.router, prefix=settings.api_prefix)
app.include_router(telegram_auth.router, prefix=settings.api_prefix)
app.include_router(instagram_auth.router, prefix=settings.api_prefix)
app.include_router(amocrm_auth.router, prefix=settings.api_prefix)
app.include_router(sync.router, prefix=settings.api_prefix)
app.include_router(ws.router, prefix=settings.api_prefix)
app.include_router(webhook.router, prefix=settings.api_prefix)
app.include_router(webhook_instagram.router, prefix=settings.api_prefix)
app.include_router(webhook_amocrm.router, prefix=settings.api_prefix)
