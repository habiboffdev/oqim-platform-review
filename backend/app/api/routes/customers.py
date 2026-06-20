import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_workspace, get_db_session
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.learning_signal import LearningSignal
from app.models.message import Message
from app.models.workspace import Workspace
from app.schemas.conversation import ConversationNextBestActionSchema
from app.schemas.customer import (
    CustomerCrmListProjection,
    CustomerCrmStageSummary,
    CustomerConversation,
    CustomerCreate,
    CustomerDetail,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
)
from app.services.conversation_state import (
    get_customer_conversation_state,
    project_conversation_tail,
    project_crm_stage,
    project_dialog_last_message_text,
    project_next_best_action,
    resolved_pipeline_stage,
)

logger = get_logger("api.customers")

router = APIRouter(prefix="/customers", tags=["customers"])
VALID_CONTACT_TYPES = {"customer", "supplier", "personal", "work", "group"}

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def _get_customer_for_workspace(
    customer_id: int,
    workspace: Workspace,
    session: AsyncSession,
) -> Customer:
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id, Customer.workspace_id == workspace.id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
    return customer


async def _load_latest_preview_by_conversation(
    session: AsyncSession,
    conversation_ids: list[int],
) -> dict[int, tuple[str | None, datetime | None]]:
    if not conversation_ids:
        return {}
    latest_ts = func.coalesce(Message.telegram_timestamp, Message.created_at)
    msg_subq = (
        select(
            Message.conversation_id,
            Message.content,
            latest_ts.label("latest_ts"),
            func.row_number().over(
                partition_by=Message.conversation_id,
                order_by=(latest_ts.desc().nullslast(), Message.id.desc()),
            ).label("rn"),
        )
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.is_deleted.is_(False),
        )
        .subquery()
    )
    rows = (await session.execute(
        select(msg_subq.c.conversation_id, msg_subq.c.content, msg_subq.c.latest_ts)
        .where(msg_subq.c.rn == 1)
    )).all()
    return {
        int(row.conversation_id): ((row.content or "")[:100], row.latest_ts)
        for row in rows
    }


async def _load_unread_by_conversation(
    session: AsyncSession,
    conversation_ids: list[int],
) -> dict[int, int]:
    if not conversation_ids:
        return {}
    rows = (await session.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.is_read.is_(False),
            Message.sender_type == "customer",
        )
        .group_by(Message.conversation_id)
    )).all()
    return {int(conversation_id): int(count or 0) for conversation_id, count in rows}


async def _load_pending_replies_by_conversation(
    session: AsyncSession,
    conversation_ids: list[int],
) -> dict[int, tuple[bool, float | None]]:
    _ = session, conversation_ids
    return {}


def _project_customer_conversation_summary(
    conversation: Conversation,
    latest_previews: dict[int, tuple[str | None, datetime | None]],
) -> str | None:
    local_text, local_at = latest_previews.get(
        conversation.id,
        (conversation.summary, conversation.last_message_at),
    )
    return project_dialog_last_message_text(
        conversation,
        local_text=local_text,
        local_at=local_at,
    )


def _latest_customer_conversation(conversations: list[Conversation]) -> Conversation | None:
    return max(
        conversations,
        key=lambda c: c.last_message_at or c.created_at,
        default=None,
    )


def _conversation_needs_followup(conversation: Conversation) -> bool:
    if conversation.needs_attention:
        return True
    state = get_customer_conversation_state(conversation)
    follow_up = state.follow_up
    if follow_up is None:
        return False
    return (follow_up.status or "").lower() not in {
        "",
        "none",
        "completed",
        "dismissed",
        "cancelled",
        "canceled",
        "resolved",
    }


def _build_next_best_action_payload(conversation: Conversation) -> ConversationNextBestActionSchema:
    state = get_customer_conversation_state(conversation)
    next_best_action = project_next_best_action(
        state,
        needs_attention=bool(conversation.needs_attention),
        override_mode=conversation.override_mode or "auto",
    )
    return ConversationNextBestActionSchema(
        action=next_best_action.action,
        ready=next_best_action.ready,
        reason=next_best_action.reason,
    )


def _build_customer_response(
    customer: Customer,
    conversations: list[Conversation],
    *,
    latest_previews: dict[int, tuple[str | None, datetime | None]],
    unread_by_conversation: dict[int, int],
    reply_by_conversation: dict[int, tuple[bool, float | None]],
) -> CustomerResponse:
    last_conv_at = max(
        (c.last_message_at for c in conversations if c.last_message_at),
        default=None,
    )
    latest_conv = _latest_customer_conversation(conversations)
    latest_tail = None
    next_best_action = None
    if latest_conv is not None:
        local_text, local_at = latest_previews.get(
            latest_conv.id,
            (latest_conv.summary, latest_conv.last_message_at),
        )
        latest_tail = project_conversation_tail(
            latest_conv,
            local_text=local_text,
            local_at=local_at,
            db_unread_count=unread_by_conversation.get(latest_conv.id, 0),
        ).to_payload()
        next_best_action = _build_next_best_action_payload(latest_conv)

    pending_replies = [
        draft
        for conversation in conversations
        if (draft := reply_by_conversation.get(conversation.id)) is not None
    ]
    latest_reply_confidence = next(
        (confidence for has_pending_reply, confidence in pending_replies if has_pending_reply and confidence is not None),
        None,
    )

    return CustomerResponse(
        id=customer.id,
        display_name=customer.display_name,
        phone_number=customer.phone_number,
        contact_type=customer.contact_type,
        classification_confidence=customer.classification_confidence,
        classification_corrected=customer.classification_corrected,
        language=customer.language,
        tags=customer.tags or [],
        lifetime_value=customer.lifetime_value,
        notes=customer.notes,
        ai_brief=customer.ai_brief,
        address=customer.address,
        ai_muted=customer.ai_muted,
        conversation_count=len(conversations),
        last_conversation_at=last_conv_at,
        stage=resolved_pipeline_stage(latest_conv) if latest_conv else None,
        crm_stage=project_crm_stage(latest_conv) if latest_conv else None,
        latest_conversation_id=latest_conv.id if latest_conv else None,
        latest_conversation_tail=latest_tail,
        next_best_action=next_best_action,
        needs_followup=any(_conversation_needs_followup(conv) for conv in conversations),
        has_pending_reply=any(has_pending_reply for has_pending_reply, _ in pending_replies),
        latest_reply_confidence=latest_reply_confidence,
        created_at=customer.created_at,
    )


def _build_customer_crm_summary(customers: list[CustomerResponse]) -> CustomerCrmListProjection:
    stage_counts: dict[str, int] = {}
    for customer in customers:
        stage = customer.crm_stage.stage if customer.crm_stage else "new"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return CustomerCrmListProjection(
        total=len(customers),
        stages=[
            CustomerCrmStageSummary(stage=stage, count=count)
            for stage, count in sorted(stage_counts.items())
        ],
        needs_attention_count=sum(1 for customer in customers if customer.needs_followup),
        pending_reply_count=sum(1 for customer in customers if customer.has_pending_reply),
    )


@router.get("", response_model=CustomerListResponse)
async def list_customers(
    workspace: WorkspaceDep,
    session: SessionDep,
    search: str | None = Query(None, description="Search by name or phone"),
    tag: str | None = Query(None, description="Filter by tag"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base_query = select(Customer).where(Customer.workspace_id == workspace.id)

    if search:
        base_query = base_query.where(
            Customer.display_name.ilike(f"%{search}%")
            | Customer.phone_number.ilike(f"%{search}%")
        )
    if tag:
        # Normalize to lowercase so filter matches tags regardless of how they were saved
        base_query = base_query.where(Customer.tags.contains([tag.lower()]))

    # Total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Avg LTV
    avg_ltv_query = select(func.avg(Customer.lifetime_value)).where(
        Customer.workspace_id == workspace.id
    )
    avg_ltv = (await session.execute(avg_ltv_query)).scalar() or 0.0

    # New this week
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_week_query = select(func.count(Customer.id)).where(
        Customer.workspace_id == workspace.id,
        Customer.created_at >= week_ago,
    )
    new_this_week = (await session.execute(new_week_query)).scalar() or 0

    # Fetch customers
    query = (
        base_query
        .options(selectinload(Customer.conversations))
        .order_by(Customer.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(query)
    customers = result.scalars().all()

    conv_ids = [conv.id for cust in customers for conv in cust.conversations]
    latest_previews = await _load_latest_preview_by_conversation(session, conv_ids)
    unread_by_conversation = await _load_unread_by_conversation(session, conv_ids)
    reply_by_conversation = await _load_pending_replies_by_conversation(session, conv_ids)

    responses = [
        _build_customer_response(
            cust,
            list(cust.conversations),
            latest_previews=latest_previews,
            unread_by_conversation=unread_by_conversation,
            reply_by_conversation=reply_by_conversation,
        )
        for cust in customers
    ]

    return CustomerListResponse(
        customers=responses,
        total=total,
        avg_ltv=round(float(avg_ltv), 2),
        new_this_week=new_this_week,
        crm_summary=_build_customer_crm_summary(responses),
    )


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    data: CustomerCreate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    customer = Customer(
        workspace_id=workspace.id,
        display_name=data.display_name,
        phone_number=data.phone_number,
        language=data.language,
        tags=data.tags,
        notes=data.notes,
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)

    return CustomerResponse(
        id=customer.id,
        display_name=customer.display_name,
        phone_number=customer.phone_number,
        language=customer.language,
        tags=customer.tags or [],
        lifetime_value=customer.lifetime_value,
        notes=customer.notes,
        ai_muted=customer.ai_muted,
        conversation_count=0,
        last_conversation_at=None,
        stage=None,
        crm_stage=None,
        created_at=customer.created_at,
    )


@router.get("/export", response_class=StreamingResponse)
async def export_customers(
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Export all customers as CSV."""
    query = (
        select(Customer)
        .where(Customer.workspace_id == workspace.id)
        .options(selectinload(Customer.conversations))
        .order_by(Customer.created_at.desc())
    )
    result = await session.execute(query)
    customers = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Ism", "Telefon", "Til", "Teglar",
        "LTV", "Eslatma", "Suhbatlar soni", "Yaratilgan",
    ])
    for cust in customers:
        conv_count = len(cust.conversations) if cust.conversations else 0
        writer.writerow([
            cust.id,
            cust.display_name,
            cust.phone_number or "",
            cust.language,
            ", ".join(cust.tags or []),
            cust.lifetime_value,
            cust.notes or "",
            conv_count,
            cust.created_at.strftime("%Y-%m-%d %H:%M") if cust.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=customers.csv"},
    )


@router.get("/{customer_id}", response_model=CustomerDetail)
async def get_customer(
    customer_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    customer = await _get_customer_for_workspace(customer_id, workspace, session)

    # Load conversations
    conv_result = await session.execute(
        select(Conversation)
        .where(Conversation.customer_id == customer.id)
        .order_by(Conversation.last_message_at.desc().nullslast())
    )
    conversations = conv_result.scalars().all()

    # Batch-fetch most active agent + avg confidence per conversation in one query.
    # Use a window function to rank agents within each conversation, then keep rank=1.
    conv_ids = [c.id for c in conversations]
    latest_previews = await _load_latest_preview_by_conversation(session, conv_ids)
    unread_by_conversation = await _load_unread_by_conversation(session, conv_ids)
    reply_by_conversation = await _load_pending_replies_by_conversation(session, conv_ids)

    conv_responses = []
    for conv in conversations:
        conv_responses.append(CustomerConversation(
            id=conv.id,
            pipeline_stage=resolved_pipeline_stage(conv),
            crm_stage=project_crm_stage(conv),
            summary=_project_customer_conversation_summary(conv, latest_previews),
            last_message_at=conv.last_message_at,
            agent_name=None,
            avg_confidence=None,
        ))

    base_response = _build_customer_response(
        customer,
        conversations,
        latest_previews=latest_previews,
        unread_by_conversation=unread_by_conversation,
        reply_by_conversation=reply_by_conversation,
    )

    return CustomerDetail(
        **base_response.model_dump(),
        conversations=conv_responses,
    )


async def _update_customer_projection(
    customer_id: int,
    data: CustomerUpdate,
    workspace: Workspace,
    session: AsyncSession,
) -> CustomerResponse:
    customer = await _get_customer_for_workspace(customer_id, workspace, session)

    update_data = data.model_dump(exclude_unset=True)
    if "contact_type" in update_data and update_data["contact_type"] not in VALID_CONTACT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid contact_type. Must be one of: {', '.join(sorted(VALID_CONTACT_TYPES))}",
        )
    for field, value in update_data.items():
        setattr(customer, field, value)
    if "contact_type" in update_data:
        customer.classification_corrected = True
        customer.classification_confidence = 1.0

    await session.commit()
    await session.refresh(customer, attribute_names=["conversations"])

    convs = customer.conversations
    conv_ids = [conv.id for conv in convs]
    latest_previews = await _load_latest_preview_by_conversation(session, conv_ids)
    unread_by_conversation = await _load_unread_by_conversation(session, conv_ids)
    reply_by_conversation = await _load_pending_replies_by_conversation(session, conv_ids)

    return _build_customer_response(
        customer,
        list(convs),
        latest_previews=latest_previews,
        unread_by_conversation=unread_by_conversation,
        reply_by_conversation=reply_by_conversation,
    )


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    return await _update_customer_projection(customer_id, data, workspace, session)


@router.patch("/{customer_id:int}", response_model=CustomerResponse)
async def patch_customer(
    customer_id: int,
    data: CustomerUpdate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    return await _update_customer_projection(customer_id, data, workspace, session)


# --- Contact classification correction ---


class ClassifyRequest(PydanticBaseModel):
    contact_type: str  # customer, supplier, personal, work, group


@router.patch("/{customer_id}/classify")
async def correct_classification(
    customer_id: int,
    data: ClassifyRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Seller corrects a contact's classification.

    Updates the contact_type, marks as manually corrected,
    and creates a LearningSignal for future model improvement.
    """
    valid_types = {"customer", "supplier", "personal", "work", "group"}
    if data.contact_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid contact_type. Must be one of: {', '.join(valid_types)}",
        )

    customer = await _get_customer_for_workspace(customer_id, workspace, session)

    old_type = customer.contact_type
    customer.contact_type = data.contact_type
    customer.classification_corrected = True
    customer.classification_confidence = 1.0  # Manual = 100% confidence

    # Record the manual correction. Semantic learning/indexing belongs behind
    # Business Brain/OQIM Intelligence runtimes, not route-local embeddings.
    signal = LearningSignal(
        workspace_id=workspace.id,
        signal_type="classification_correction",
        context=f"Customer '{customer.display_name}' (telegram_id={customer.telegram_id})",
        correction=f"Changed from '{old_type}' to '{data.contact_type}'",
        indexing_status="pending",
    )

    session.add(signal)
    await session.commit()

    logger.info(
        "Customer %d classification corrected: %s → %s",
        customer_id, old_type, data.contact_type,
    )

    return {
        "id": customer.id,
        "contact_type": customer.contact_type,
        "classification_corrected": True,
        "learning_signal_created": True,
    }


# --- Batch classification endpoints ---


@router.post("/classify-batch")
async def classify_contacts_batch(workspace: WorkspaceDep, session: SessionDep):
    """Return current contact classifications for review during onboarding."""
    result = await session.execute(
        select(Customer).where(
            Customer.workspace_id == workspace.id,
        ).order_by(Customer.display_name)
    )
    customers = result.scalars().all()

    if not customers:
        return {"items": [], "total": 0}

    items = [
        {
            "customer_id": c.id,
            "name": c.display_name or c.name,
            "suggested_type": c.contact_type or "customer",
            "current_type": c.contact_type,
        }
        for c in customers
    ]
    return {"items": items, "total": len(items)}


class ClassifyConfirmItem(PydanticBaseModel):
    customer_id: int
    confirmed_type: str


class ClassifyConfirmRequest(PydanticBaseModel):
    items: list[ClassifyConfirmItem]


@router.patch("/classify-confirm")
async def classify_confirm(
    data: ClassifyConfirmRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Persist the seller's confirmed contact type corrections from the classify step."""
    updated = 0
    for item in data.items:
        result = await session.execute(
            select(Customer).where(
                Customer.id == item.customer_id,
                Customer.workspace_id == workspace.id,
            )
        )
        customer = result.scalar_one_or_none()
        if customer:
            customer.contact_type = item.confirmed_type
            customer.classification_corrected = True
            updated += 1
    await session.commit()
    return {"updated": updated}
