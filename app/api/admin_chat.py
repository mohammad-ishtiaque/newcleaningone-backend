import uuid
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Query
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.chat_service import (
    get_user_id, format_conversation_list_item, format_conversation_detail,
    format_conversation, format_message, resolve_participant_profile,
    get_conversation_participants_details, add_conversation_participants,
    remove_conversation_participant
)
from app.schemas.chat import (
    ConversationCreate, ConversationResponse, ConversationListItemResponse,
    ConversationDetailResponse, ConversationUpdate, DeleteConversationResponse,
    MessageCreate, MessageUpdate, MessageResponse, PaginatedMessagesResponse,
    ParticipantProfileResponse, AttachmentUploadResponse,
    PaginatedConversationsResponse, ConversationParticipantsResponse,
    AddParticipantsRequest, RemoveParticipantResponse
)

base_chat_router = APIRouter()


def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager or Admin role required")
    return current_user


@base_chat_router.get(
    "/conversations",
    response_model=PaginatedConversationsResponse,
    summary="List All Conversations (Paginated)",
    description="Lists all chat conversations (Direct clients, direct workers, and Group) for Admin and Manager oversight with pagination, type filtering ('all', 'direct worker', 'Direct clients', 'Group'), and search."
)
async def list_admin_conversations(
    type: Optional[str] = Query(None, description="Optional filter by conversation type: 'all', 'direct worker', 'Direct clients', 'Group'"),
    search: Optional[str] = Query(None, description="Search by conversation title or participant name"),
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    List All Conversations Endpoint (Paginated).
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    query = {}

    # 1. Type filtering
    if type and str(type).strip().lower() not in ["all", "", "none"]:
        t_clean = str(type).strip().lower()
        if t_clean in ["group", "cleaning_plan_group"]:
            query["$or"] = [
                {"type": "group"},
                {"shift_id": {"$exists": True, "$ne": None, "$ne": ""}},
                {"cleaning_plan_id": {"$exists": True, "$ne": None, "$ne": ""}},
                {"participants.2": {"$exists": True}}
            ]
        elif t_clean in ["direct clients", "direct client", "direct_client", "client", "clients"]:
            client_users = await db["users"].find({"role": "client"}, {"_id": 1, "id": 1}).to_list(length=500)
            client_uids = [str(u.get("_id") or u.get("id")) for u in client_users]
            query["$and"] = [
                {"type": {"$nin": ["group", "cleaning_plan_group"]}},
                {"$or": [{"cleaning_plan_id": None}, {"cleaning_plan_id": ""}, {"cleaning_plan_id": {"$exists": False}}]},
                {"$or": [{"shift_id": None}, {"shift_id": ""}, {"shift_id": {"$exists": False}}]},
                {"participants.2": {"$exists": False}},
                {"participants.user_id": {"$in": client_uids}}
            ]
        elif t_clean in ["direct worker", "direct_worker", "worker", "workers", "employee", "employees"]:
            worker_users = await db["users"].find({"role": "worker"}, {"_id": 1, "id": 1}).to_list(length=500)
            worker_uids = [str(u.get("_id") or u.get("id")) for u in worker_users]
            query["$and"] = [
                {"type": {"$nin": ["group", "cleaning_plan_group"]}},
                {"$or": [{"cleaning_plan_id": None}, {"cleaning_plan_id": ""}, {"cleaning_plan_id": {"$exists": False}}]},
                {"$or": [{"shift_id": None}, {"shift_id": ""}, {"shift_id": {"$exists": False}}]},
                {"participants.2": {"$exists": False}},
                {"participants.user_id": {"$in": worker_uids}}
            ]

    # 2. Search filtering
    if search and str(search).strip():
        s_regex = {"$regex": str(search).strip(), "$options": "i"}
        search_or = [
            {"title": s_regex},
            {"participants.name": s_regex},
            {"last_message.text": s_regex}
        ]
        if "$and" in query:
            query["$and"].append({"$or": search_or})
        elif "$or" in query:
            query = {"$and": [{"$or": query.pop("$or")}, {"$or": search_or}]}
        else:
            query["$or"] = search_or

    total_count = await db["conversations"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["conversations"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_convs = await cursor.to_list(length=limit)
    convs_res = [format_conversation_list_item(c, current_user_id=admin_id) for c in raw_convs]

    has_more = (skip + len(convs_res)) < total_count

    return PaginatedConversationsResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        conversations=convs_res
    )


@base_chat_router.get("/conversations/clients", response_model=PaginatedConversationsResponse, include_in_schema=False)
async def admin_list_client_conversations(page: int = 1, limit: int = 20, current_user: UserInDB = Depends(require_manager)):
    return await list_admin_conversations(type="Direct clients", search=None, page=page, limit=limit, current_user=current_user)

@base_chat_router.get("/conversations/employees", response_model=PaginatedConversationsResponse, include_in_schema=False)
async def admin_list_employee_conversations(page: int = 1, limit: int = 20, current_user: UserInDB = Depends(require_manager)):
    return await list_admin_conversations(type="direct worker", search=None, page=page, limit=limit, current_user=current_user)


@base_chat_router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Get Conversation",
    description="Creates a new 1-1 or group conversation or returns an existing one."
)
async def create_admin_conversation(
    conv_in: ConversationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Create or Get Conversation Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)

    incoming_p_ids = list(conv_in.participant_ids or [])
    if conv_in.target_user_id and conv_in.target_user_id not in incoming_p_ids:
        incoming_p_ids.append(conv_in.target_user_id)

    target_uids = list(set([admin_id] + incoming_p_ids))

    if conv_in.type == "direct" and len(incoming_p_ids) == 1:
        other_id = incoming_p_ids[0]
        existing = await db["conversations"].find_one({
            "type": "direct",
            "participants.user_id": {"$all": [admin_id, other_id]}
        })
        if existing:
            return format_conversation(existing, current_user_id=admin_id)

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

    c_title = conv_in.title
    if not c_title and conv_in.cleaning_plan_id:
        p_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": conv_in.cleaning_plan_id}, {"id": conv_in.cleaning_plan_id}]})
        if p_doc:
            c_title = p_doc.get("title") or p_doc.get("plan_name") or p_doc.get("name")

    conv_id = f"conv_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    doc = {
        "_id": conv_id,
        "id": conv_id,
        "type": conv_in.type,
        "title": c_title or "Conversation",
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
    return format_conversation(doc, current_user_id=admin_id)


@base_chat_router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get Conversation Details",
    description="Retrieves single conversation metadata for Admin and Manager with full participant details."
)
async def get_admin_conversation_detail(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Get Conversation Details Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return format_conversation_detail(doc, current_user_id=admin_id)


@base_chat_router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Edit Conversation",
    description="Updates conversation title, subtitle, or avatar. Restricted to Manager and Admin."
)
async def update_admin_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Edit Conversation Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    update_fields = {}
    if payload.title is not None and payload.title.strip():
        update_fields["title"] = payload.title.strip()
    if payload.subtitle is not None:
        update_fields["subtitle"] = payload.subtitle.strip()
    if payload.avatar_url is not None:
        update_fields["avatar_url"] = payload.avatar_url.strip()

    if update_fields:
        now = datetime.now(timezone.utc)
        update_fields["updated_at"] = now
        await db["conversations"].update_one(
            {"_id": doc["_id"]},
            {"$set": update_fields}
        )
        doc = await db["conversations"].find_one({"_id": doc["_id"]})

        try:
            from app.api.chat import ws_manager
            all_uids = [str(p.get("user_id")) for p in doc.get("participants", [])]
            await ws_manager.broadcast_to_users({
                "type": "conversation_updated",
                "conversation_id": conversation_id,
                "title": doc.get("title"),
                "subtitle": doc.get("subtitle"),
                "avatar_url": doc.get("avatar_url"),
                "updated_at": now.isoformat()
            }, all_uids)
        except Exception:
            pass

    return format_conversation_detail(doc, current_user_id=admin_id)


@base_chat_router.delete(
    "/conversations/{conversation_id}",
    response_model=DeleteConversationResponse,
    summary="Delete Conversation",
    description="Deletes conversation and associated messages from worker, client, and manager sides. Restricted to Manager and Admin."
)
async def delete_admin_conversation(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Delete Conversation Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    all_uids = [str(p.get("user_id")) for p in doc.get("participants", [])]

    await db["conversations"].delete_one({"_id": doc["_id"]})
    await db["chat_messages"].delete_many({"conversation_id": conversation_id})

    try:
        from app.api.chat import ws_manager
        await ws_manager.broadcast_to_users({
            "type": "conversation_deleted",
            "conversation_id": conversation_id,
            "deleted_by": admin_id
        }, all_uids)
    except Exception:
        pass

    return DeleteConversationResponse(
        message="Conversation and all associated messages deleted successfully",
        conversation_id=conversation_id
    )


@base_chat_router.get(
    "/conversations/{conversation_id}/participants",
    response_model=ConversationParticipantsResponse,
    summary="Get Conversation Participants",
    description="Returns the full list and profile details of all participants in the conversation."
)
async def get_admin_conversation_participants(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Get Conversation Participants Endpoint.
    """
    db = get_database()
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await get_conversation_participants_details(db, doc)


@base_chat_router.post(
    "/conversations/{conversation_id}/participants",
    response_model=ConversationParticipantsResponse,
    status_code=status.HTTP_200_OK,
    summary="Add Clients or Workers to Conversation",
    description="Adds one or more clients, workers, or managers as participants to a group conversation."
)
async def add_chat_participants_endpoint(
    conversation_id: str,
    payload: AddParticipantsRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Add Clients or Workers to Conversation Endpoint.
    """
    db = get_database()
    return await add_conversation_participants(
        db=db,
        conversation_id=conversation_id,
        user_ids=payload.user_ids,
        actor_user=current_user
    )


@base_chat_router.delete(
    "/conversations/{conversation_id}/participants/{user_id}",
    response_model=RemoveParticipantResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Client or Worker from Conversation",
    description="Removes a specific client, worker, or participant from a group conversation."
)
async def remove_chat_participant_endpoint(
    conversation_id: str,
    user_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Remove Client or Worker from Conversation Endpoint.
    """
    db = get_database()
    result = await remove_conversation_participant(
        db=db,
        conversation_id=conversation_id,
        target_user_id=user_id,
        actor_user=current_user
    )
    return RemoveParticipantResponse(**result)


@base_chat_router.get(
    "/conversations/{conversation_id}/participant-profile",
    response_model=ParticipantProfileResponse,
    summary="Participant Profile Sidebar",
    description="Returns user profile details for the right-side participant info drawer."
)
async def get_admin_participant_profile(
    conversation_id: str,
    target_user_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Participant Profile Sidebar Endpoint.
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


@base_chat_router.get(
    "/conversations/{conversation_id}/messages",
    response_model=PaginatedMessagesResponse,
    summary="Get Paginated Messages",
    description="Fetches paginated message history for a conversation."
)
async def get_admin_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Get Paginated Messages Endpoint.
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


@base_chat_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send Message",
    description="Sends a text or attachment message in a conversation."
)
async def send_admin_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Send Message Endpoint.
    """
    db = get_database()
    admin_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    sender_name = getattr(current_user, "full_name", None) or "Admin"
    raw_role = getattr(current_user, "role", "manager")
    sender_role = getattr(raw_role, "value", str(raw_role)).lower()

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": admin_id,
        "sender_name": sender_name,
        "sender_role": sender_role,
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

    from app.services.chat_ws_service import (
        broadcast_new_message, broadcast_message_edited,
        broadcast_message_deleted, broadcast_messages_read
    )
    await broadcast_new_message(db, conversation_id, msg_doc)

    return format_message(msg_doc)


@base_chat_router.patch(
    "/messages/{message_id}",
    response_model=MessageResponse,
    summary="Edit Message",
    description="Edits the text content of a message sent by Admin or Manager."
)
async def edit_admin_message(
    message_id: str,
    msg_in: MessageUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Edit Message Endpoint.
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
    c_id = updated.get("conversation_id")
    if c_id:
        from app.services.chat_ws_service import broadcast_message_edited
        await broadcast_message_edited(db, c_id, updated)

    return format_message(updated)


@base_chat_router.delete(
    "/messages/{message_id}",
    summary="Delete Message",
    description="Deletes a message from a conversation."
)
async def delete_admin_message(
    message_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Delete Message Endpoint.
    """
    db = get_database()
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    c_id = msg_doc.get("conversation_id")
    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})

    if c_id:
        from app.services.chat_ws_service import broadcast_message_deleted
        await broadcast_message_deleted(db, c_id, message_id)

    return {"message": "Message deleted successfully"}


@base_chat_router.post(
    "/conversations/{conversation_id}/read",
    summary="Mark Messages as Seen",
    description="Marks all messages in the conversation as read/seen for Admin or Manager."
)
async def mark_admin_messages_read(
    conversation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Mark Messages as Read Endpoint.
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

    from app.services.chat_ws_service import broadcast_messages_read
    await broadcast_messages_read(db, conversation_id, admin_id, now.isoformat())

    return {"message": "Messages marked as read"}


@base_chat_router.post(
    "/upload-attachment",
    response_model=AttachmentUploadResponse,
    summary="Upload Chat Attachment",
    description="Uploads an image or file attachment for chat."
)
async def upload_admin_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    """
    Upload Chat Attachment Endpoint.
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


# Manager Chat Router (/manager/chat)
manager_chat_router = APIRouter(prefix="/manager/chat", tags=["Manager Chat Management"])
manager_chat_router.include_router(base_chat_router)

# Admin Chat Router (/admin/chat)
admin_chat_router = APIRouter(prefix="/admin/chat", tags=["Admin Chat Management"])
admin_chat_router.include_router(base_chat_router)

# Default alias for backwards compatibility
router = manager_chat_router

