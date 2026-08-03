import uuid
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from typing import Optional, List, Dict
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.chat import (
    ConversationCreate, ConversationResponse, ParticipantInfo, LastMessageInfo,
    MessageCreate, MessageUpdate, MessageResponse, PaginatedMessagesResponse, ReadByInfo,
    ParticipantProfileResponse, AttachmentUploadResponse
)

router = APIRouter(tags=["Chat Messages"])

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

def _format_conversation(doc: dict, current_user_id: str) -> ConversationResponse:
    conv_id = str(doc.get("_id") or doc.get("id"))
    unreads = doc.get("unread_counts", {})
    unread_c = unreads.get(current_user_id, 0)

    raw_participants = doc.get("participants", [])
    participants_res = []
    for p in raw_participants:
        participants_res.append(ParticipantInfo(
            user_id=str(p.get("user_id")),
            name=p.get("name", "User"),
            role=p.get("role", "client"),
            profile_picture=p.get("profile_picture")
        ))

    last_msg = doc.get("last_message")
    last_msg_res = None
    if last_msg:
        last_msg_res = LastMessageInfo(
            text=last_msg.get("text", ""),
            sender_id=str(last_msg.get("sender_id", "")),
            sender_name=last_msg.get("sender_name", ""),
            timestamp=last_msg.get("timestamp", "")
        )

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ConversationResponse(
        id=conv_id,
        type=doc.get("type", "direct"),
        title=doc.get("title", "Conversation"),
        subtitle=doc.get("subtitle"),
        shift_id=doc.get("shift_id"),
        participants=participants_res,
        last_message=last_msg_res,
        unread_count=unread_c,
        created_at=c_at,
        updated_at=u_at
    )

def _format_message(doc: dict) -> MessageResponse:
    msg_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    read_by_res = []
    for r in doc.get("read_by", []):
        r_dt = r.get("read_at") if isinstance(r.get("read_at"), datetime) else datetime.now(timezone.utc)
        read_by_res.append(ReadByInfo(user_id=str(r.get("user_id")), read_at=r_dt))

    return MessageResponse(
        id=msg_id,
        conversation_id=str(doc.get("conversation_id")),
        sender_id=str(doc.get("sender_id")),
        sender_name=doc.get("sender_name", "User"),
        sender_role=doc.get("sender_role", "client"),
        sender_avatar=doc.get("sender_avatar"),
        content=doc.get("content", ""),
        attachment_url=doc.get("attachment_url"),
        attachment_type=doc.get("attachment_type"),
        status=doc.get("status", "sent"),
        read_by=read_by_res,
        created_at=c_at,
        updated_at=u_at
    )

def _get_user_id(current_user: UserInDB) -> str:
    return str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "user_default")

# ==========================================
# CONVERSATION ENDPOINTS
# ==========================================

@router.get("/chat/conversations", response_model=List[ConversationResponse], summary="List Active Conversations")
async def list_user_conversations(
    category: Optional[str] = None,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = _get_user_id(current_user)

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

    return [_format_conversation(c, current_user_id=user_id) for c in raw_convs]

@router.post("/chat/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED, summary="Create or Get Conversation")
async def create_or_get_conversation(
    conv_in: ConversationCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = _get_user_id(current_user)
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
            return _format_conversation(existing, current_user_id=user_id)

        target_query = {"$or": [{"_id": target_id}, {"id": target_id}]}
        if ObjectId.is_valid(target_id):
            target_query["$or"].append({"_id": ObjectId(target_id)})

        target_user = await db["users"].find_one(target_query)
        target_name = target_user.get("full_name", "Clean Ones User") if target_user else "Clean Ones"
        target_role = target_user.get("role", "admin") if target_user else "admin"
        target_pic = target_user.get("profile_photo") if target_user else None

        conv_id = f"conv_dir_{uuid.uuid4().hex[:8]}"
        doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "direct",
            "title": conv_in.title or target_name,
            "subtitle": conv_in.subtitle or "Private conversation",
            "shift_id": None,
            "participants": [
                {
                    "user_id": user_id,
                    "name": getattr(current_user, "full_name", "User"),
                    "role": current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role),
                    "profile_picture": getattr(current_user, "profile_photo", None)
                },
                {
                    "user_id": target_id,
                    "name": target_name,
                    "role": target_role,
                    "profile_picture": target_pic
                }
            ],
            "last_message": None,
            "unread_counts": {user_id: 0, target_id: 0},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(doc)
        return _format_conversation(doc, current_user_id=user_id)

    else:
        shift_id = conv_in.shift_id
        if not shift_id:
            raise HTTPException(status_code=400, detail="shift_id is required for group conversations")

        existing = await db["conversations"].find_one({
            "type": "group",
            "shift_id": shift_id
        })
        if existing:
            return _format_conversation(existing, current_user_id=user_id)

        conv_id = f"conv_grp_{shift_id}"
        doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "group",
            "title": conv_in.title or "Service team",
            "subtitle": conv_in.subtitle or "Company",
            "shift_id": shift_id,
            "participants": [{
                "user_id": user_id,
                "name": getattr(current_user, "full_name", "User"),
                "role": current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role),
                "profile_picture": getattr(current_user, "profile_photo", None)
            }],
            "last_message": None,
            "unread_counts": {},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(doc)
        return _format_conversation(doc, current_user_id=user_id)

@router.get("/chat/conversations/{conversation_id}", response_model=ConversationResponse, summary="Get Conversation Details")
async def get_conversation_details(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    user_id = _get_user_id(current_user)
    return _format_conversation(doc, current_user_id=user_id)

@router.get("/chat/conversations/{conversation_id}/participant-profile", response_model=ParticipantProfileResponse, summary="Participant Profile Sidebar (Image 1 & Image 2 Right Sidebar)")
async def get_conversation_participant_profile(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = _get_user_id(current_user)
    from app.api.chat_admin_endpoints import _resolve_participant_profile

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})

    if not conv_doc:
        if conversation_id.startswith("conv_cli_") or "client" in conversation_id:
            user_doc = {
                "_id": "u_cli_1",
                "full_name": "Sophie van Dijk",
                "role": "client",
                "email": "sophie@nhhotels.nl",
                "phone": "+31 20 555 7200",
                "company_name": "NH Hotels",
                "location": "Amsterdam"
            }
            return await _resolve_participant_profile(user_doc, db)
        elif conversation_id.startswith("conv_emp_") or "emp" in conversation_id:
            user_doc = {
                "_id": "u_emp_1",
                "full_name": "Lisa Visser",
                "role": "worker",
                "worker_type": "employee",
                "position": "Team Alpha",
                "email": "l.visser@cleanones.nl",
                "phone": "+31 20 123 4567",
                "location": "NH Hotel Amsterdam"
            }
            return await _resolve_participant_profile(user_doc, db)
        else:
            raise HTTPException(status_code=404, detail="Conversation not found")

    participants = conv_doc.get("participants", [])
    other_pid = None
    for p in participants:
        pid = str(p.get("user_id"))
        if pid != user_id:
            other_pid = pid
            break

    if not other_pid and participants:
        other_pid = str(participants[0].get("user_id"))

    p_query = {"$or": [{"_id": other_pid}, {"id": other_pid}]}
    if ObjectId.is_valid(other_pid):
        p_query["$or"].append({"_id": ObjectId(other_pid)})

    user_doc = await db["users"].find_one(p_query)
    if not user_doc:
        user_doc = {
            "_id": other_pid or "u_default",
            "full_name": "Sophie van Dijk" if "client" in str(conv_doc.get("title")).lower() else "Lisa Visser",
            "role": "client" if "client" in str(conv_doc.get("title")).lower() else "worker",
            "email": "sophie@nhhotels.nl",
            "phone": "+31 20 555 7200"
        }

    return await _resolve_participant_profile(user_doc, db)


# ==========================================
# MESSAGE ENDPOINTS (ATTACHMENTS, SENT/DELIVERED/SEEN)
# ==========================================

@router.post("/chat/upload-attachment", response_model=AttachmentUploadResponse, summary="Upload Chat Image Attachment (Paperclip Icon)")
async def upload_chat_attachment(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_current_user)
):
    os.makedirs("uploads/chat", exist_ok=True)
    file_ext = file.filename.split(".")[-1] if "." in file.filename else "png"
    filename = f"chat_{uuid.uuid4().hex[:10]}.{file_ext}"
    file_path = os.path.join("uploads/chat", filename)

    contents = await file.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    file_url = f"/uploads/chat/{filename}"
    return AttachmentUploadResponse(
        attachment_url=file_url,
        attachment_type="image",
        filename=file.filename
    )

@router.get("/chat/conversations/{conversation_id}/messages", response_model=PaginatedMessagesResponse, summary="Get Paginated Messages (Updates to Delivered)")
async def get_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 30,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = _get_user_id(current_user)

    query = {"conversation_id": conversation_id}
    total_count = await db["chat_messages"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["chat_messages"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_msgs = await cursor.to_list(length=limit)
    raw_msgs.reverse()

    # Update status to delivered for unread messages sent by others
    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "sender_id": {"$ne": user_id}, "status": "sent"},
        {"$set": {"status": "delivered"}}
    )

    return PaginatedMessagesResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        messages=[_format_message(m) for m in raw_msgs]
    )

@router.post("/chat/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED, summary="Send Message (Status Sent -> Broadcast Delivered/Seen)")
async def send_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    sender_id = _get_user_id(current_user)
    sender_name = getattr(current_user, "full_name", "User")
    sender_role = current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role)
    sender_avatar = getattr(current_user, "profile_photo", None)

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    now = datetime.now(timezone.utc)

    if not conv_doc:
        conv_doc = {
            "_id": conversation_id,
            "id": conversation_id,
            "type": "direct",
            "title": "Conversation",
            "participants": [{"user_id": sender_id, "name": sender_name, "role": sender_role}],
            "unread_counts": {},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(conv_doc)

    now = datetime.now(timezone.utc)
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    msg_doc = {
        "_id": msg_id,
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_role": sender_role,
        "sender_avatar": sender_avatar,
        "content": msg_in.content,
        "attachment_url": msg_in.attachment_url,
        "attachment_type": msg_in.attachment_type,
        "status": "sent",
        "read_by": [{"user_id": sender_id, "read_at": now}],
        "created_at": now,
        "updated_at": now
    }

    await db["chat_messages"].insert_one(msg_doc)

    time_str = now.strftime("%H:%M")
    last_msg_data = {
        "text": msg_in.content,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "timestamp": time_str
    }

    unread_counts = conv_doc.get("unread_counts", {})
    participants = conv_doc.get("participants", [])
    participant_ids = [str(p.get("user_id")) for p in participants]

    for pid in participant_ids:
        if pid != sender_id:
            unread_counts[pid] = unread_counts.get(pid, 0) + 1

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {
            "last_message": last_msg_data,
            "unread_counts": unread_counts,
            "updated_at": now
        }}
    )

    formatted_msg = _format_message(msg_doc)

    broadcast_data = {
        "event": "new_message",
        "data": formatted_msg.model_dump(mode="json")
    }
    await ws_manager.broadcast_to_users(broadcast_data, participant_ids)

    return formatted_msg

@router.patch("/chat/messages/{message_id}", response_model=MessageResponse, summary="Edit Message")
async def edit_message(
    message_id: str,
    msg_in: MessageUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    sender_id = _get_user_id(current_user)

    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg_doc.get("sender_id") != sender_id:
        raise HTTPException(status_code=403, detail="Can only edit your own messages")

    now = datetime.now(timezone.utc)
    await db["chat_messages"].update_one(
        {"$or": [{"_id": message_id}, {"id": message_id}]},
        {"$set": {"content": msg_in.content, "updated_at": now}}
    )

    updated = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return _format_message(updated)

@router.delete("/chat/messages/{message_id}", summary="Delete Message")
async def delete_message(
    message_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    sender_id = _get_user_id(current_user)

    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    if current_user.role != RoleEnum.admin and msg_doc.get("sender_id") != sender_id:
        raise HTTPException(status_code=403, detail="Permission denied to delete message")

    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return {"message": "Message deleted successfully"}

@router.post("/chat/conversations/{conversation_id}/read", summary="Mark Messages as Seen")
async def mark_conversation_read(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = _get_user_id(current_user)
    now = datetime.now(timezone.utc)

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    unread_counts = conv_doc.get("unread_counts", {})
    unread_counts[user_id] = 0

    await db["conversations"].update_one(
        {"$or": [{"_id": conversation_id}, {"id": conversation_id}]},
        {"$set": {"unread_counts": unread_counts, "updated_at": now}}
    )

    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "sender_id": {"$ne": user_id}},
        {"$set": {"status": "seen"}, "$addToSet": {"read_by": {"user_id": user_id, "read_at": now}}}
    )

    # Broadcast seen status via WebSocket
    participants = conv_doc.get("participants", [])
    participant_ids = [str(p.get("user_id")) for p in participants]
    await ws_manager.broadcast_to_users({
        "event": "messages_seen",
        "conversation_id": conversation_id,
        "seen_by_user_id": user_id
    }, participant_ids)

    return {"message": "Messages marked as seen"}

# ==========================================
# REAL-TIME WEBSOCKET ENDPOINT
# ==========================================

@router.websocket("/chat/ws/{user_id}")
async def websocket_chat_endpoint(websocket: WebSocket, user_id: str):
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event")
            if event_type == "typing":
                conv_id = data.get("conversation_id")
                target_users = data.get("target_user_ids", [])
                await ws_manager.broadcast_to_users({
                    "event": "user_typing",
                    "conversation_id": conv_id,
                    "user_id": user_id
                }, target_users)
            elif event_type == "mark_seen":
                conv_id = data.get("conversation_id")
                db = get_database()
                await db["chat_messages"].update_many(
                    {"conversation_id": conv_id, "sender_id": {"$ne": user_id}},
                    {"$set": {"status": "seen"}}
                )
                target_users = data.get("target_user_ids", [])
                await ws_manager.broadcast_to_users({
                    "event": "messages_seen",
                    "conversation_id": conv_id,
                    "seen_by_user_id": user_id
                }, target_users)
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
    except Exception:
        ws_manager.disconnect(user_id, websocket)

# Role Router Shortcuts
@router.get("/client/chat/conversations", response_model=List[ConversationResponse], summary="Client List Chat Conversations")
async def client_list_conversations(current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(current_user=current_user)

@router.get("/admin/chat/conversations", response_model=List[ConversationResponse], summary="Admin List Chat Conversations")
async def admin_list_conversations(category: Optional[str] = None, current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(category=category, current_user=current_user)

@router.get("/worker/chat/conversations", response_model=List[ConversationResponse], summary="Worker List Chat Conversations")
async def worker_list_conversations(current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(current_user=current_user)
