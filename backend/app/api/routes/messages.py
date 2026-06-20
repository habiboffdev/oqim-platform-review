"""Send messages via the unified DeliveryService."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session, get_delivery_service
from app.models.conversation import Conversation
from app.models.workspace import Workspace
from app.services.delivery import DeliveryService

router = APIRouter(prefix="/messages", tags=["messages"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
DeliveryDep = Annotated[DeliveryService, Depends(get_delivery_service)]


class SendMessageRequest(BaseModel):
    conversation_id: int
    text: str


class SendMessageResponse(BaseModel):
    external_message_id: str
    channel: str


@router.post("/send", response_model=SendMessageResponse)
async def send_message(
    body: SendMessageRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
    delivery: DeliveryDep,
):
    row = await session.execute(
        select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.workspace_id == workspace.id,
        )
    )
    conv = row.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await delivery.deliver_message(
        conv.id, body.text, db=session, workspace_id=workspace.id,
    )
    if not result.success:
        raise HTTPException(status_code=503, detail="Message delivery temporarily unavailable")

    return SendMessageResponse(
        external_message_id=result.external_message_id or "",
        channel="telegram_dm",
    )
