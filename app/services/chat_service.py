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
from app.services.chat_formatters import (
    get_user_id, build_user_id_or_query, build_user_map, resolve_conversation_type,
    _extract_conv_base, format_conversation_list_item, format_conversation_detail,
    format_conversation, format_message, resolve_participant_profile,
    get_conversation_participants_details, get_total_unread_count
)


async def get_or_create_worker_admin_conversation(
    db,
    worker_user: UserInDB,
    custom_title: Optional[str] = None,
    custom_subtitle: Optional[str] = None
) -> dict:
    """
    Creates or retrieves the unified Direct Management conversation for a Worker.
    Includes the Worker and all active Admins & Managers.
    Idempotent: Re-calling always returns the existing conversation thread.
    """
    worker_id = get_user_id(worker_user)
    worker_name = getattr(worker_user, "full_name", None) or getattr(worker_user, "name", "Worker")
    worker_pic = getattr(worker_user, "profile_photo", None) or getattr(worker_user, "profile_picture", None)

    # 1. Fetch all active Admins & Managers from database
    admin_mgr_cursor = db["users"].find({
        "role": {"$in": ["admin", "manager", RoleEnum.admin, RoleEnum.manager]},
        "is_active": True
    })
    admin_mgr_users = await admin_mgr_cursor.to_list(length=100)

    participants = [{
        "user_id": worker_id,
        "name": worker_name,
        "role": "worker",
        "profile_picture": worker_pic
    }]

    seen_uids = {worker_id}
    for adm in admin_mgr_users:
        u_id = str(adm.get("_id") or adm.get("id"))
        if u_id not in seen_uids:
            seen_uids.add(u_id)
            participants.append({
                "user_id": u_id,
                "name": adm.get("full_name", "Manager"),
                "role": str(adm.get("role", "manager")).lower(),
                "profile_picture": adm.get("profile_photo")
            })

    if len(participants) == 1:
        participants.append({
            "user_id": "admin_1",
            "name": "Admin Support",
            "role": "admin",
            "profile_picture": None
        })

    # 2. Check for existing direct worker conversation
    existing = await db["conversations"].find_one({
        "$or": [
            {"_id": f"conv_worker_{worker_id}"},
            {"id": f"conv_worker_{worker_id}"},
            {
                "type": {"$in": ["direct", "direct_worker", "direct worker"]},
                "cleaning_plan_id": {"$in": [None, ""]},
                "shift_id": {"$in": [None, ""]},
                "participants.user_id": worker_id
            }
        ]
    })

    now = datetime.now(timezone.utc)

    if existing:
        update_fields = {}
        if existing.get("type") not in ["direct_worker", "direct worker"]:
            update_fields["type"] = "direct_worker"

        existing_participants = existing.get("participants", [])
        existing_p_map = {str(p.get("user_id")): p for p in existing_participants if p.get("user_id")}
        updated_participants = list(existing_participants)
        needs_update = False

        for target_p in participants:
            p_uid = str(target_p["user_id"])
            if p_uid not in existing_p_map:
                updated_participants.append(target_p)
                needs_update = True
            elif p_uid == worker_id:
                curr = existing_p_map[p_uid]
                if curr.get("name") != worker_name or curr.get("profile_picture") != worker_pic:
                    curr["name"] = worker_name
                    curr["profile_picture"] = worker_pic
                    needs_update = True

        if needs_update:
            update_fields["participants"] = updated_participants

        if update_fields:
            update_fields["updated_at"] = now
            await db["conversations"].update_one(
                {"_id": existing["_id"]},
                {"$set": update_fields}
            )
            existing.update(update_fields)

        return existing

    # 3. Create new direct conversation for Worker <-> Management
    conv_id = f"conv_worker_{worker_id}"
    pos = getattr(worker_user, "position", None)
    w_type = getattr(worker_user, "worker_type", None)
    if isinstance(w_type, str):
        w_type_str = w_type.capitalize()
    else:
        w_type_str = "Employee"

    subtitle = custom_subtitle or (f"{w_type_str} • {pos}" if pos else "Worker Direct Chat")
    title = custom_title or worker_name

    conv_doc = {
        "_id": conv_id,
        "id": conv_id,
        "type": "direct_worker",
        "title": title,
        "subtitle": subtitle,
        "shift_id": None,
        "cleaning_plan_id": None,
        "avatar_url": worker_pic,
        "participants": participants,
        "last_message": None,
        "unread_counts": {str(p["user_id"]): 0 for p in participants},
        "seen_by_management": [],
        "created_at": now,
        "updated_at": now
    }

    await db["conversations"].insert_one(conv_doc)
    return conv_doc


async def get_or_create_client_admin_conversation(
    db,
    client_user: UserInDB,
    custom_title: Optional[str] = None,
    custom_subtitle: Optional[str] = None
) -> dict:
    """
    Creates or retrieves the unified Direct Management conversation for a Client.
    Includes the Client and all active Admins & Managers.
    Idempotent: Re-calling always returns the existing conversation thread.
    """
    client_id = get_user_id(client_user)
    client_name = getattr(client_user, "company_name", None) or getattr(client_user, "full_name", None) or "Client"
    client_pic = getattr(client_user, "profile_photo", None) or getattr(client_user, "profile_picture", None)

    # 1. Fetch all active Admins & Managers from database
    admin_mgr_cursor = db["users"].find({
        "role": {"$in": ["admin", "manager", RoleEnum.admin, RoleEnum.manager]},
        "is_active": True
    })
    admin_mgr_users = await admin_mgr_cursor.to_list(length=100)

    participants = [{
        "user_id": client_id,
        "name": client_name,
        "role": "client",
        "profile_picture": client_pic
    }]

    seen_uids = {client_id}
    for adm in admin_mgr_users:
        u_id = str(adm.get("_id") or adm.get("id"))
        if u_id not in seen_uids:
            seen_uids.add(u_id)
            participants.append({
                "user_id": u_id,
                "name": adm.get("full_name", "Manager"),
                "role": str(adm.get("role", "manager")).lower(),
                "profile_picture": adm.get("profile_photo")
            })

    if len(participants) == 1:
        participants.append({
            "user_id": "admin_1",
            "name": "Admin Support",
            "role": "admin",
            "profile_picture": None
        })

    # 2. Check for existing direct client conversation
    existing = await db["conversations"].find_one({
        "$or": [
            {"_id": f"conv_client_{client_id}"},
            {"id": f"conv_client_{client_id}"},
            {
                "type": {"$in": ["direct", "direct_client", "direct clients", "Direct clients"]},
                "cleaning_plan_id": {"$in": [None, ""]},
                "shift_id": {"$in": [None, ""]},
                "participants.user_id": client_id
            }
        ]
    })

    now = datetime.now(timezone.utc)

    if existing:
        update_fields = {}
        if existing.get("type") not in ["direct_client", "direct clients", "Direct clients"]:
            update_fields["type"] = "direct_client"

        existing_participants = existing.get("participants", [])
        existing_p_map = {str(p.get("user_id")): p for p in existing_participants if p.get("user_id")}
        updated_participants = list(existing_participants)
        needs_update = False

        for target_p in participants:
            p_uid = str(target_p["user_id"])
            if p_uid not in existing_p_map:
                updated_participants.append(target_p)
                needs_update = True
            elif p_uid == client_id:
                curr = existing_p_map[p_uid]
                if curr.get("name") != client_name or curr.get("profile_picture") != client_pic:
                    curr["name"] = client_name
                    curr["profile_picture"] = client_pic
                    needs_update = True

        if needs_update:
            update_fields["participants"] = updated_participants

        if update_fields:
            update_fields["updated_at"] = now
            await db["conversations"].update_one(
                {"_id": existing["_id"]},
                {"$set": update_fields}
            )
            existing.update(update_fields)

        return existing

    # 3. Create new direct conversation for Client <-> Management
    conv_id = f"conv_client_{client_id}"
    comp = getattr(client_user, "company_name", None)
    subtitle = custom_subtitle or (f"Client • {comp}" if comp else "Direct Client Support")
    title = custom_title or client_name

    conv_doc = {
        "_id": conv_id,
        "id": conv_id,
        "type": "direct_client",
        "title": title,
        "subtitle": subtitle,
        "shift_id": None,
        "cleaning_plan_id": None,
        "avatar_url": client_pic,
        "participants": participants,
        "last_message": None,
        "unread_counts": {str(p["user_id"]): 0 for p in participants},
        "seen_by_management": [],
        "created_at": now,
        "updated_at": now
    }

    await db["conversations"].insert_one(conv_doc)
    return conv_doc


async def mark_conversation_read_shared_management(
    db,
    conversation_id: str,
    reader_user: UserInDB
) -> dict:
    """
    Marks messages in a conversation as read and records rich footprints.
    For Admins & Managers, clears unread counts for all Admins & Managers (Shared Inbox) and tracks seen_by_management footprint.
    For Workers / Clients, clears unread count for that specific user.
    """
    reader_id = get_user_id(reader_user)
    raw_role = getattr(reader_user, "role", "worker")
    reader_role = (raw_role.value if hasattr(raw_role, "value") else str(raw_role)).lower()
    reader_name = getattr(reader_user, "full_name", None) or getattr(reader_user, "name", "User")
    reader_pic = getattr(reader_user, "profile_photo", None) or getattr(reader_user, "profile_picture", None)
    now = datetime.now(timezone.utc)

    reader_footprint = {
        "user_id": reader_id,
        "name": reader_name,
        "role": reader_role,
        "profile_picture": reader_pic,
        "read_at": now
    }

    # 1. Update read_by list in chat_messages with full footprint
    await db["chat_messages"].update_many(
        {"conversation_id": conversation_id, "read_by.user_id": {"$ne": reader_id}},
        {"$push": {"read_by": reader_footprint}}
    )

    # 2. Update unread_counts & seen_by_management in conversations
    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if conv_doc:
        if "admin" in reader_role or "manager" in reader_role:
            admin_mgr_uids = [
                str(p.get("user_id")) for p in conv_doc.get("participants", [])
                if str(p.get("role", "")).lower() in ["admin", "manager"]
            ]
            if reader_id not in admin_mgr_uids:
                admin_mgr_uids.append(reader_id)

            existing_seen = [s for s in conv_doc.get("seen_by_management", []) if str(s.get("user_id")) != reader_id]
            existing_seen.append(reader_footprint)

            unset_or_zero = {f"unread_counts.{uid}": 0 for uid in admin_mgr_uids}
            await db["conversations"].update_one(
                {"_id": conv_doc["_id"]},
                {"$set": {**unset_or_zero, "seen_by_management": existing_seen}}
            )
        else:
            await db["conversations"].update_one(
                {"_id": conv_doc["_id"]},
                {"$set": {f"unread_counts.{reader_id}": 0}}
            )

    # 3. Broadcast WebSocket read receipt
    from app.services.chat_ws_service import broadcast_messages_read
    await broadcast_messages_read(db, conversation_id, reader_id, now.isoformat())

    return {"message": "Messages marked as read"}


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

    # 1. Add All Managers and Admin
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
            "seen_by_management": [],
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
            "seen_by_management": [],
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
    """Adds one or more users to an existing group conversation."""
    from fastapi import HTTPException
    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv_doc.get("type") not in ["group", "cleaning_plan_group"]:
        raise HTTPException(status_code=400, detail="Cannot add participants to a direct 1-1 conversation.")

    existing_participants = conv_doc.get("participants", [])
    existing_uids = set(str(p.get("user_id")) for p in existing_participants if p.get("user_id"))

    clean_uids = [str(u).strip() for u in user_ids if str(u).strip()]
    if not clean_uids:
        raise HTTPException(status_code=400, detail="At least one valid user_id must be provided")

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
    """Removes a user from an existing group conversation."""
    from fastapi import HTTPException
    conv_doc = await db["conversations"].find_one({"$or": [{"_id": conversation_id}, {"id": conversation_id}]})
    if not conv_doc:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv_doc.get("type") not in ["group", "cleaning_plan_group"]:
        raise HTTPException(status_code=400, detail="Cannot remove participants from a direct 1-1 conversation.")

    existing_participants = conv_doc.get("participants", [])
    target_p = next((p for p in existing_participants if str(p.get("user_id")) == str(target_user_id)), None)
    if not target_p:
        raise HTTPException(status_code=404, detail=f"User '{target_user_id}' is not a participant in this conversation")

    if len(existing_participants) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last remaining participant in the conversation")

    updated_participants = [p for p in existing_participants if str(p.get("user_id")) != str(target_user_id)]
    now = datetime.now(timezone.utc)

    target_name = target_p.get("name", "User")
    actor_name = getattr(actor_user, "full_name", None) or "Admin"
    sys_msg_id = f"msg_sys_{uuid.uuid4().hex[:10]}"
    sys_msg_text = f"{actor_name} removed {target_name} from the group."

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


def _chat_route_for_role(role: str, conversation_id: str) -> str:
    """Each role has its own chat prefix, so the same conversation is opened
    from a different path depending on which app the recipient is on."""
    role = (role or "").lower()
    if role == "worker":
        return f"/worker/chat/conversations/{conversation_id}"
    if role == "client":
        return f"/client/chat/conversations/{conversation_id}"
    return f"/manager/chat/conversations/{conversation_id}"


async def notify_new_chat_message(
    db,
    conversation_id: str,
    sender_id: str,
    sender_name: str,
    message_preview: str,
    recipient_uids: List[str]
) -> None:
    """
    Creates a persistent, clickable in-app notification (route_type="chat")
    for every other participant of a conversation when a new message arrives,
    on top of the existing WebSocket broadcast + per-conversation unread badge.
    Without this, a recipient who wasn't online at send time had no record of
    the message anywhere in their notification center.
    """
    if not recipient_uids:
        return

    from app.services.notification_service import NotificationService
    notif_service = NotificationService()

    recipients = await db["users"].find(
        {"$or": build_user_id_or_query(recipient_uids)},
        {"role": 1, "onesignal_player_id": 1}
    ).to_list(length=len(recipient_uids))
    role_map = build_user_map(recipients)

    preview = (message_preview or "").strip()
    if len(preview) > 120:
        preview = preview[:117] + "..."
    if not preview:
        preview = "Sent an attachment"

    for uid in recipient_uids:
        recipient_doc = role_map.get(str(uid))
        recipient_role = (recipient_doc.get("role") if recipient_doc else None) or "worker"
        player_id = recipient_doc.get("onesignal_player_id") if recipient_doc else None
        route = _chat_route_for_role(recipient_role, conversation_id)

        await notif_service.create_notification(
            title=f"New message from {sender_name}",
            message=preview,
            notification_type="new_message",
            route_type="chat",
            recipient_type=recipient_role,
            user_id=str(uid),
            player_ids=[player_id] if player_id else None,
            data={
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "route": route,
                "deeplink": f"cleaningone://{route.lstrip('/')}"
            }
        )
