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
    get_user_id, format_conversation_list_item, format_conversation_detail,
    format_conversation, format_message, resolve_participant_profile,
    get_conversation_participants_details
)
from app.schemas.chat import (
    ConversationCreate, ConversationResponse, ConversationListItemResponse,
    ConversationDetailResponse, MessageCreate, MessageUpdate,
    MessageResponse, PaginatedMessagesResponse, ParticipantProfileResponse,
    AttachmentUploadResponse, PaginatedConversationsResponse,
    ConversationParticipantsResponse
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

    convs_res = [format_conversation_list_item(c, current_user_id=client_id) for c in raw_convs]
    return PaginatedConversationsResponse(total_count=total_count, page=page, limit=limit, conversations=convs_res)


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Create or Get Conversation",
    description="Creates a new direct or group conversation for client or returns an existing direct conversation."
)
async def create_client_conversation(
    conv_in: ConversationCreate,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Create or Get Conversation Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)

    incoming_p_ids = list(conv_in.participant_ids or [])
    if conv_in.target_user_id and conv_in.target_user_id not in incoming_p_ids:
        incoming_p_ids.append(conv_in.target_user_id)

    target_uids = list(set([client_id] + incoming_p_ids))

    if conv_in.type == "direct" and len(incoming_p_ids) == 1:
        other_id = incoming_p_ids[0]
        existing = await db["conversations"].find_one({
            "type": "direct",
            "participants.user_id": {"$all": [client_id, other_id]}
        })
        if existing:
            return format_conversation(existing, current_user_id=client_id)

    participants = []
    for u_id in target_uids:
        u_query = {"$or": [{"_id": ObjectId(u_id)}, {"id": u_id}, {"_id": u_id}]} if ObjectId.is_valid(u_id) else {"$or": [{"_id": u_id}, {"id": u_id}]}
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
        "cleaning_plan_id": conv_in.cleaning_plan_id,
        "participants": participants,
        "last_message": None,
        "unread_counts": {},
        "created_at": now,
        "updated_at": now
    }

    await db["conversations"].insert_one(doc)
    return format_conversation(doc, current_user_id=client_id)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
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
    return format_conversation_detail(doc, current_user_id=client_id)


@router.get(
    "/conversations/{conversation_id}/participants",
    response_model=ConversationParticipantsResponse,
    summary="Client Get Conversation Participants",
    description="Returns the full list and profile details of all participants in the conversation for client."
)
async def get_client_conversation_participants(
    conversation_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Get Conversation Participants Endpoint.
    """
    db = get_database()
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await get_conversation_participants_details(db, doc)


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

    from app.services.chat_ws_service import (
        broadcast_new_message, broadcast_message_edited,
        broadcast_message_deleted, broadcast_messages_read
    )
    await broadcast_new_message(db, conversation_id, msg_doc)

    return format_message(msg_doc)


@router.patch(
    "/messages/{message_id}",
    response_model=MessageResponse,
    summary="Client Edit Message",
    description="Edits the text content of a message sent by the client."
)
async def edit_client_message(
    message_id: str,
    msg_in: MessageUpdate,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Edit Message Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")
    if str(msg_doc.get("sender_id")) != client_id:
        raise HTTPException(status_code=403, detail="Cannot edit messages sent by another user")

    now = datetime.now(timezone.utc)
    await db["chat_messages"].update_one(
        {"$or": [{"_id": message_id}, {"id": message_id}]},
        {"$set": {"content": msg_in.content, "updated_at": now}}
    )

    updated = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    c_id = updated.get("conversation_id")
    if c_id:
        from app.services.chat_ws_service import broadcast_message_edited
        await broadcast_message_edited(db, c_id, updated)

    return format_message(updated)


@router.delete(
    "/messages/{message_id}",
    summary="Client Delete Message",
    description="Deletes a message sent by the client."
)
async def delete_client_message(
    message_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Delete Message Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")
    if str(msg_doc.get("sender_id")) != client_id:
        raise HTTPException(status_code=403, detail="Cannot delete messages sent by another user")

    c_id = msg_doc.get("conversation_id")
    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})

    if c_id:
        from app.services.chat_ws_service import broadcast_message_deleted
        await broadcast_message_deleted(db, c_id, message_id)

    return {"message": "Message deleted successfully"}


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

    from app.services.chat_ws_service import broadcast_messages_read
    await broadcast_messages_read(db, conversation_id, client_id, now.isoformat())

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
