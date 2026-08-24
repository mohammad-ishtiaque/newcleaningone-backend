import uuid
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from app.models.user import UserInDB, RoleEnum
from app.schemas.chat import (
    ConversationResponse, ConversationListItemResponse, ConversationDetailResponse,
    ParticipantInfo, LastMessageInfo, MessageResponse, ReadByInfo, ParticipantProfileResponse,
    ParticipantDetailItem, ConversationParticipantsResponse
)

def get_user_id(user: UserInDB) -> str:
    """Helper to extract string user ID cleanly."""
    return str(getattr(user, "id", None) or getattr(user, "_id", None) or getattr(user, "mongo_id", None) or "user_default")

def build_user_id_or_query(user_ids: List[str]) -> List[dict]:
    """Builds MongoDB query clauses to match string IDs or ObjectIds."""
    clean_ids = [str(u).strip() for u in user_ids if str(u).strip()]
    obj_ids = [ObjectId(u) for u in clean_ids if ObjectId.is_valid(u)]
    clauses = [{"id": {"$in": clean_ids}}, {"_id": {"$in": clean_ids}}]
    if obj_ids:
        clauses.append({"_id": {"$in": obj_ids}})
    return clauses

def build_user_map(users: List[dict]) -> dict:
    """Builds dual lookup map by string ObjectId and string id."""
    mapping = {}
    for u in users:
        if "_id" in u:
            mapping[str(u["_id"])] = u
        if "id" in u and u["id"]:
            mapping[str(u["id"])] = u
    return mapping

def resolve_conversation_type(doc: dict, current_user_id: Optional[str] = None) -> str:
    """
    Dynamically determines conversation type:
    - 'Group': shift group, cleaning plan group, >1 worker, >1 client, or both worker and client.
    - 'Direct clients': 1 Client + Admins/Managers.
    - 'direct worker': 1 Worker + Admins/Managers (Management Support Thread).
    """
    raw_type = str(doc.get("type", "direct")).strip().lower()
    raw_participants = doc.get("participants", [])

    if raw_type in ["group", "cleaning_plan_group"] or doc.get("shift_id") or doc.get("cleaning_plan_id"):
        return "Group"

    client_participants = [p for p in raw_participants if str(p.get("role", "")).lower() == "client"]
    worker_participants = [p for p in raw_participants if str(p.get("role", "")).lower() in ["worker", "employee", "cleaner"]]

    if (len(client_participants) > 0 and len(worker_participants) > 0) or len(client_participants) > 1 or len(worker_participants) > 1:
        return "Group"

    if len(client_participants) == 1 and len(worker_participants) == 0:
        return "Direct clients"

    if len(worker_participants) == 1 and len(client_participants) == 0:
        return "direct worker"

    if raw_type in ["direct_worker", "direct worker", "worker"]:
        return "direct worker"
    if raw_type in ["direct_client", "direct clients", "client"]:
        return "Direct clients"

    return "direct worker"


def _extract_conv_base(doc: dict, current_user_id: str, viewer_role: Optional[str] = None):
    conv_id = str(doc.get("_id") or doc.get("id"))
    unread_c = doc.get("unread_counts", {}).get(current_user_id, 0)
    raw_participants = doc.get("participants", [])
    last_msg = doc.get("last_message")
    last_msg_res = LastMessageInfo(
        text=last_msg.get("text", ""),
        sender_id=str(last_msg.get("sender_id", "")),
        sender_name=last_msg.get("sender_name", ""),
        timestamp=last_msg.get("timestamp", "")
    ) if last_msg else None

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)
    conv_type = resolve_conversation_type(doc, current_user_id=current_user_id)

    # Determine current viewer's role if not explicitly passed
    if not viewer_role:
        for p in raw_participants:
            if str(p.get("user_id")) == str(current_user_id):
                viewer_role = str(p.get("role", "")).lower()
                break

    # Context-Aware Title, Subtitle, and Avatar
    title = doc.get("title") or "Conversation"
    subtitle = doc.get("subtitle")
    avatar_url = doc.get("avatar_url")

    if conv_type == "direct worker":
        worker_p = next((p for p in raw_participants if str(p.get("role", "")).lower() in ["worker", "employee", "cleaner"]), None)
        admin_p = next((p for p in raw_participants if str(p.get("role", "")).lower() in ["admin", "manager"]), None)

        if viewer_role in ["worker", "employee", "cleaner"] or (worker_p and str(worker_p.get("user_id")) == str(current_user_id)):
            # Worker view: sees "Management & Support"
            title = "Management & Support"
            subtitle = subtitle or "Admin & Manager Team"
            avatar_url = avatar_url or (admin_p.get("profile_picture") if admin_p else None)
        else:
            # Admin / Manager view: sees Worker's full name & profile
            if worker_p:
                title = worker_p.get("name") or "Worker"
                subtitle = subtitle or "Worker Direct Chat"
                avatar_url = avatar_url or worker_p.get("profile_picture")

    elif conv_type == "Direct clients":
        client_p = next((p for p in raw_participants if str(p.get("role", "")).lower() == "client"), None)
        admin_p = next((p for p in raw_participants if str(p.get("role", "")).lower() in ["admin", "manager"]), None)

        if viewer_role == "client" or (client_p and str(client_p.get("user_id")) == str(current_user_id)):
            # Client view: sees "Management & Support"
            title = "Management & Support"
            subtitle = subtitle or "Admin & Manager Team"
            avatar_url = avatar_url or (admin_p.get("profile_picture") if admin_p else None)
        else:
            # Admin / Manager view: sees Client's Name
            if client_p:
                title = client_p.get("name") or "Client"
                subtitle = subtitle or "Direct Client"
                avatar_url = avatar_url or client_p.get("profile_picture")

    elif not avatar_url and conv_type != "Group":
        other_p = next((p for p in raw_participants if str(p.get("user_id")) != str(current_user_id)), None)
        if other_p:
            avatar_url = other_p.get("profile_picture")

    # Extract Admin/Manager seen footprint
    raw_seen = doc.get("seen_by_management", [])
    seen_by_res = []
    for s in raw_seen:
        s_dt = s.get("read_at") if isinstance(s.get("read_at"), datetime) else datetime.now(timezone.utc)
        seen_by_res.append(ReadByInfo(
            user_id=str(s.get("user_id")),
            name=s.get("name"),
            role=s.get("role"),
            profile_picture=s.get("profile_picture"),
            read_at=s_dt
        ))

    base = {
        "id": conv_id,
        "type": conv_type,
        "title": title,
        "subtitle": subtitle,
        "shift_id": doc.get("shift_id"),
        "cleaning_plan_id": doc.get("cleaning_plan_id"),
        "avatar_url": avatar_url,
        "participants_count": len(raw_participants),
        "last_message": last_msg_res,
        "unread_count": unread_c,
        "seen_by_management": seen_by_res,
        "created_at": c_at,
        "updated_at": u_at
    }
    return base, raw_participants


def format_conversation_list_item(doc: dict, current_user_id: str, viewer_role: Optional[str] = None) -> ConversationListItemResponse:
    """Formats a raw MongoDB conversation document into a compact ConversationListItemResponse."""
    base, _ = _extract_conv_base(doc, current_user_id, viewer_role=viewer_role)
    return ConversationListItemResponse(**base)


def format_conversation_detail(doc: dict, current_user_id: str, viewer_role: Optional[str] = None) -> ConversationDetailResponse:
    """Formats a raw MongoDB conversation document into full ConversationDetailResponse."""
    base, raw_participants = _extract_conv_base(doc, current_user_id, viewer_role=viewer_role)
    participants_res = [
        ParticipantInfo(
            user_id=str(p.get("user_id")),
            name=p.get("name", "User"),
            role=p.get("role", "client"),
            profile_picture=p.get("profile_picture")
        )
        for p in raw_participants
    ]
    return ConversationDetailResponse(**base, participants=participants_res)

format_conversation = format_conversation_list_item


def format_message(doc: dict) -> MessageResponse:
    """Formats a raw MongoDB message document into a MessageResponse schema with rich read_by footprint."""
    msg_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    read_by_res = []
    for r in doc.get("read_by", []):
        r_dt = r.get("read_at") if isinstance(r.get("read_at"), datetime) else datetime.now(timezone.utc)
        read_by_res.append(ReadByInfo(
            user_id=str(r.get("user_id")),
            name=r.get("name"),
            role=r.get("role"),
            profile_picture=r.get("profile_picture"),
            read_at=r_dt
        ))

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
    uid = str(user_doc.get("_id") or user_doc.get("id") or "")
    role = str(user_doc.get("role", "worker")).lower()
    name = user_doc.get("full_name") or user_doc.get("name", "User")
    email = user_doc.get("email") or ""
    phone = user_doc.get("phone") or user_doc.get("phone_number") or ""
    pic = user_doc.get("profile_photo") or user_doc.get("profile_picture")

    client_name = ""
    role_label = role.capitalize()
    current_loc_name = user_doc.get("location") or user_doc.get("address") or ""

    if role in ["worker", "employee"]:
        w_type = user_doc.get("worker_type", "employee")
        pos = user_doc.get("position")
        role_label = f"{w_type.capitalize()} • {pos}" if pos else f"{w_type.capitalize()}"
        client_name = f"Worker • {w_type.capitalize()}"

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        shift_doc = await db["shifts"].find_one({
            "workers.worker_id": uid,
            "date": {"$regex": f"^{today_str}"}
        })
        if not shift_doc:
            shift_doc = await db["shift_executions"].find_one({
                "assigned_workers.worker_id": uid,
                "date": today_str
            })
        if not shift_doc:
            shift_doc = await db["shifts"].find_one({"workers.worker_id": uid})

        if shift_doc and shift_doc.get("location_name"):
            current_loc_name = shift_doc.get("location_name")
        elif shift_doc and shift_doc.get("location_id"):
            loc_doc = await db["locations"].find_one({"$or": [{"_id": shift_doc.get("location_id")}, {"id": shift_doc.get("location_id")}]})
            current_loc_name = loc_doc.get("name", "") if loc_doc else ""
        else:
            current_loc_name = user_doc.get("location") or ""

    elif role == "client":
        c_doc = await db["client_list"].find_one({"$or": [{"_id": uid}, {"id": uid}, {"email": email}, {"primary_contact_name": name}]})
        if c_doc:
            client_name = c_doc.get("company_name", name)
            role_label = client_name
            current_loc_name = c_doc.get("address") or c_doc.get("location_name") or ""
        else:
            client_name = user_doc.get("company_name") or name
            role_label = client_name
            current_loc_name = user_doc.get("location") or user_doc.get("address") or ""

    return ParticipantProfileResponse(
        user_id=uid,
        name=name,
        role=role,
        role_label=role_label,
        email=email,
        phone=phone,
        current_location_name=current_loc_name,
        client_name=client_name,
        account_status="Active client" if role == "client" else ("Active manager" if role in ["manager", "admin"] else "Active worker"),
        is_online=True,
        profile_picture=pic
    )


async def get_conversation_participants_details(db, conv_doc: dict) -> ConversationParticipantsResponse:
    """
    Returns rich details of all participants in a conversation (name, role, email, phone, avatar, position, company).
    """
    conv_id = str(conv_doc.get("_id") or conv_doc.get("id"))
    conv_title = conv_doc.get("title") or "Conversation"
    raw_participants = conv_doc.get("participants", [])

    items = []
    p_uids = [str(p.get("user_id")) for p in raw_participants if p.get("user_id")]

    # Fetch users from database
    user_list = []
    if p_uids:
        users_cursor = db["users"].find({"$or": build_user_id_or_query(p_uids)})
        user_list = await users_cursor.to_list(length=len(p_uids) + 50)
    user_map = build_user_map(user_list)

    for p in raw_participants:
        u_id = str(p.get("user_id"))
        u_doc = user_map.get(u_id) or {}
        role = str(u_doc.get("role") or p.get("role") or "client")
        name = u_doc.get("full_name") or p.get("name") or "User"
        email = u_doc.get("email")
        phone = u_doc.get("phone")
        pic = u_doc.get("profile_photo") or p.get("profile_picture")
        pos = u_doc.get("position")
        comp = u_doc.get("company_name")

        items.append(ParticipantDetailItem(
            user_id=u_id, name=name, role=role, email=email,
            phone=phone, profile_picture=pic, position=pos,
            company_name=comp, is_online=True
        ))

    return ConversationParticipantsResponse(
        conversation_id=conv_id, conversation_title=conv_title,
        total_participants=len(items), participants=items
    )
