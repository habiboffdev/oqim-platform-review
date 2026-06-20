from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import DateTime as SqlDateTime, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import (
    get_current_workspace,
    get_db_session,
)
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.conversation_core.messages import get_paginated_message_page
from app.schemas.conversation import (
    ConversationNextBestActionSchema,
    ConversationResponse,
    LiveChatResponse,
    LiveChatsResponse,
    PaginatedConversationsResponse,
)
from app.schemas.crm import CrmPipelineCard, CrmPipelineColumn, CrmPipelineProjectionResponse
from app.schemas.message import (
    HistoryGapResponse,
    PaginatedMessagesResponse,
)
from app.services.conversation_state import (
    CANONICAL_PIPELINE_STAGES,
    get_customer_conversation_state,
    message_effective_time,
    project_conversation_tail,
    project_crm_stage,
    project_message_preview_text,
    project_next_best_action,
    project_visible_gap_repair_request,
    resolved_pipeline_stage,
    resolved_pipeline_stage_expr,
    resolved_products_interested,
    should_surface_older_history_from_state,
)
from app.services.conversation_hydration_runtime import (
    conversation_needs_hydration,
    get_conversation_hydration_runtime,
    latest_local_message_for_conversation,
    project_conversation_hydration_runtime,
)
from app.services.message_response_projection import serialize_message_response

logger = get_logger("api.conversations")

router = APIRouter(prefix="/conversations", tags=["conversations"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

def _safe_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _projected_last_message_at_expr(model: type[Conversation] = Conversation):
    dialog_raw = func.nullif(
        model.crm_state["sync"]["dialog"]["last_message_date"].astext,
        "",
    )
    dialog_at = cast(dialog_raw, SqlDateTime(timezone=True))
    return func.greatest(
        func.coalesce(model.last_message_at, dialog_at),
        func.coalesce(dialog_at, model.last_message_at),
    )


def _build_crm_snapshot_payload(conv: Conversation) -> dict:
    state = get_customer_conversation_state(conv)
    stage = project_crm_stage(conv)
    products = resolved_products_interested(conv)
    lead_score = _safe_optional_float(state.model_extra.get("lead_score") if state.model_extra else None)
    media_ready_value = (state.model_extra or {}).get("media_ready")
    if media_ready_value is not None and not isinstance(media_ready_value, bool):
        media_ready_value = None
    return {
        "pipeline_stage": stage.stage,
        "lead_score": lead_score,
        "last_intent": state.last_intent,
        "products_interested": products[:5] if isinstance(products, list) else [],
        "urgency": bool(state.urgency) if state.urgency is not None else None,
        "needs_attention": bool(conv.needs_attention),
        "media_ready": media_ready_value,
        "last_updated": _parse_iso_dt(state.last_updated),
    }


def _build_next_best_action_payload(conv: Conversation) -> ConversationNextBestActionSchema:
    state = get_customer_conversation_state(conv)
    nba = project_next_best_action(
        state,
        needs_attention=bool(conv.needs_attention),
        override_mode=conv.override_mode or "auto",
    )
    return ConversationNextBestActionSchema(
        action=nba.action,
        ready=nba.ready,
        reason=nba.reason,
    )


async def _count_unread_customer_messages(
    session: AsyncSession,
    conversation_id: int,
) -> int:
    unread_q = select(func.count(Message.id)).where(
        Message.conversation_id == conversation_id,
        Message.is_read.is_(False),
        Message.sender_type == "customer",
    )
    unread_result = await session.execute(unread_q)
    return unread_result.scalar() or 0


async def _load_latest_local_message_preview(
    session: AsyncSession,
    conversation_id: int,
) -> tuple[str | None, datetime | None]:
    latest_ts = func.coalesce(Message.telegram_timestamp, Message.created_at)
    result = await session.execute(
        select(Message.content, Message.media_type, latest_ts)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_(False),
        )
        .order_by(latest_ts.desc().nullslast(), Message.id.desc())
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return None, None
    return project_message_preview_text(row[0], media_type=row[1]), row[2]


async def _load_hydration_payload(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation: Conversation,
    latest_local_message: Message | None = None,
) -> dict | None:
    if latest_local_message is None:
        latest_local_message = await latest_local_message_for_conversation(
            session,
            conversation_id=conversation.id,
        )
    runtime = await get_conversation_hydration_runtime(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation.id,
    )
    needed = conversation_needs_hydration(
        conversation,
        latest_local_message=latest_local_message,
    )
    if runtime is None and not needed:
        return None
    return project_conversation_hydration_runtime(runtime, needed=needed).to_payload()


async def _load_latest_local_message_previews(
    session: AsyncSession,
    conversation_ids: list[int],
) -> tuple[dict[int, str], dict[int, datetime]]:
    if not conversation_ids:
        return {}, {}

    latest_ts = func.coalesce(Message.telegram_timestamp, Message.created_at)
    channel_order_key = func.coalesce(Message.telegram_message_id, Message.id)
    ranked = (
        select(
            Message.conversation_id.label("conversation_id"),
            Message.content.label("content"),
            Message.media_type.label("media_type"),
            latest_ts.label("latest_ts"),
            func.row_number()
            .over(
                partition_by=Message.conversation_id,
                order_by=(
                    latest_ts.desc().nullslast(),
                    channel_order_key.desc().nullslast(),
                    Message.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.is_deleted.is_(False),
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ranked.c.conversation_id,
                ranked.c.content,
                ranked.c.media_type,
                ranked.c.latest_ts,
            )
            .where(ranked.c.rank == 1)
        )
    ).all()
    text_by_conv: dict[int, str] = {}
    ts_by_conv: dict[int, datetime] = {}
    for conversation_id, content, media_type, latest_at in rows:
        text_by_conv[int(conversation_id)] = (
            project_message_preview_text(content, media_type=media_type) or ""
        )
        ts_by_conv[int(conversation_id)] = latest_at
    return text_by_conv, ts_by_conv


def _build_conversation_response(
    conv: Conversation,
    *,
    unread_count: int = 0,
    last_message_at: datetime | None = None,
    last_message_text: str | None = None,
    contact_type: str | None = None,
    has_pending_reply: bool = False,
    latest_reply_confidence: float | None = None,
    include_deal: bool = False,
    tail: dict | None = None,
    hydration: dict | None = None,
) -> ConversationResponse:
    return ConversationResponse(
        id=conv.id,
        customer_id=conv.customer_id,
        customer_name=conv.customer.display_name if conv.customer else None,
        channel=conv.channel,
        telegram_chat_id=conv.telegram_chat_id,
        external_chat_id=conv.external_chat_id,
        external_thread_id=conv.external_thread_id,
        pipeline_stage=resolved_pipeline_stage(conv),
        override_mode=conv.override_mode,
        summary=conv.summary,
        needs_attention=conv.needs_attention,
        read_outbox_max_id=conv.read_outbox_max_id,
        deal_value=float(conv.deal_value) if include_deal and conv.deal_value else None,
        products_mentioned=resolved_products_interested(conv) if include_deal else None,
        last_message_at=last_message_at if last_message_at is not None else conv.last_message_at,
        unread_count=unread_count,
        latest_conversation_seq=conv.message_sequence,
        latest_conversation_revision=conv.message_revision,
        created_at=conv.created_at,
        latest_action=None,
        crm_snapshot=_build_crm_snapshot_payload(conv),
        crm_stage=project_crm_stage(conv),
        next_best_action=_build_next_best_action_payload(conv),
        last_message_text=last_message_text,
        contact_type=contact_type,
        has_pending_reply=has_pending_reply,
        latest_reply_confidence=latest_reply_confidence,
        tail=tail,
        hydration=hydration,
    )


@router.get("", response_model=PaginatedConversationsResponse)
async def list_conversations(
    workspace: WorkspaceDep,
    session: SessionDep,
    stage: str | None = Query(None, description="Filter by pipeline stage"),
    contact_type: str | None = Query(None, description="Filter by customer contact_type"),
    has_pending_reply: bool = Query(False, description="Filter to conversations with pending agent actions"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    cursor: str | None = Query(None, description="Cursor: ISO 8601 timestamp of last item's last_message_at"),
):
    projected_last_message_at = _projected_last_message_at_expr()
    query = (
        select(Conversation)
        .where(Conversation.workspace_id == workspace.id)
        .options(selectinload(Conversation.customer))
        .order_by(projected_last_message_at.desc().nullslast(), Conversation.id.desc())
    )

    if stage:
        query = query.where(resolved_pipeline_stage_expr() == stage)

    if contact_type:
        query = query.join(Customer).where(Customer.contact_type == contact_type)

    if has_pending_reply:
        return PaginatedConversationsResponse(items=[], next_cursor=None)

    if cursor:
        # URL query params may decode '+' as space; restore before parsing
        normalized_cursor = cursor.replace(" ", "+").replace("Z", "+00:00")
        cursor_dt = datetime.fromisoformat(normalized_cursor)
        query = query.where(projected_last_message_at < cursor_dt)

    # Fetch limit+1 to detect if more pages exist
    query = query.limit(limit + 1)

    result = await session.execute(query)
    conversations = list(result.scalars().all())

    has_more = len(conversations) > limit
    if has_more:
        conversations = conversations[:limit]

    # Unread counts come from canonical DB projections.
    conv_ids = [conv.id for conv in conversations]
    unread_by_conv: dict[int, int] = {}
    conv_ids_needing_db = conv_ids
    if conv_ids_needing_db:
        unread_rows = (await session.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(
                Message.conversation_id.in_(conv_ids_needing_db),
                Message.is_read.is_(False),
                Message.sender_type == "customer",
            )
            .group_by(Message.conversation_id)
        )).all()
        for row in unread_rows:
            unread_by_conv[row[0]] = row[1]

    msg_text_by_conv, msg_ts_by_conv = await _load_latest_local_message_previews(
        session,
        conv_ids,
    )

    # Batch fetch contact_type per customer
    contact_type_by_conv: dict[int, str] = {}
    if conv_ids:
        customer_ids = [conv.customer_id for conv in conversations]
        ct_rows = (await session.execute(
            select(Customer.id, Customer.contact_type)
            .where(Customer.id.in_(customer_ids))
        )).all()
        cust_ct = {row[0]: row[1] for row in ct_rows}
        contact_type_by_conv = {
            conv.id: cust_ct.get(conv.customer_id, "customer")
            for conv in conversations
        }

    items = []
    for conv in conversations:
        local_message_text = msg_text_by_conv.get(conv.id)
        tail = project_conversation_tail(
            conv,
            local_text=local_message_text,
            local_at=msg_ts_by_conv.get(conv.id),
            db_unread_count=unread_by_conv.get(conv.id, 0),
        )
        items.append(
            _build_conversation_response(
                conv,
                unread_count=tail.unread_count,
                last_message_at=tail.latest_message_at,
                last_message_text=tail.latest_message_text,
                contact_type=contact_type_by_conv.get(conv.id),
                has_pending_reply=False,
                latest_reply_confidence=None,
                tail=tail.to_payload(),
            )
        )

    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        if last_item.last_message_at:
            next_cursor = last_item.last_message_at.isoformat()

    return PaginatedConversationsResponse(items=items, next_cursor=next_cursor)


@router.get("/pipeline", response_model=CrmPipelineProjectionResponse)
async def get_pipeline_projection(
    workspace: WorkspaceDep,
    session: SessionDep,
    contact_type: str | None = Query(None, description="Filter by customer contact_type"),
    limit: int = Query(500, ge=1, le=1000),
):
    projected_last_message_at = _projected_last_message_at_expr()
    query = (
        select(Conversation)
        .where(Conversation.workspace_id == workspace.id)
        .options(selectinload(Conversation.customer))
        .order_by(projected_last_message_at.desc().nullslast(), Conversation.id.desc())
        .limit(limit)
    )
    if contact_type:
        query = query.join(Customer).where(Customer.contact_type == contact_type)

    result = await session.execute(query)
    conversations = list(result.scalars().all())
    conv_ids = [conv.id for conv in conversations]
    unread_by_conv: dict[int, int] = {}
    if conv_ids:
        unread_rows = (
            await session.execute(
                select(Message.conversation_id, func.count(Message.id))
                .where(
                    Message.conversation_id.in_(conv_ids),
                    Message.is_read.is_(False),
                    Message.sender_type == "customer",
                )
                .group_by(Message.conversation_id)
            )
        ).all()
        unread_by_conv = {int(row[0]): int(row[1]) for row in unread_rows}

    msg_text_by_conv, msg_ts_by_conv = await _load_latest_local_message_previews(
        session,
        conv_ids,
    )
    grouped: dict[str, list[CrmPipelineCard]] = {
        stage: [] for stage in CANONICAL_PIPELINE_STAGES
    }
    for conv in conversations:
        stage = project_crm_stage(conv)
        local_message_text = msg_text_by_conv.get(conv.id)
        tail = project_conversation_tail(
            conv,
            local_text=local_message_text,
            local_at=msg_ts_by_conv.get(conv.id),
            db_unread_count=unread_by_conv.get(conv.id, 0),
        )
        grouped.setdefault(stage.stage, []).append(
            CrmPipelineCard(
                conversation_id=conv.id,
                customer_id=conv.customer_id,
                customer_name=conv.customer.display_name if conv.customer else None,
                channel=conv.channel,
                stage=stage,
                last_message_text=tail.latest_message_text,
                last_message_at=tail.latest_message_at,
                unread_count=tail.unread_count,
                has_pending_reply=False,
                latest_reply_confidence=None,
                contact_type=conv.customer.contact_type if conv.customer else None,
                needs_attention=bool(conv.needs_attention),
                deal_value=float(conv.deal_value) if conv.deal_value else None,
            )
        )

    return CrmPipelineProjectionResponse(
        total=len(conversations),
        stages=[
            CrmPipelineColumn(stage=stage, count=len(grouped[stage]), cards=grouped[stage])
            for stage in CANONICAL_PIPELINE_STAGES
        ],
    )


def _compute_has_ai(
    conversation: Conversation | None,
    customer: Customer | None,
) -> bool:
    """Derive whether Seller Agent reply generation is active for a chat."""
    if not conversation or not customer:
        return False
    # Explicit override takes priority
    if conversation.override_mode == "off":
        return False
    if conversation.override_mode == "force_draft":
        return True
    # Auto mode: need customer classification with sufficient confidence
    if customer.classification_corrected:
        # Manually corrected contacts are trusted implicitly
        return customer.contact_type == "customer"
    if customer.classification_confidence is None or customer.classification_confidence < 0.7:
        return False
    return customer.contact_type == "customer"


@router.get("/live", response_model=LiveChatsResponse)
async def live_chats(
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Chat list from DB (populated by bridge catch-up sync).

    Returns private 1:1 chats with DB ordering, unread counts,
    contact_type, pending reply status. Gateway removed — Issue #69.
    """
    # Conversations with customers
    convs_result = await session.execute(
        select(Conversation, Customer)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(
            Conversation.workspace_id == workspace.id,
        )
        .order_by(_projected_last_message_at_expr().desc().nullslast(), Conversation.id.desc())
    )
    rows = convs_result.all()

    conv_ids = [conv.id for conv, _ in rows]
    latest_msg_text_by_conv, latest_msg_ts_by_conv = await _load_latest_local_message_previews(
        session,
        conv_ids,
    )

    unread_by_conv: dict[int, int] = {}
    if conv_ids:
        unread_rows = (await session.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(
                Message.conversation_id.in_(conv_ids),
                Message.is_read.is_(False),
                Message.sender_type == "customer",
            )
            .group_by(Message.conversation_id)
        )).all()
        unread_by_conv = {row[0]: int(row[1]) for row in unread_rows}

    chats: list[LiveChatResponse] = []
    for conv, customer in rows:
        unread_count = unread_by_conv.get(conv.id, 0)
        tail = project_conversation_tail(
            conv,
            local_text=latest_msg_text_by_conv.get(conv.id),
            local_at=latest_msg_ts_by_conv.get(conv.id),
            db_unread_count=unread_count,
        )
        conversation_state = get_customer_conversation_state(conv)
        dialog_state = conversation_state.sync.dialog if conversation_state.sync else None
        chats.append(LiveChatResponse(
            telegram_chat_id=conv.telegram_chat_id,
            telegram_user_id=customer.telegram_id or conv.telegram_chat_id,
            channel=conv.channel,
            display_name=customer.display_name or "Unknown",
            phone=None,
            unread_count=tail.unread_count,
            last_message_text=tail.latest_message_text or "",
            last_message_date=tail.latest_message_at.isoformat() if tail.latest_message_at else None,
            last_message_is_outgoing=bool(dialog_state.last_message_is_outgoing) if dialog_state else False,
            read_outbox_max_id=conv.read_outbox_max_id or 0,
            contact_type=customer.contact_type,
            has_ai=_compute_has_ai(conv, customer),
            has_pending_reply=False,
            conversation_id=conv.id,
            customer_id=customer.id,
        ))

    return LiveChatsResponse(chats=chats, count=len(chats))


@router.get("/by-telegram-chat/{telegram_chat_id}", response_model=ConversationResponse)
async def get_conversation_by_telegram_chat(
    telegram_chat_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Lookup conversation by telegram_chat_id — O(1) instead of client-side O(n) scan."""
    result = await session.execute(
        select(Conversation)
        .options(selectinload(Conversation.customer))
        .where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == telegram_chat_id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    unread_count = await _count_unread_customer_messages(session, conv.id)
    local_text, local_at = await _load_latest_local_message_preview(session, conv.id)
    tail = project_conversation_tail(
        conv,
        local_text=local_text,
        local_at=local_at,
        db_unread_count=unread_count,
    )
    hydration = await _load_hydration_payload(
        session,
        workspace_id=workspace.id,
        conversation=conv,
    )
    return _build_conversation_response(
        conv,
        unread_count=tail.unread_count,
        last_message_at=tail.latest_message_at,
        last_message_text=tail.latest_message_text,
        include_deal=True,
        tail=tail.to_payload(),
        hydration=hydration,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
        .options(
            selectinload(Conversation.customer),
            selectinload(Conversation.messages),
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    unread_count = await _count_unread_customer_messages(session, conv.id)
    local_text, local_at = await _load_latest_local_message_preview(session, conv.id)
    tail = project_conversation_tail(
        conv,
        local_text=local_text,
        local_at=local_at,
        db_unread_count=unread_count,
    )
    hydration = await _load_hydration_payload(
        session,
        workspace_id=workspace.id,
        conversation=conv,
    )
    return _build_conversation_response(
        conv,
        unread_count=tail.unread_count,
        last_message_at=tail.latest_message_at,
        last_message_text=tail.latest_message_text,
        include_deal=True,
        tail=tail.to_payload(),
        hydration=hydration,
    )


@router.get("/{conversation_id}/messages", response_model=PaginatedMessagesResponse)
async def get_conversation_messages(
    conversation_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: int = Query(50, le=200),
    before_id: int | None = Query(None, description="Fetch messages with id < before_id"),
    after_conversation_seq: int | None = Query(
        None,
        description="Fetch messages with conversation_seq > after_conversation_seq",
    ),
):
    if before_id is not None and after_conversation_seq is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="before_id and after_conversation_seq cannot be combined",
        )

    # Verify conversation belongs to workspace
    conv_result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    page = await get_paginated_message_page(
        session,
        conversation=conv,
        limit=limit,
        before_id=before_id,
        after_conversation_seq=after_conversation_seq,
    )
    has_older = should_surface_older_history_from_state(
        conv,
        page_has_older=page.has_older,
        oldest_message=page.items[0] if page.items else None,
    )

    media_runtime_by_message_id: dict[int, MediaRuntime] = {}
    delivery_runtime_by_message_id: dict[int, DeliveryRuntime] = {}
    media_message_ids = [message.id for message in page.items if message.media_type]
    if media_message_ids:
        media_rows = await session.scalars(
            select(MediaRuntime).where(MediaRuntime.message_id.in_(media_message_ids))
        )
        media_runtime_by_message_id = {
            runtime.message_id: runtime for runtime in media_rows.all()
        }
    message_ids = [message.id for message in page.items]
    if message_ids:
        delivery_rows = await session.scalars(
            select(DeliveryRuntime).where(DeliveryRuntime.message_id.in_(message_ids))
        )
        delivery_runtime_by_message_id = {
            runtime.message_id: runtime for runtime in delivery_rows.all()
        }

    responses = [
        serialize_message_response(
            message,
            conv,
            media_runtime_by_message_id.get(message.id),
            delivery_runtime_by_message_id.get(message.id),
        )
        for message in page.items
    ]
    gap_request = project_visible_gap_repair_request(conv, messages=page.items)
    history_gap = (
        HistoryGapResponse(
            reason=gap_request.reason,
            before_external_message_id=gap_request.before_external_message_id,
            after_external_message_id=gap_request.after_external_message_id,
        )
        if gap_request
        else None
    )
    latest_local_message = await latest_local_message_for_conversation(
        session,
        conversation_id=conv.id,
    )
    tail = project_conversation_tail(
        conv,
        local_text=latest_local_message.content if latest_local_message else None,
        local_media_type=latest_local_message.media_type if latest_local_message else None,
        local_at=message_effective_time(latest_local_message) if latest_local_message else None,
        db_unread_count=await _count_unread_customer_messages(session, conv.id),
        messages=page.items,
    )
    hydration = await _load_hydration_payload(
        session,
        workspace_id=workspace.id,
        conversation=conv,
        latest_local_message=latest_local_message,
    )
    return PaginatedMessagesResponse(
        items=responses,
        has_older=has_older,
        latest_conversation_seq=conv.message_sequence,
        latest_conversation_revision=conv.message_revision,
        history_gap=history_gap,
        tail=tail.to_payload(),
        hydration=hydration,
    )
