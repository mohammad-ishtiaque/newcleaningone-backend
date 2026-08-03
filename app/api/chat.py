import uuid
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from typing import Optional, List, Dict
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.chat_service import (
    get_user_id, format_conversation, format_message, resolve_participant_profile,
    sync_shift_group_conversation
)
from app.schemas.chat import (
    ConversationCreate, ConversationResponse, MessageCreate, MessageUpdate,
    MessageResponse, PaginatedMessagesResponse, ParticipantProfileResponse,
    AttachmentUploadResponse
)

router = APIRouter(prefix="/chat", include_in_schema=False)


# WebSocket Connection Manager for Real-time Messaging
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_personal_message(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

    async def broadcast_to_users(self, message: dict, user_ids: List[str]):
        for u_id in user_ids:
            await self.send_personal_message(message, str(u_id))

ws_manager = ConnectionManager()


@router.get("/conversations", response_model=List[ConversationResponse], summary="List Active Conversations")
async def list_user_conversations(
    category: Optional[str] = None,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    List Active Conversations Endpoint.
    Returns all active conversations for the authenticated user.
    """
    db = get_database()
    user_id = get_user_id(current_user)

    if current_user.role == RoleEnum.admin:
        query = {}
        if category in ["clients", "client"]:
            client_users = await db["users"].find({"role": "client"}).to_list(length=200)
            client_uids = [str(u.get("_id") or u.get("id")) for u in client_users]
            query = {"participants.user_id": {"$in": client_uids}}
        elif category in ["employees", "workers", "employee", "worker"]:
            worker_users = await db["users"].find({"role": "worker"}).to_list(length=200)
            worker_uids = [str(u.get("_id") or u.get("id")) for u in worker_users]
            query = {"participants.user_id": {"$in": worker_uids}}
    else:
        query = {"participants.user_id": user_id}

    cursor = db["conversations"].find(query).sort("updated_at", -1)
    raw_convs = await cursor.to_list(length=100)

    return [format_conversation(c, current_user_id=user_id) for c in raw_convs]


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED, summary="Create or Get Conversation")
async def create_or_get_conversation(
    conv_in: ConversationCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Create or Get Conversation Endpoint.
    Supports creating 1-1 direct chats or shift group chats.
    """
    db = get_database()
    user_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)

    if conv_in.type == "direct":
        target_id = conv_in.target_user_id
        if not target_id:
            admin_doc = await db["users"].find_one({"role": "admin"})
            target_id = str(admin_doc.get("_id") or admin_doc.get("id")) if admin_doc else "admin_default"

        existing = await db["conversations"].find_one({
            "type": "direct",
            "participants.user_id": {"$all": [user_id, target_id]}
        })
        if existing:
            return format_conversation(existing, current_user_id=user_id)

        target_query = {"$or": [{"_id": target_id}, {"id": target_id}]}
        if ObjectId.is_valid(target_id):
            target_query["$or"].append({"_id": ObjectId(target_id)})

        target_user = await db["users"].find_one(target_query)
        target_name = target_user.get("full_name", "User") if target_user else "User"
        target_role = target_user.get("role", "admin") if target_user else "admin"

        conv_id = f"conv_dir_{uuid.uuid4().hex[:8]}"
        doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "direct",
            "title": conv_in.title or target_name,
            "subtitle": conv_in.subtitle or "Private conversation",
            "shift_id": None,
            "participants": [
                {"user_id": user_id, "name": getattr(current_user, "full_name", "User"), "role": str(current_user.role)},
                {"user_id": target_id, "name": target_name, "role": target_role}
            ],
            "last_message": None,
            "unread_counts": {user_id: 0, target_id: 0},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(doc)
        return format_conversation(doc, current_user_id=user_id)
    else:
        shift_id = conv_in.shift_id
        if not shift_id:
            raise HTTPException(status_code=400, detail="shift_id is required for group conversations")
        shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
        if not shift_doc:
            raise HTTPException(status_code=404, detail="Shift not found for group conversation")
        doc = await sync_shift_group_conversation(db, shift_doc)
        return format_conversation(doc, current_user_id=user_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse, summary="Get Conversation Details")
async def get_conversation_detail(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Get Conversation Details Endpoint.
    """
    db = get_database()
    user_id = get_user_id(current_user)
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return format_conversation(doc, current_user_id=user_id)


@router.get("/conversations/{conversation_id}/participant-profile", response_model=ParticipantProfileResponse, summary="Participant Profile Sidebar")
async def get_participant_profile(
    conversation_id: str,
    target_user_id: Optional[str] = None,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Participant Profile Sidebar Endpoint.
    """
    db = get_database()
    user_id = get_user_id(current_user)

    if not target_user_id:
        conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
        if conv_doc and "participants" in conv_doc:
            other = next((p for p in conv_doc["participants"] if str(p.get("user_id")) != user_id), None)
            if other:
                target_user_id = str(other.get("user_id"))

    if not target_user_id:
        target_user_id = "admin_1"

    u_query = {"$or": [{"_id": ObjectId(target_user_id)}, {"id": target_user_id}]} if ObjectId.is_valid(target_user_id) else {"_id": target_user_id}
    u_doc = await db["users"].find_one(u_query)
    if not u_doc:
        u_doc = {"_id": target_user_id, "full_name": "Support User", "role": "admin"}

    return await resolve_participant_profile(u_doc, db)


@router.post("/upload-attachment", response_model=AttachmentUploadResponse, summary="Upload Chat Image Attachment")
async def upload_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Upload Chat Image Attachment Endpoint.
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


@router.get("/conversations/{conversation_id}/messages", response_model=PaginatedMessagesResponse, summary="Get Paginated Messages")
async def get_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(get_current_user)
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


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED, summary="Send Message")
async def send_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Send Message Endpoint.
    """
    db = get_database()
    user_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    sender_name = getattr(current_user, "full_name", None) or "User"

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": user_id,
        "sender_name": sender_name,
        "sender_role": str(current_user.role),
        "sender_avatar": getattr(current_user, "profile_photo", None),
        "content": msg_in.content,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{"user_id": user_id, "read_at": now}],
        "created_at": now,
        "updated_at": now
    }

    await db["chat_messages"].insert_one(msg_doc)

    last_text = msg_in.content if msg_in.content else "[Attachment]"
    conv = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if conv:
        await db["conversations"].update_one(
            {"_id": conv["_id"]},
            {"$set": {
                "last_message": {
                    "text": last_text,
                    "sender_id": user_id,
                    "sender_name": sender_name,
                    "timestamp": now.strftime("%I:%M %p")
                },
                "updated_at": now
            }}
        )
        participant_ids = [str(p.get("user_id")) for p in conv.get("participants", []) if str(p.get("user_id")) != user_id]
        formatted_msg = format_message(msg_doc)
        await ws_manager.broadcast_to_users(formatted_msg.model_dump(mode="json"), participant_ids)

    return format_message(msg_doc)


@router.patch("/messages/{message_id}", response_model=MessageResponse, summary="Edit Message")
async def edit_message(
    message_id: str,
    msg_in: MessageUpdate,
    current_user: UserInDB = Depends(get_current_user)
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
    return format_message(updated)


@router.delete("/messages/{message_id}", summary="Delete Message")
async def delete_message(
    message_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Delete Message Endpoint.
    """
    db = get_database()
    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return {"message": "Message deleted successfully"}


@router.post("/conversations/{conversation_id}/read", summary="Mark Messages as Seen")
async def mark_messages_read(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Mark Messages as Seen Endpoint.
    """
    db = get_database()
    user_id = get_user_id(current_user)
    now = datetime.now(timezone.utc)

    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "read_by.user_id": {"$ne": user_id}},
        {"$push": {"read_by": {"user_id": user_id, "read_at": now}}}
    )

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {f"unread_counts.{user_id}": 0}}
    )

    return {"message": "Messages marked as read"}


@router.websocket("/ws/{user_id}")
async def websocket_chat_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket Chat Endpoint for Real-time Messaging.
    """
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")
            if event_type == "read":
                c_id = data.get("conversation_id")
                if c_id:
                    db = get_database()
                    now = datetime.now(timezone.utc)
                    await db["chat_messages"].update_many(
                        {"conversation_id": c_id, "read_by.user_id": {"$ne": user_id}},
                        {"$push": {"read_by": {"user_id": user_id, "read_at": now}}}
                    )
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
