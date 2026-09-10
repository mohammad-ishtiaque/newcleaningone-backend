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
    get_conversation_participants_details, get_or_create_client_admin_conversation,
    mark_conversation_read_shared_management, get_total_unread_count
)
from app.schemas.chat import (
    ConversationResponse, ConversationListItemResponse,
    ConversationDetailResponse, MessageCreate, MessageUpdate,
    MessageResponse, PaginatedMessagesResponse, ParticipantProfileResponse,
    AttachmentUploadResponse, PaginatedConversationsResponse,
    ConversationParticipantsResponse, DeleteConversationResponse
)
from app.schemas.suggested_questions import SuggestedQuestionResponse

router = APIRouter(prefix="/client/chat", tags=["Client Chat Management"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client and current_user.role != "client":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


@router.get(
    "/suggested-questions",
    response_model=List[SuggestedQuestionResponse],
    summary="Client Get Suggested Questions",
    description="""
### Client Get Suggested Questions (Chat Pre-Screen)
Same mechanism as the worker version: manager-curated question/answer pairs shown before a
real chat starts. If none help, the client taps "Direct chat to manager" —
`POST /client/chat/start` (already existing).
"""
)
async def get_client_suggested_questions(current_user: UserInDB = Depends(require_client)):
    db = get_database()
    cursor = db["suggested_questions"].find({
        "is_active": True,
        "target_role": {"$in": ["client", "all"]}
    }).sort("created_at", -1)
    raw = await cursor.to_list(length=200)

    return [
        SuggestedQuestionResponse(
            id=str(d.get("_id") or d.get("id")),
            question=d.get("question", ""),
            answer=d.get("answer", ""),
            target_role=d.get("target_role", "all"),
            is_active=d.get("is_active", True),
            created_by_manager_id=d.get("created_by_manager_id"),
            created_at=d.get("created_at") if isinstance(d.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=d.get("updated_at") if isinstance(d.get("updated_at"), datetime) else datetime.now(timezone.utc)
        )
        for d in raw
    ]


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

    convs_res = [format_conversation_list_item(c, current_user_id=client_id, viewer_role="client") for c in raw_convs]
    has_more = (skip + len(convs_res)) < total_count
    total_unread = await get_total_unread_count(db, client_id)

    return PaginatedConversationsResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        unread_count=total_unread,
        conversations=convs_res
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Create or Get Conversation",
    description="Creates or retrieves the unified direct support conversation with all Admins and Managers. Requires no request body."
)
async def create_client_conversation(
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Create or Get Conversation Endpoint.
    Automatically assigns all active Admins & Managers to the conversation.
    Idempotent: Returns the existing conversation if one already exists.
    Requires no request body.
    """
    db = get_database()
    client_id = get_user_id(current_user)

    doc = await get_or_create_client_admin_conversation(
        db=db,
        client_user=current_user
    )
    return format_conversation(doc, current_user_id=client_id, viewer_role="client")


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Client Get Conversation Details",
    description="Retrieves conversation metadata, participant list, and management seen footprints for the specified conversation ID."
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
    return format_conversation_detail(doc, current_user_id=client_id, viewer_role="client")


@router.delete(
    "/conversations/{conversation_id}",
    response_model=DeleteConversationResponse,
    summary="Client Delete Conversation",
    description="Permanently deletes the conversation and all its messages for every participant (client, admins, and managers)."
)
async def delete_client_conversation(
    conversation_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Delete Conversation Endpoint.
    """
    db = get_database()
    client_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    participant_uids = [str(p.get("user_id")) for p in doc.get("participants", []) if p.get("user_id")]
    if client_id not in participant_uids:
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation")

    await db["conversations"].delete_one({"_id": doc["_id"]})
    await db["chat_messages"].delete_many({"conversation_id": conversation_id})

    try:
        from app.api.chat import ws_manager
        await ws_manager.broadcast_to_users({
            "type": "conversation_deleted",
            "conversation_id": conversation_id,
            "deleted_by": client_id
        }, participant_uids)
    except Exception:
        pass

    return DeleteConversationResponse(
        message="Conversation and all associated messages deleted successfully",
        conversation_id=conversation_id
    )


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
    description="Fetches paginated message history for a conversation including Admin & Manager seen footprints."
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

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    participant_uids = [str(p.get("user_id")) for p in conv_doc.get("participants", []) if p.get("user_id")]
    if client_id not in participant_uids:
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation")

    content_clean = (msg_in.content or "").strip()
    if not content_clean and not msg_in.attachment_url:
        raise HTTPException(status_code=400, detail="Message content or attachment_url is required")

    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"
    sender_name = getattr(current_user, "company_name", None) or getattr(current_user, "full_name", None) or "Client"
    sender_pic = getattr(current_user, "profile_photo", None)

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": str(conv_doc.get("_id") or conv_doc.get("id")),
        "sender_id": client_id,
        "sender_name": sender_name,
        "sender_role": "client",
        "sender_avatar": sender_pic,
        "content": content_clean,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{
            "user_id": client_id,
            "name": sender_name,
            "role": "client",
            "profile_picture": sender_pic,
            "read_at": now
        }],
        "created_at": now,
        "updated_at": now
    }

    await db["chat_messages"].insert_one(msg_doc)

    last_text = content_clean if content_clean else "[Attachment]"
    other_uids = [uid for uid in participant_uids if uid != client_id]
    inc_unreads = {f"unread_counts.{uid}": 1 for uid in other_uids}

    update_payload = {
        "$set": {
            "last_message": {
                "text": last_text,
                "sender_id": client_id,
                "sender_name": sender_name,
                "timestamp": now.strftime("%I:%M %p")
            },
            "updated_at": now
        }
    }
    if inc_unreads:
        update_payload["$inc"] = inc_unreads

    await db["conversations"].update_one(
        {"_id": conv_doc["_id"]},
        update_payload
    )

    from app.services.chat_ws_service import broadcast_new_message
    await broadcast_new_message(db, str(conv_doc["_id"]), msg_doc)

    from app.services.chat_service import notify_new_chat_message
    await notify_new_chat_message(
        db, str(conv_doc["_id"]), client_id, sender_name, last_text, other_uids
    )

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

    content_clean = (msg_in.content or "").strip()
    if not content_clean:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    now = datetime.now(timezone.utc)
    await db["chat_messages"].update_one(
        {"$or": [{"_id": message_id}, {"id": message_id}]},
        {"$set": {"content": content_clean, "updated_at": now}}
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
    return await mark_conversation_read_shared_management(db, conversation_id, current_user)


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


@router.post(
    "/start",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Cold Start Support Conversation",
    description="Initiates or returns the active support conversation with managers for cold-start chat."
)
async def start_client_conversation(current_user: UserInDB = Depends(require_client)):
    db = get_database()
    doc = await get_or_create_client_admin_conversation(db, current_user)
    client_id = get_user_id(current_user)
    return format_conversation(doc, current_user_id=client_id, viewer_role="client")


from fastapi import WebSocket

@router.websocket("/ws/{user_id}")
async def client_chat_ws(websocket: WebSocket, user_id: str):
    from app.api.chat import ws_manager
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
    finally:
        ws_manager.disconnect(user_id, websocket)
