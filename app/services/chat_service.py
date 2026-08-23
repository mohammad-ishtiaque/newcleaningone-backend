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
    - 'Group': group type, cleaning plan group, shift group, or > 2 participants
    - 'Direct clients': 1-1 conversation with a Client
    - 'direct worker': 1-1 conversation with a Worker / Cleaner
    """
    raw_type = str(doc.get("type", "direct")).strip().lower()
    raw_participants = doc.get("participants", [])

    if raw_type in ["group", "cleaning_plan_group"] or doc.get("shift_id") or doc.get("cleaning_plan_id") or len(raw_participants) > 2:
        return "Group"

    other_participants = [p for p in raw_participants if str(p.get("user_id")) != str(current_user_id)] if current_user_id else raw_participants
    if not other_participants and raw_participants:
        other_participants = raw_participants

    has_client = any(str(p.get("role", "")).lower() == "client" for p in other_participants)
    has_worker = any(str(p.get("role", "")).lower() in ["worker", "employee", "cleaner"] for p in other_participants)

    if has_client and not has_worker:
        return "Direct clients"
    elif has_worker and not has_client:
        return "direct worker"
    elif has_client and has_worker:
        return "Group"
    elif raw_type in ["direct_client", "client", "direct clients"]:
        return "Direct clients"
    elif raw_type in ["direct_worker", "worker", "employee", "direct worker"]:
        return "direct worker"

    return "direct worker"


def _extract_conv_base(doc: dict, current_user_id: str):
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

    avatar_url = doc.get("avatar_url")
    if not avatar_url and conv_type != "Group":
        other_p = next((p for p in raw_participants if str(p.get("user_id")) != str(current_user_id)), None)
        if other_p:
            avatar_url = other_p.get("profile_picture")

    base = {
        "id": conv_id,
        "type": conv_type,
        "title": doc.get("title", "Conversation"),
        "subtitle": doc.get("subtitle"),
        "shift_id": doc.get("shift_id"),
        "cleaning_plan_id": doc.get("cleaning_plan_id"),
        "avatar_url": avatar_url,
        "participants_count": len(raw_participants),
        "last_message": last_msg_res,
        "unread_count": unread_c,
        "created_at": c_at,
        "updated_at": u_at
    }
    return base, raw_participants


def format_conversation_list_item(doc: dict, current_user_id: str) -> ConversationListItemResponse:
    """Formats a raw MongoDB conversation document into a compact ConversationListItemResponse (short form)."""
    base, _ = _extract_conv_base(doc, current_user_id)
    return ConversationListItemResponse(**base)


def format_conversation_detail(doc: dict, current_user_id: str) -> ConversationDetailResponse:
    """Formats a raw MongoDB conversation document into full ConversationDetailResponse (with all participants)."""
    base, raw_participants = _extract_conv_base(doc, current_user_id)
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

format_conversation = format_conversation_detail

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

async def sync_cleaning_plan_group_conversation(db, plan_doc: dict, current_manager: Optional[UserInDB] = None) -> dict:
    """
    Automatically creates or updates a group chat conversation for a cleaning plan
    containing all assigned Workers, the Client, and all Managers (and Admin).
    """
    plan_id = str(plan_doc.get("_id") or plan_doc.get("id"))
    client_id_raw = str(plan_doc.get("client_id", "client_1"))
    location_name = plan_doc.get("location_name") or "Facility"
    now = datetime.now(timezone.utc)

    participants = []
    seen_uids = set()

    # 1. Add All Managers and Admin (All managers should see and be part of this group)
    admin_mgr_cursor = db["users"].find({"role": {"$in": ["manager", "admin", RoleEnum.manager, RoleEnum.admin]}, "is_active": True})
    admin_mgr_users = await admin_mgr_cursor.to_list(length=100)
    for adm in admin_mgr_users:
        u_id = str(adm.get("_id") or adm.get("id"))
        if u_id not in seen_uids:
            seen_uids.add(u_id)
            participants.append({
                "user_id": u_id,
                "name": adm.get("full_name", "Manager"),
                "role": str(adm.get("role", "manager")),
                "profile_picture": adm.get("profile_photo")
            })

    if current_manager:
        cm_id = get_user_id(current_manager)
        if cm_id not in seen_uids:
            seen_uids.add(cm_id)
            participants.append({
                "user_id": cm_id,
                "name": getattr(current_manager, "full_name", "Manager"),
                "role": str(getattr(current_manager, "role", "manager")),
                "profile_picture": getattr(current_manager, "profile_photo", None)
            })

    # 2. Add Client
    c_query = {"$or": [{"_id": ObjectId(client_id_raw)}, {"id": client_id_raw}, {"_id": client_id_raw}]} if ObjectId.is_valid(client_id_raw) else {"$or": [{"_id": client_id_raw}, {"id": client_id_raw}]}
    c_user = await db["users"].find_one(c_query)
    
    if not c_user:
        c_list_doc = await db["client_list"].find_one({"$or": [{"_id": client_id_raw}, {"id": client_id_raw}]})
        if c_list_doc and c_list_doc.get("email"):
            c_user = await db["users"].find_one({"email": c_list_doc["email"]})
        if not c_user and c_list_doc:
            c_user = {
                "_id": client_id_raw,
                "full_name": c_list_doc.get("primary_contact_name") or c_list_doc.get("company_name") or "Client",
                "role": "client",
                "profile_photo": None
            }

    if c_user:
        c_uid = str(c_user.get("_id") or c_user.get("id"))
        if c_uid not in seen_uids:
            seen_uids.add(c_uid)
            c_name = c_user.get("full_name") or plan_doc.get("client_name") or "Client"
            participants.append({
                "user_id": c_uid,
                "name": c_name,
                "role": "client",
                "profile_picture": c_user.get("profile_photo")
            })

    # 3. Add Assigned Workers
    raw_worker_ids = plan_doc.get("worker_ids", [])
    if not raw_worker_ids and plan_doc.get("assigned_workers"):
        raw_worker_ids = [w.get("worker_id") for w in plan_doc.get("assigned_workers") if isinstance(w, dict)]

    if raw_worker_ids:
        w_query_ids = [str(w).strip() for w in raw_worker_ids if str(w).strip()]
        cursor_w = db["users"].find({"$or": build_user_id_or_query(w_query_ids)})
        w_users = await cursor_w.to_list(length=len(w_query_ids) * 2)
        w_user_map = build_user_map(w_users)

        for w_id in w_query_ids:
            w_doc = w_user_map.get(w_id)
            if w_id not in seen_uids:
                seen_uids.add(w_id)
                w_name = w_doc.get("full_name", "Worker") if w_doc else "Worker"
                w_pic = w_doc.get("profile_photo") if w_doc else None
                participants.append({
                    "user_id": w_id,
                    "name": w_name,
                    "role": "worker",
                    "profile_picture": w_pic
                })

    num_workers = len(raw_worker_ids)
    conv_title = plan_doc.get("title") or plan_doc.get("plan_name") or plan_doc.get("name") or f"{location_name} - Cleaning Team"
    conv_subtitle = f"Plan #{plan_id[:8]} • {num_workers} Worker(s)"

    # Look for existing conversation for this cleaning_plan_id
    existing = await db["conversations"].find_one({
        "$or": [
            {"cleaning_plan_id": plan_id},
            {"_id": f"conv_grp_plan_{plan_id}"},
            {"id": f"conv_grp_plan_{plan_id}"}
        ]
    })

    if existing:
        await db["conversations"].update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "title": conv_title,
                "subtitle": conv_subtitle,
                "cleaning_plan_id": plan_id,
                "participants": participants,
                "updated_at": now
            }}
        )
        return await db["conversations"].find_one({"_id": existing["_id"]})
    else:
        conv_id = f"conv_grp_plan_{plan_id}"
        
        # Create initial system welcome message
        initial_msg_id = f"msg_sys_{uuid.uuid4().hex[:10]}"
        initial_msg_text = f"Cleaning plan group created for {location_name} with {num_workers} assigned worker(s)."
        sys_msg_doc = {
            "_id": initial_msg_id, "id": initial_msg_id, "conversation_id": conv_id,
            "sender_id": "system", "sender_name": "CleanOnes System", "sender_role": "system",
            "sender_avatar": None, "content": initial_msg_text, "attachment_url": None,
            "attachment_type": None, "status": "sent", "read_by": [],
            "created_at": now, "updated_at": now
        }
        await db["chat_messages"].insert_one(sys_msg_doc)

        conv_doc = {
            "_id": conv_id,
            "id": conv_id,
            "type": "group",
            "title": conv_title,
            "subtitle": conv_subtitle,
            "cleaning_plan_id": plan_id,
            "participants": participants,
            "last_message": {
                "text": initial_msg_text,
                "sender_id": "system",
                "sender_name": "CleanOnes System",
                "timestamp": now.strftime("%I:%M %p")
            },
            "unread_counts": {},
            "created_at": now,
            "updated_at": now
        }
        await db["conversations"].insert_one(conv_doc)

        try:
            from app.api.chat import ws_manager
            all_p_uids = list(seen_uids)
            await ws_manager.broadcast_to_users({
                "type": "new_conversation",
                "conversation": format_conversation(conv_doc, current_user_id="system").model_dump(mode="json"),
                "message": initial_msg_text
            }, all_p_uids)
        except Exception:
            pass

        return conv_doc

async def sync_shift_group_conversation(db, shift_doc: dict) -> dict:
    """Automatically creates or updates a group chat conversation for a shift containing Admin, Client, and assigned Workers."""
    shift_id = str(shift_doc.get("_id") or shift_doc.get("id"))
    client_id = str(shift_doc.get("client_id", "client_1"))
    client_name = shift_doc.get("client_name") or shift_doc.get("client_company_name") or "Client"
    location_name = shift_doc.get("location_name", "Main Location")

    participants = []

    # Add Admin / Manager
    admin_user = await db["users"].find_one({"role": {"$in": ["admin", "manager"]}})
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

async def add_conversation_participants(
    db,
    conversation_id: str,
    user_ids: List[str],
    actor_user: UserInDB
) -> ConversationParticipantsResponse:
    """
    Adds one or more users (workers/clients/managers) to an existing group conversation.
    """
    from fastapi import HTTPException
    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv_doc.get("type") != "group":
        raise HTTPException(status_code=400, detail="Cannot add participants to a direct 1-1 conversation. Use a group conversation.")

    existing_participants = conv_doc.get("participants", [])
    existing_uids = set(str(p.get("user_id")) for p in existing_participants if p.get("user_id"))

    clean_uids = [str(u).strip() for u in user_ids if str(u).strip()]
    if not clean_uids:
        raise HTTPException(status_code=400, detail="At least one valid user_id must be provided")

    # Fetch users to add
    users_cursor = db["users"].find({"$or": build_user_id_or_query(clean_uids)})
    found_users = await users_cursor.to_list(length=len(clean_uids) * 2)
    found_map = build_user_map(found_users)

    new_participants = []
    added_names = []

    for u_id in clean_uids:
        if u_id in existing_uids:
            continue
        u_doc = found_map.get(u_id)
        if not u_doc:
            c_doc = await db["client_list"].find_one({"$or": [{"_id": u_id}, {"id": u_id}]})
            if c_doc:
                u_doc = {
                    "_id": u_id,
                    "full_name": c_doc.get("primary_contact_name") or c_doc.get("company_name") or "Client",
                    "role": "client",
                    "profile_photo": None
                }

        if not u_doc:
            raise HTTPException(status_code=404, detail=f"User with ID '{u_id}' not found")

        u_name = u_doc.get("full_name") or u_doc.get("name") or "User"
        p_item = {
            "user_id": u_id,
            "name": u_name,
            "role": str(u_doc.get("role", "client")),
            "profile_picture": u_doc.get("profile_photo") or u_doc.get("profile_picture")
        }
        new_participants.append(p_item)
        existing_uids.add(u_id)
        added_names.append(u_name)

    if not new_participants:
        return await get_conversation_participants_details(db, conv_doc)

    updated_participants = existing_participants + new_participants
    now = datetime.now(timezone.utc)

    # Post system message announcing addition
    actor_name = getattr(actor_user, "full_name", None) or "Admin"
    sys_msg_id = f"msg_sys_{uuid.uuid4().hex[:10]}"
    sys_msg_text = f"{actor_name} added {', '.join(added_names)} to the group."

    sys_msg_doc = {
        "_id": sys_msg_id,
        "id": sys_msg_id,
        "conversation_id": conversation_id,
        "sender_id": "system",
        "sender_name": "CleanOnes System",
        "sender_role": "system",
        "sender_avatar": None,
        "content": sys_msg_text,
        "attachment_url": None,
        "attachment_type": None,
        "status": "sent",
        "read_by": [],
        "created_at": now,
        "updated_at": now
    }
    await db["chat_messages"].insert_one(sys_msg_doc)

    await db["conversations"].update_one(
        {"_id": conv_doc["_id"]},
        {"$set": {
            "participants": updated_participants,
            "last_message": {
                "text": sys_msg_text,
                "sender_id": "system",
                "sender_name": "CleanOnes System",
                "timestamp": now.strftime("%I:%M %p")
            },
            "updated_at": now
        }}
    )

    try:
        from app.api.chat import ws_manager
        all_member_uids = list(existing_uids)
        await ws_manager.broadcast_to_users({
            "type": "participants_added",
            "conversation_id": conversation_id,
            "added_users": new_participants,
            "message": sys_msg_text
        }, all_member_uids)
    except Exception:
        pass

    updated_conv = await db["conversations"].find_one({"_id": conv_doc["_id"]})
    return await get_conversation_participants_details(db, updated_conv)

async def remove_conversation_participant(
    db,
    conversation_id: str,
    target_user_id: str,
    actor_user: UserInDB
) -> dict:
    """
    Removes a user (worker/client/manager) from an existing group conversation.
    """
    from fastapi import HTTPException
    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv_doc.get("type") != "group":
        raise HTTPException(status_code=400, detail="Cannot remove participants from a direct 1-1 conversation.")

    existing_participants = conv_doc.get("participants", [])
    target_p = next((p for p in existing_participants if str(p.get("user_id")) == str(target_user_id)), None)
    if not target_p:
        raise HTTPException(status_code=404, detail=f"User '{target_user_id}' is not a participant in this conversation")

    if len(existing_participants) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last remaining participant in the conversation")

    updated_participants = [p for p in existing_participants if str(p.get("user_id")) != str(target_user_id)]
    now = datetime.now(timezone.utc)

    # Post system message announcing removal
    target_name = target_p.get("name", "User")
    actor_name = getattr(actor_user, "full_name", None) or "Admin"
    sys_msg_doc = {
        "_id": sys_msg_id, "id": sys_msg_id, "conversation_id": conversation_id,
        "sender_id": "system", "sender_name": "CleanOnes System", "sender_role": "system",
        "sender_avatar": None, "content": sys_msg_text, "attachment_url": None,
        "attachment_type": None, "status": "sent", "read_by": [],
        "created_at": now, "updated_at": now
    }
    await db["chat_messages"].insert_one(sys_msg_doc)

    await db["conversations"].update_one(
        {"_id": conv_doc["_id"]},
        {
            "$set": {
                "participants": updated_participants,
                "last_message": {
                    "text": sys_msg_text,
                    "sender_id": "system",
                    "sender_name": "CleanOnes System",
                    "timestamp": now.strftime("%I:%M %p")
                },
                "updated_at": now
            },
            "$unset": {f"unread_counts.{target_user_id}": ""}
        }
    )

    try:
        from app.api.chat import ws_manager
        all_member_uids = [str(p.get("user_id")) for p in existing_participants]
        await ws_manager.broadcast_to_users({
            "type": "participant_removed",
            "conversation_id": conversation_id,
            "removed_user_id": str(target_user_id),
            "message": sys_msg_text
        }, all_member_uids)
    except Exception:
        pass

    return {
        "message": f"User '{target_name}' removed from conversation successfully",
        "conversation_id": conversation_id,
        "removed_user_id": str(target_user_id),
        "remaining_participants_count": len(updated_participants)
    }
