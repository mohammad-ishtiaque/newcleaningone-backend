import uuid
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.chat_service import (
    get_user_id, format_conversation, format_message, resolve_participant_profile
)
from app.schemas.chat import (
    ConversationResponse, MessageCreate, MessageResponse, PaginatedMessagesResponse,
    ParticipantProfileResponse, AttachmentUploadResponse, PaginatedConversationsResponse
)

router = APIRouter(prefix="/client/chat", tags=["Client Chat Management"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


@router.get(
    "/conversations",
    response_model=PaginatedConversationsResponse,
    summary="Client List Chat Conversations (Paginated)",
    description="Lists all chat conversations accessible to the logged-in client with pagination (page, limit), including 1-1 chats with Admin and shift group chats."
)
async def list_client_conversations(
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client List Chat Conversations Endpoint (Paginated).
    Returns paginated 1-1 admin conversations and shift group conversations where client is a participant.
    """
    db = get_database()
    client_id = get_user_id(current_user)

    query = {"participants.user_id": client_id}
    total_count = await db["conversations"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["conversations"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_convs = await cursor.to_list(length=limit)

    if not raw_convs and page == 1:
        now = datetime.now(timezone.utc)
        mock_convs = [
            ConversationResponse(
                id="conv_cli_1",
                type="direct",
                title="Cleaning One Admin",
                subtitle="Support & Management",
                shift_id=None,
                participants=[
                    {"user_id": "admin_1", "name": "Admin", "role": "admin"},
                    {"user_id": client_id, "name": getattr(current_user, "full_name", "Client"), "role": "client"}
                ],
                last_message={"text": "Can we add an extra window-cleaning service next week?", "sender_id": client_id, "sender_name": getattr(current_user, "full_name", "Client"), "timestamp": "09:08"},
                unread_count=0,
                created_at=now,
                updated_at=now
            )
        ]
        return PaginatedConversationsResponse(total_count=1, page=1, limit=limit, conversations=mock_convs)

    convs_res = [format_conversation(c, current_user_id=client_id) for c in raw_convs]
    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Client Get Conversation Details",
    description="Retrieves conversation metadata and participant list for the specified conversation ID."
)
async def get_client_conversation_detail(
    conversation_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Get Conversation Details Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return format_conversation(doc, current_user_id=client_id)


@router.get(
    "/conversations/{conversation_id}/participant-profile",
    response_model=ParticipantProfileResponse,
    summary="Client Get Participant Profile Sidebar",
    description="Returns user profile details for the right-side participant info drawer."
)
async def get_client_participant_profile(
    conversation_id: str,
    target_user_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Get Participant Profile Sidebar Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)

    if not target_user_id:
        conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
        if conv_doc and "participants" in conv_doc:
            other = next((p for p in conv_doc["participants"] if str(p.get("user_id")) != client_id), None)
            if other:
                target_user_id = str(other.get("user_id"))

    if not target_user_id:
        target_user_id = "admin_1"

    u_query = {"$or": [{"_id": ObjectId(target_user_id)}, {"id": target_user_id}]} if ObjectId.is_valid(target_user_id) else {"_id": target_user_id}
    u_doc = await db["users"].find_one(u_query)
    if not u_doc:
        u_doc = {"_id": target_user_id, "full_name": "Admin Support", "role": "admin", "email": "admin@cleanones.nl"}

    return await resolve_participant_profile(u_doc, db)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=PaginatedMessagesResponse,
    summary="Client Get Paginated Messages",
    description="Fetches paginated message history for a conversation and marks unread messages as delivered."
)
async def get_client_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Get Paginated Messages Endpoint.
    """
    db = get_database()
    query = {"conversation_id": conversation_id}

    total_count = await db["chat_messages"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["chat_messages"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_msgs = await cursor.to_list(length=limit)
    raw_msgs.reverse()

    return PaginatedMessagesResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        messages=[format_message(m) for m in raw_msgs]
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Send Message",
    description="Sends a text or attachment message in a 1-1 or shift group conversation."
)
async def send_client_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Send Message Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    sender_name = getattr(current_user, "full_name", None) or "Client"

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": client_id,
        "sender_name": sender_name,
        "sender_role": "client",
        "sender_avatar": getattr(current_user, "profile_photo", None),
        "content": msg_in.content,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{"user_id": client_id, "read_at": now}],
        "created_at": now,
        "updated_at": now
    }

    await db["chat_messages"].insert_one(msg_doc)

    last_text = msg_in.content if msg_in.content else "[Attachment]"
    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {
            "last_message": {
                "text": last_text,
                "sender_id": client_id,
                "sender_name": sender_name,
                "timestamp": now.strftime("%I:%M %p")
            },
            "updated_at": now
        }}
    )

    return format_message(msg_doc)


@router.post(
    "/conversations/{conversation_id}/read",
    summary="Client Mark Messages as Seen",
    description="Marks all messages in the conversation as read/seen for the logged-in client."
)
async def mark_client_messages_read(
    conversation_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Mark Messages as Read Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)

    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "read_by.user_id": {"$ne": client_id}},
        {"$push": {"read_by": {"user_id": client_id, "read_at": now}}}
    )

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {f"unread_counts.{client_id}": 0}}
    )

    return {"message": "Messages marked as read"}


@router.post(
    "/upload-attachment",
    response_model=AttachmentUploadResponse,
    summary="Client Upload Chat Attachment",
    description="Uploads an image or document attachment to include in a chat message."
)
async def upload_client_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Upload Chat Attachment Endpoint.
    """
    os.makedirs("uploads/chat", exist_ok=True)
    file_ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    filename = f"chat_{uuid.uuid4().hex[:10]}.{file_ext}"
    file_path = os.path.join("uploads/chat", filename)

    contents = await file.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    file_url = f"/uploads/chat/{filename}"
    att_type = "image" if file_ext.lower() in ["jpg", "jpeg", "png", "webp", "gif"] else "file"

    return AttachmentUploadResponse(
        attachment_url=file_url,
        attachment_type=att_type,
        file_name=file.filename or filename
    )
