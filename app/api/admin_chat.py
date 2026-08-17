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
    ConversationCreate, ConversationResponse, MessageCreate, MessageUpdate,
    MessageResponse, PaginatedMessagesResponse, ParticipantProfileResponse,
    AttachmentUploadResponse, PaginatedConversationsResponse
)

router = APIRouter(prefix="/manager/chat", tags=["Manager Chat Management"])


def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
    return current_user


@router.get(
    "/conversations",
    response_model=PaginatedConversationsResponse,
    summary="Admin List All Conversations (Paginated)",
    description="Lists all chat conversations in the system for Admin oversight with pagination (page, limit)."
)
async def list_admin_conversations(
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin List All Conversations Endpoint (Paginated).
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    total_count = await db["conversations"].count_documents({})
    skip = (page - 1) * limit

    cursor = db["conversations"].find().sort("updated_at", -1).skip(skip).limit(limit)
    raw_convs = await cursor.to_list(length=limit)
    convs_res = [format_conversation(c, current_user_id=admin_id) for c in raw_convs]

    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.get(
    "/conversations/clients",
    response_model=PaginatedConversationsResponse,
    summary="Admin List Client Conversations (Image 1 - Paginated)",
    description="Returns all active client conversations matching Image 1 mockup tab with pagination."
)
async def admin_list_client_conversations(
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin List Client Conversations Endpoint (Paginated).
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    client_users = await db["users"].find({"role": "client"}).to_list(length=200)
    client_uids = [str(u.get("_id") or u.get("id")) for u in client_users]

    query = {"participants.user_id": {"$in": client_uids}}
    total_count = await db["conversations"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["conversations"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    convs_res = [format_conversation(c, current_user_id=admin_id) for c in raw_convs]
    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.get(
    "/conversations/employees",
    response_model=PaginatedConversationsResponse,
    summary="Admin List Employee Conversations (Image 2 - Paginated)",
    description="Returns all active worker/employee conversations matching Image 2 mockup tab with pagination."
)
async def admin_list_employee_conversations(
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin List Employee Conversations Endpoint (Paginated).
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    worker_users = await db["users"].find({"role": "worker"}).to_list(length=200)
    worker_uids = [str(u.get("_id") or u.get("id")) for u in worker_users]

    query = {"participants.user_id": {"$in": worker_uids}}
    total_count = await db["conversations"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["conversations"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_convs = await cursor.to_list(length=limit)

    convs_res = [format_conversation(c, current_user_id=admin_id) for c in raw_convs]
    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin Create or Get Conversation",
    description="Creates a new 1-1 or group conversation or returns an existing one."
)
async def create_admin_conversation(
    conv_in: ConversationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Create or Get Conversation Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    target_uids = list(set([admin_id] + conv_in.participant_ids))

    if conv_in.type == "direct" and len(conv_in.participant_ids) == 1:
        other_id = conv_in.participant_ids[0]
        existing = await db["conversations"].find_one({
            "type": "direct",
            "participants.user_id": {"$all": [admin_id, other_id]}
        })
        if existing:
            return format_conversation(existing, current_user_id=admin_id)

    participants = []
    for u_id in target_uids:
        u_query = {"_id": ObjectId(u_id)} if ObjectId.is_valid(u_id) else {"_id": u_id}
        u_doc = await db["users"].find_one(u_query)
        if u_doc:
            participants.append({
                "user_id": str(u_doc.get("_id") or u_doc.get("id")),
                "name": u_doc.get("full_name", "User"),
                "role": u_doc.get("role", "client"),
                "profile_picture": u_doc.get("profile_photo")
            })
        else:
            participants.append({
                "user_id": str(u_id),
                "name": "User",
                "role": "client",
                "profile_picture": None
            })

    conv_id = f"conv_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    doc = {
        "_id": conv_id,
        "id": conv_id,
        "type": conv_in.type,
        "title": conv_in.title or "Conversation",
        "subtitle": conv_in.subtitle,
        "shift_id": conv_in.shift_id,
        "participants": participants,
        "last_message": None,
        "unread_counts": {},
        "created_at": now,
        "updated_at": now
    }

    await db["conversations"].insert_one(doc)
    return format_conversation(doc, current_user_id=admin_id)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Admin Get Conversation Details",
    description="Retrieves single conversation metadata for Admin."
)
async def get_admin_conversation_detail(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Get Conversation Details Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return format_conversation(doc, current_user_id=admin_id)


@router.get(
    "/conversations/{conversation_id}/participant-profile",
    response_model=ParticipantProfileResponse,
    summary="Admin Participant Profile Sidebar",
    description="Returns user profile details for the right-side participant info drawer."
)
async def get_admin_participant_profile(
    conversation_id: str,
    target_user_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Participant Profile Sidebar Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    if not target_user_id:
        conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
        if conv_doc and "participants" in conv_doc:
            other = next((p for p in conv_doc["participants"] if str(p.get("user_id")) != admin_id), None)
            if other:
                target_user_id = str(other.get("user_id"))

    if not target_user_id:
        target_user_id = "u_conv_cli_1"

    u_query = {"$or": [{"_id": ObjectId(target_user_id)}, {"id": target_user_id}]} if ObjectId.is_valid(target_user_id) else {"_id": target_user_id}
    u_doc = await db["users"].find_one(u_query)
    if not u_doc:
        u_doc = {"_id": target_user_id, "full_name": "Sophie van Dijk", "role": "client", "email": "sophie@nhhotels.nl"}

    return await resolve_participant_profile(u_doc, db)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=PaginatedMessagesResponse,
    summary="Admin Get Paginated Messages",
    description="Fetches paginated message history for a conversation for Admin."
)
async def get_admin_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Get Paginated Messages Endpoint.
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
    summary="Admin Send Message",
    description="Sends a text or attachment message in a conversation from Admin."
)
async def send_admin_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Send Message Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    sender_name = getattr(current_user, "full_name", None) or "Admin"

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": admin_id,
        "sender_name": sender_name,
        "sender_role": "admin",
        "sender_avatar": getattr(current_user, "profile_photo", None),
        "content": msg_in.content,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{"user_id": admin_id, "read_at": now}],
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
                "sender_id": admin_id,
                "sender_name": sender_name,
                "timestamp": now.strftime("%I:%M %p")
            },
            "updated_at": now
        }}
    )

    return format_message(msg_doc)


@router.patch(
    "/messages/{message_id}",
    response_model=MessageResponse,
    summary="Admin Edit Message",
    description="Edits the text content of a message sent by Admin."
)
async def edit_admin_message(
    message_id: str,
    msg_in: MessageUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Edit Message Endpoint.
    """
    db = get_database()
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    now = datetime.now(timezone.utc)
    await db["chat_messages"].update_one(
        {"$or": [{"_id": message_id}, {"id": message_id}]},
        {"$set": {"content": msg_in.content, "updated_at": now}}
    )

    updated = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return format_message(updated)


@router.delete(
    "/messages/{message_id}",
    summary="Admin Delete Message",
    description="Deletes a message from a conversation."
)
async def delete_admin_message(
    message_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Delete Message Endpoint.
    """
    db = get_database()
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return {"message": "Message deleted successfully"}


@router.post(
    "/conversations/{conversation_id}/read",
    summary="Admin Mark Messages as Seen",
    description="Marks all messages in the conversation as read/seen for Admin."
)
async def mark_admin_messages_read(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Mark Messages as Read Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)

    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "read_by.user_id": {"$ne": admin_id}},
        {"$push": {"read_by": {"user_id": admin_id, "read_at": now}}}
    )

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {f"unread_counts.{admin_id}": 0}}
    )

    return {"message": "Messages marked as read"}


@router.post(
    "/upload-attachment",
    response_model=AttachmentUploadResponse,
    summary="Admin Upload Chat Attachment",
    description="Uploads an image or file attachment for Admin chat."
)
async def upload_admin_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Upload Chat Attachment Endpoint.
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
