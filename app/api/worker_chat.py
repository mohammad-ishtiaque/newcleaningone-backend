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

router = APIRouter(prefix="/worker/chat", tags=["Worker Chat Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@router.get(
    "/conversations",
    response_model=PaginatedConversationsResponse,
    summary="Worker List Chat Conversations (Paginated)",
    description="Lists all chat conversations accessible to the logged-in worker with pagination (page, limit), including 1-1 chats with Admin and shift group chats."
)
async def list_worker_conversations(
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker List Chat Conversations Endpoint (Paginated).
    Returns paginated 1-1 admin conversations and shift group conversations where worker is assigned.
    """
    db = get_database()
    worker_id = get_user_id(current_user)

    query = {"participants.user_id": worker_id}
    total_count = await db["conversations"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["conversations"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_convs = await cursor.to_list(length=limit)

    convs_res = [format_conversation(c, current_user_id=worker_id) for c in raw_convs]
    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Worker Get Conversation Details",
    description="Retrieves conversation metadata and participant list for worker."
)
async def get_worker_conversation_detail(
    conversation_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Get Conversation Details Endpoint.
    """
    db = get_database()
    worker_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return format_conversation(doc, current_user_id=worker_id)


@router.get(
    "/conversations/{conversation_id}/participant-profile",
    response_model=ParticipantProfileResponse,
    summary="Worker Participant Profile Sidebar",
    description="Returns profile details for the right-side drawer in Worker chat."
)
async def get_worker_participant_profile(
    conversation_id: str,
    target_user_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Participant Profile Sidebar Endpoint.
    """
    db = get_database()
    worker_id = get_user_id(current_user)

    if not target_user_id:
        conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
        if conv_doc and "participants" in conv_doc:
            other = next((p for p in conv_doc["participants"] if str(p.get("user_id")) != worker_id), None)
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
    summary="Worker Get Paginated Messages",
    description="Fetches paginated message history for a conversation for worker."
)
async def get_worker_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Get Paginated Messages Endpoint.
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
    summary="Worker Send Message",
    description="Sends a text or attachment message in a 1-1 or shift group conversation."
)
async def send_worker_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Send Message Endpoint.
    """
    db = get_database()
    worker_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    sender_name = getattr(current_user, "full_name", None) or "Worker"

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": worker_id,
        "sender_name": sender_name,
        "sender_role": "worker",
        "sender_avatar": getattr(current_user, "profile_photo", None),
        "content": msg_in.content,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{"user_id": worker_id, "read_at": now}],
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
                "sender_id": worker_id,
                "sender_name": sender_name,
                "timestamp": now.strftime("%I:%M %p")
            },
            "updated_at": now
        }}
    )

    return format_message(msg_doc)


@router.post(
    "/conversations/{conversation_id}/read",
    summary="Worker Mark Messages as Seen",
    description="Marks all messages in the conversation as read/seen for worker."
)
async def mark_worker_messages_read(
    conversation_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Mark Messages as Read Endpoint.
    """
    db = get_database()
    worker_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)

    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "read_by.user_id": {"$ne": worker_id}},
        {"$push": {"read_by": {"user_id": worker_id, "read_at": now}}}
    )

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {f"unread_counts.{worker_id}": 0}}
    )

    return {"message": "Messages marked as read"}


@router.post(
    "/upload-attachment",
    response_model=AttachmentUploadResponse,
    summary="Worker Upload Chat Attachment",
    description="Uploads an image or file attachment for Worker chat."
)
async def upload_worker_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Upload Chat Attachment Endpoint.
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
