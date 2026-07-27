import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from typing import Optional, List, Dict
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.schemas.chat import (
    ConversationCreate, ConversationResponse, ParticipantInfo, LastMessageInfo,
    MessageCreate, MessageUpdate, MessageResponse, PaginatedMessagesResponse, ReadByInfo
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


# ==========================================
# CONVERSATION ENDPOINTS
# ==========================================

@router.get("/chat/conversations", response_model=List[ConversationResponse], summary="List Active Conversations")
async def list_user_conversations(
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)

    if current_user.role == RoleEnum.admin:
        query = {}
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
    user_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)

    if conv_in.type == "direct":
        user_role_str = current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role)
        if user_role_str not in ["client", "admin"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="1-on-1 direct private chat is strictly reserved between Client and Admin only.")

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

        target_query = {"_id": ObjectId(target_id)} if ObjectId.is_valid(target_id) else {"_id": target_id}
        target_user = await db["users"].find_one(target_query)
        target_name = target_user.get("full_name", "Clean Ones Admin") if target_user else "Clean Ones"
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

        s_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
        cleaning_name = conv_in.title
        company_name = conv_in.subtitle

        if s_doc:
            if not company_name:
                company_name = s_doc.get("client_name")
                c_id = s_doc.get("client_id")
                if c_id:
                    cl_doc = await db["client_list"].find_one({"_id": c_id})
                    if cl_doc and cl_doc.get("company_name"):
                        company_name = cl_doc.get("company_name")

            if not cleaning_name:
                cleaning_name = s_doc.get("cleaning_plan_name") or s_doc.get("title")
                cp_id = s_doc.get("cleaning_plan_id")
                if cp_id:
                    cp_doc = await db["global_cleaning_plans"].find_one({"$or": [{"_id": cp_id}, {"id": cp_id}]})
                    if cp_doc:
                        cleaning_name = cp_doc.get("title") or cp_doc.get("plan_name") or cp_doc.get("name") or cleaning_name

        cleaning_name = cleaning_name or "Service team"
        company_name = company_name or "Company"

        conv_id = f"conv_grp_{shift_id}"
        doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "group",
            "title": cleaning_name,
            "subtitle": company_name,
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
    user_id = str(current_user.id or current_user.mongo_id)
    return _format_conversation(doc, current_user_id=user_id)


# ==========================================
# MESSAGE ENDPOINTS (CRUD & READ RECEIPTS)
# ==========================================

@router.get("/chat/conversations/{conversation_id}/messages", response_model=PaginatedMessagesResponse, summary="Get Paginated Conversation Messages")
async def get_conversation_messages(
    conversation_id: str,
    page: int = 1,
    limit: int = 30,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    query = {"conversation_id": conversation_id}
    total_count = await db["chat_messages"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["chat_messages"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_msgs = await cursor.to_list(length=limit)
    raw_msgs.reverse()

    now = datetime.now(timezone.utc)
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


@router.post("/chat/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED, summary="Send Message")
async def send_message(
    conversation_id: str,
    msg_in: MessageCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    sender_id = str(current_user.id or current_user.mongo_id)
    sender_name = getattr(current_user, "full_name", "User")
    sender_role = current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role)
    sender_avatar = getattr(current_user, "profile_photo", None)

    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

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

    time_str = now.strftime("%I:%M %p")
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
    sender_id = str(current_user.id or current_user.mongo_id)

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
    sender_id = str(current_user.id or current_user.mongo_id)

    msg_doc = await db["chat_messages"].find_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Message not found")

    if current_user.role != RoleEnum.admin and msg_doc.get("sender_id") != sender_id:
        raise HTTPException(status_code=403, detail="Permission denied to delete message")

    await db["chat_messages"].delete_one({"$or": [{"_id": message_id}, {"id": message_id}]})
    return {"message": "Message deleted successfully"}


@router.post("/chat/conversations/{conversation_id}/read", summary="Mark Conversation Messages as Read")
async def mark_conversation_read(
    conversation_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)
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
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
    except Exception:
        ws_manager.disconnect(user_id, websocket)


# ==========================================
# ROLE ROUTER SHORTCUTS (/client/chat, /admin/chat, /worker/chat)
# ==========================================

@router.get("/client/chat/conversations", response_model=List[ConversationResponse], summary="Client List Chat Conversations")
async def client_list_conversations(current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(current_user=current_user)

@router.get("/admin/chat/conversations", response_model=List[ConversationResponse], summary="Admin List Chat Conversations")
async def admin_list_conversations(current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(current_user=current_user)

@router.get("/worker/chat/conversations", response_model=List[ConversationResponse], summary="Worker List Chat Conversations")
async def worker_list_conversations(current_user: UserInDB = Depends(get_current_user)):
    return await list_user_conversations(current_user=current_user)
