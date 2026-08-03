import uuid
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from app.models.user import UserInDB, RoleEnum
from app.schemas.chat import (
    ConversationResponse, ParticipantInfo, LastMessageInfo,
    MessageResponse, ReadByInfo, ParticipantProfileResponse
)

def get_user_id(user: UserInDB) -> str:
    """Helper to extract string user ID cleanly."""
    return str(getattr(user, "id", None) or getattr(user, "_id", None) or getattr(user, "mongo_id", None) or "user_default")

def format_conversation(doc: dict, current_user_id: str) -> ConversationResponse:
    """Formats a raw MongoDB conversation document into a ConversationResponse schema."""
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

def format_message(doc: dict) -> MessageResponse:
    """Formats a raw MongoDB message document into a MessageResponse schema."""
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

async def resolve_participant_profile(user_doc: dict, db) -> ParticipantProfileResponse:
    """Resolves dynamic participant profile details for the chat right sidebar."""
    uid = str(user_doc.get("_id") or user_doc.get("id") or "user_1")
    role = str(user_doc.get("role", "worker")).lower()
    name = user_doc.get("full_name") or user_doc.get("name", "User")
    email = user_doc.get("email", "user@cleanones.nl")
    phone = user_doc.get("phone", "+31 20 555 7200")
    pic = user_doc.get("profile_photo") or user_doc.get("profile_picture")

    client_name = "NH Hotels"
    role_label = "NH Hotels"
    current_loc_name = "Amsterdam"

    if role in ["worker", "employee"]:
        w_type = user_doc.get("worker_type", "employee")
        pos = user_doc.get("position", "Team Alpha")
        role_label = f"{w_type.capitalize()} • {pos}" if pos else f"{w_type.capitalize()}"
        client_name = f"Employee • {pos}"

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        shift_doc = await db["shifts"].find_one({
            "workers.worker_id": uid,
            "date": {"$regex": f"^{today_str}"}
        })
        if not shift_doc:
            shift_doc = await db["shifts"].find_one({"workers.worker_id": uid})

        if shift_doc and shift_doc.get("location_name"):
            current_loc_name = shift_doc.get("location_name")
        elif shift_doc and shift_doc.get("location_id"):
            loc_doc = await db["locations"].find_one({"$or": [{"_id": shift_doc.get("location_id")}, {"id": shift_doc.get("location_id")}]})
            current_loc_name = loc_doc.get("name", "NH Hotel Amsterdam") if loc_doc else "NH Hotel Amsterdam"
        else:
            current_loc_name = user_doc.get("location") or "NH Hotel Amsterdam"

    elif role == "client":
        c_doc = await db["client_list"].find_one({"$or": [{"email": email}, {"primary_contact_name": name}]})
        if c_doc:
            client_name = c_doc.get("company_name", "NH Hotels")
            role_label = client_name
            current_loc_name = c_doc.get("address") or "Amsterdam"
        else:
            client_name = user_doc.get("company_name", "NH Hotels")
            role_label = client_name
            current_loc_name = user_doc.get("location", "Amsterdam")

    return ParticipantProfileResponse(
        user_id=uid,
        name=name,
        role=role,
        role_label=role_label,
        email=email,
        phone=phone,
        current_location_name=current_loc_name,
        client_name=client_name,
        account_status="Active client" if role == "client" else "Active worker",
        is_online=True,
        profile_picture=pic
    )

async def sync_shift_group_conversation(db, shift_doc: dict) -> dict:
    """Automatically creates or updates a group chat conversation for a shift containing Admin, Client, and assigned Workers."""
    shift_id = str(shift_doc.get("_id") or shift_doc.get("id"))
    client_id = str(shift_doc.get("client_id", "client_1"))
    client_name = shift_doc.get("client_name") or shift_doc.get("client_company_name") or "Client"
    location_name = shift_doc.get("location_name", "Main Location")

    participants = []

    # Add Admin
    admin_user = await db["users"].find_one({"role": "admin"})
    admin_id = str(admin_user.get("_id") or admin_user.get("id")) if admin_user else "admin_1"
    admin_name = admin_user.get("full_name", "Admin") if admin_user else "Admin"
    participants.append({
        "user_id": admin_id,
        "name": admin_name,
        "role": "admin",
        "profile_picture": getattr(admin_user, "profile_photo", None) if admin_user else None
    })

    # Add Client
    c_query = {"_id": ObjectId(client_id)} if ObjectId.is_valid(client_id) else {"_id": client_id}
    c_user = await db["users"].find_one(c_query)
    c_pic = c_user.get("profile_photo") if c_user else None
    participants.append({
        "user_id": client_id,
        "name": client_name,
        "role": "client",
        "profile_picture": c_pic
    })

    # Add Assigned Workers
    raw_workers = shift_doc.get("workers", [])
    for w in raw_workers:
        w_id = str(w.get("worker_id") or w.get("id"))
        w_name = w.get("name") or w.get("full_name") or "Worker"
        participants.append({
            "user_id": w_id,
            "name": w_name,
            "role": "worker",
            "profile_picture": w.get("profile_picture")
        })

    conv_title = f"{location_name} - Shift Group"
    conv_subtitle = f"Shift #{shift_id[:6]} • {len(raw_workers)} Worker(s)"

    existing = await db["conversations"].find_one({"shift_id": shift_id})
    now = datetime.now(timezone.utc)

    if existing:
        await db["conversations"].update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "title": conv_title,
                "subtitle": conv_subtitle,
                "participants": participants,
                "updated_at": now
            }}
        )
        return await db["conversations"].find_one({"_id": existing["_id"]})
    else:
        conv_id = f"conv_grp_{shift_id}"
        doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "group",
            "title": conv_title,
            "subtitle": conv_subtitle,
            "shift_id": shift_id,
            "participants": participants,
            "last_message": None,
            "unread_counts": {},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(doc)
        return doc
