import uuid
import asyncio
from bson import ObjectId
from datetime import datetime, timezone
from typing import List, Optional, Union
from app.schemas.client_list import (
    CleaningPlanRoomDetail, CleaningPlanWorkerDetail,
    CleaningPlanClientDetail, CleaningPlanManagerDetail,
    ManagerCleaningPlanDetailResponse, ManagerCleaningPlanListItemResponse,
    CleaningTaskResponse, RequiredPhotoResponse, TaskPhotoResponse,
    PendingAdditionalTaskResponse
)
from app.models.user import UserInDB


def _format_tasks_list(raw_tasks: list) -> List[CleaningTaskResponse]:
    """
    Formats raw tasks into CleaningTaskResponse models with embedded task photos.
    Guarantees every photo is bound to its parent task.
    """
    tasks = []
    for t in (raw_tasks or []):
        if isinstance(t, dict):
            t_id = str(t.get("id") or t.get("_id") or uuid.uuid4().hex[:8])
            t_name = t.get("name", "Task")
            t_freq = t.get("frequency_type", "every_visit")
            is_req = bool(t.get("is_photo_req", False))
            t_weekly_days = t.get("weekly_days")
            t_monthly_dates = t.get("monthly_dates")
            t_fixed_date = t.get("fixed_date")
            t_duration_minutes = t.get("duration_minutes")
            t_description = t.get("description")

            raw_photos = t.get("photo") or t.get("photos") or []
            task_photos = []
            if isinstance(raw_photos, list):
                for p in raw_photos:
                    if isinstance(p, dict):
                        p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                        task_photos.append(TaskPhotoResponse(id=p_id, name=p.get("name", "Photo")))
                    elif isinstance(p, str):
                        task_photos.append(TaskPhotoResponse(id=uuid.uuid4().hex[:8], name=p))
                    elif hasattr(p, "name"):
                        p_id = str(getattr(p, "id", None) or uuid.uuid4().hex[:8])
                        task_photos.append(TaskPhotoResponse(id=p_id, name=getattr(p, "name", "Photo")))

            if task_photos and not is_req:
                is_req = True

            tasks.append(CleaningTaskResponse(
                id=t_id,
                name=t_name,
                frequency_type=t_freq,
                is_photo_req=is_req,
                photo=task_photos,
                total_photos_required=len(task_photos),
                weekly_days=t_weekly_days,
                monthly_dates=t_monthly_dates,
                fixed_date=t_fixed_date,
                duration_minutes=t_duration_minutes,
                description=t_description
            ))
        elif isinstance(t, str):
            tasks.append(CleaningTaskResponse(
                id=uuid.uuid4().hex[:8],
                name=t,
                frequency_type="every_visit",
                is_photo_req=False,
                photo=[],
                total_photos_required=0
            ))
        elif hasattr(t, "name"):
            t_id = str(getattr(t, "id", None) or uuid.uuid4().hex[:8])
            t_name = getattr(t, "name", "Task")
            t_freq = getattr(t, "frequency_type", "every_visit")
            is_req = bool(getattr(t, "is_photo_req", False))
            t_weekly_days = getattr(t, "weekly_days", None)
            t_monthly_dates = getattr(t, "monthly_dates", None)
            t_fixed_date = getattr(t, "fixed_date", None)
            t_duration_minutes = getattr(t, "duration_minutes", None)
            t_description = getattr(t, "description", None)
            raw_photos = getattr(t, "photo", []) or []
            task_photos = []
            for p in raw_photos:
                p_id = str(getattr(p, "id", None) or uuid.uuid4().hex[:8]) if not isinstance(p, dict) else str(p.get("id") or uuid.uuid4().hex[:8])
                p_name = getattr(p, "name", "Photo") if not isinstance(p, dict) else p.get("name", "Photo")
                task_photos.append(TaskPhotoResponse(id=p_id, name=p_name))
            if task_photos and not is_req:
                is_req = True
            tasks.append(CleaningTaskResponse(
                id=t_id,
                name=t_name,
                frequency_type=t_freq,
                is_photo_req=is_req,
                photo=task_photos,
                total_photos_required=len(task_photos),
                weekly_days=t_weekly_days,
                monthly_dates=t_monthly_dates,
                fixed_date=t_fixed_date,
                duration_minutes=t_duration_minutes,
                description=t_description
            ))
    return tasks


def _format_pending_tasks_list(raw_pending: list) -> List[PendingAdditionalTaskResponse]:
    """
    Formats a cleaning plan's `pending_additional_tasks` (client-submitted
    additional-task requests) into response models. Same field extraction as
    `_format_tasks_list` plus the approval-workflow fields.

    Approved entries are excluded here — once approved, a task graduates into
    the plan's real `additional_tasks[]`, so leaving it in this list too would
    show it duplicated under "pending" even though it's no longer awaiting any
    action. Rejected entries are kept (this is the only place their
    `rejection_reason` is visible).
    """
    items = []
    for t in (raw_pending or []):
        if not isinstance(t, dict):
            continue
        if t.get("status") == "approved":
            continue
        t_id = str(t.get("id") or t.get("_id") or uuid.uuid4().hex[:8])

        raw_photos = t.get("photo") or t.get("photos") or []
        task_photos = []
        if isinstance(raw_photos, list):
            for p in raw_photos:
                if isinstance(p, dict):
                    p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                    task_photos.append(TaskPhotoResponse(id=p_id, name=p.get("name", "Photo")))
                elif isinstance(p, str):
                    task_photos.append(TaskPhotoResponse(id=uuid.uuid4().hex[:8], name=p))

        items.append(PendingAdditionalTaskResponse(
            id=t_id,
            name=t.get("name", "Task"),
            frequency_type=t.get("frequency_type", "every_visit"),
            is_photo_req=bool(t.get("is_photo_req", False)),
            photo=task_photos,
            total_photos_required=len(task_photos),
            weekly_days=t.get("weekly_days"),
            monthly_dates=t.get("monthly_dates"),
            fixed_date=t.get("fixed_date"),
            duration_minutes=t.get("duration_minutes"),
            description=t.get("description"),
            status=t.get("status", "pending"),
            requested_by=t.get("requested_by"),
            requested_by_name=t.get("requested_by_name"),
            requested_at=t.get("requested_at"),
            reviewed_by=t.get("reviewed_by"),
            reviewed_by_name=t.get("reviewed_by_name"),
            reviewed_at=t.get("reviewed_at"),
            rejection_reason=t.get("rejection_reason")
        ))
    return items


def _format_photos_list(raw_photos: list) -> List[RequiredPhotoResponse]:
    photos = []
    for p in (raw_photos or []):
        if isinstance(p, dict):
            p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
            photos.append(RequiredPhotoResponse(id=p_id, name=p.get("name", "Photo"), frequency_type=p.get("frequency_type", "every_visit")))
        elif isinstance(p, str):
            photos.append(RequiredPhotoResponse(id=uuid.uuid4().hex[:8], name=p, frequency_type="every_visit"))
        elif hasattr(p, "name"):
            photos.append(RequiredPhotoResponse(id=str(getattr(p, "id", None) or uuid.uuid4().hex[:8]), name=getattr(p, "name", "Photo"), frequency_type=getattr(p, "frequency_type", "every_visit")))
    return photos


async def _resolve_rooms_data(room_ids: List[str], db) -> List[CleaningPlanRoomDetail]:
    if not room_ids:
        return []
    cursor = db["rooms"].find({"$or": [{"_id": {"$in": room_ids}}, {"id": {"$in": room_ids}}, {"room_id": {"$in": room_ids}}]})
    raw_rooms = await cursor.to_list(length=len(room_ids) * 2 + 10)

    room_map = {}
    for r in raw_rooms:
        for k in (r.get("_id"), r.get("id"), r.get("room_id")):
            if k:
                room_map[str(k)] = r

    room_details = []
    for rid in room_ids:
        r = room_map.get(str(rid))
        if not r:
            continue

        r_name = r.get("room_name") or r.get("name", "Room")
        r_type = r.get("room_type") or r.get("type", "standard")
        flr = r.get("floor", 1)
        dur = r.get("duration") or r.get("est_cleaning_duration_minutes", 30)
        freq = r.get("monthly_cleaning_frequency", 0)
        c_type = r.get("clean_type") or r.get("cleaning_type", "standard")

        room_tasks = _format_tasks_list(r.get("tasks", []))
        total_p_req = sum(len(t.photo) for t in room_tasks)

        room_details.append(CleaningPlanRoomDetail(
            room_id=str(rid),
            room_name=r_name,
            room_type=r_type,
            floor=flr,
            duration=dur,
            monthly_cleaning_frequency=freq,
            clean_type=c_type,
            tasks=room_tasks,
            photo_number=total_p_req,
            total_photos_required=total_p_req,
            task_number=len(room_tasks)
        ))

    return room_details


async def _resolve_clients_data(rooms_data: List[CleaningPlanRoomDetail], db) -> List[CleaningPlanClientDetail]:
    if not rooms_data:
        return []

    room_ids = [r.room_id for r in rooms_data]
    cursor = db["rooms"].find({"$or": [{"_id": {"$in": room_ids}}, {"id": {"$in": room_ids}}, {"room_id": {"$in": room_ids}}]})
    raw_rooms = await cursor.to_list(length=len(room_ids) * 2 + 10)

    # Collect location IDs for rooms without direct client_id
    missing_loc_ids = [r.get("location_id") for r in raw_rooms if not r.get("client_id") and r.get("location_id")]
    loc_client_map = {}
    if missing_loc_ids:
        cursor_loc = db["locations"].find({"$or": [{"_id": {"$in": missing_loc_ids}}, {"id": {"$in": missing_loc_ids}}]})
        raw_locs = await cursor_loc.to_list(length=len(missing_loc_ids) * 2 + 10)
        for loc in raw_locs:
            c_val = str(loc.get("client_id") or "")
            for lk in (loc.get("_id"), loc.get("id")):
                if lk:
                    loc_client_map[str(lk)] = c_val

    room_client_map = {}
    for r in raw_rooms:
        cid = str(r.get("client_id") or "")
        if not cid:
            loc_id = str(r.get("location_id") or "")
            cid = loc_client_map.get(loc_id, "")
        for k in (r.get("_id"), r.get("id"), r.get("room_id")):
            if k:
                room_client_map[str(k)] = cid

    client_room_counts = {}
    client_ids_order = []
    for r in rooms_data:
        cid = room_client_map.get(r.room_id)
        if not cid:
            continue
        if cid not in client_room_counts:
            client_room_counts[cid] = 0
            client_ids_order.append(cid)
        client_room_counts[cid] += 1

    if not client_ids_order:
        return []

    cursor_c = db["client_list"].find({"$or": [{"_id": {"$in": client_ids_order}}, {"id": {"$in": client_ids_order}}]})
    raw_clients = await cursor_c.to_list(length=len(client_ids_order) * 2 + 10)

    found_cids = set()
    client_map = {}
    for c in raw_clients:
        for k in (c.get("_id"), c.get("id")):
            if k:
                client_map[str(k)] = c
                found_cids.add(str(k))

    # Fallback to users collection for client details
    missing_cids = [cid for cid in client_ids_order if cid not in found_cids]
    if missing_cids:
        cursor_u = db["users"].find({"$or": [{"_id": {"$in": missing_cids}}, {"id": {"$in": missing_cids}}]})
        raw_users = await cursor_u.to_list(length=len(missing_cids) * 2 + 10)
        for u in raw_users:
            u_dict = {
                "_id": str(u.get("_id") or u.get("id")),
                "company_name": u.get("company_name") or u.get("full_name") or "Client Company",
                "primary_contact_name": u.get("full_name") or "",
                "email": u.get("email") or "",
                "phone": u.get("phone") or ""
            }
            for k in (u.get("_id"), u.get("id")):
                if k:
                    client_map[str(k)] = u_dict

    clients_list = []
    for cid in client_ids_order:
        c_doc = client_map.get(cid) or {}
        clients_list.append(CleaningPlanClientDetail(
            client_id=cid,
            company_name=c_doc.get("company_name") or "Client Company",
            primary_contact_name=c_doc.get("primary_contact_name", ""),
            email=c_doc.get("email", ""),
            phone=c_doc.get("phone", ""),
            rooms_count=client_room_counts.get(cid, 0)
        ))

    return clients_list


async def _resolve_manager_data(manager_id: Optional[str], db, fallback_user: Optional[UserInDB] = None) -> Optional[CleaningPlanManagerDetail]:
    if manager_id:
        user_doc = await db["users"].find_one({"$or": [{"_id": manager_id}, {"id": manager_id}]})
        if not user_doc:
            try:
                from bson import ObjectId
                if ObjectId.is_valid(manager_id):
                    user_doc = await db["users"].find_one({"_id": ObjectId(manager_id)})
            except Exception:
                pass
        if user_doc:
            pic = user_doc.get("profile_photo") or user_doc.get("profile_picture") or user_doc.get("avatar_url")
            return CleaningPlanManagerDetail(
                manager_id=str(user_doc.get("_id") or user_doc.get("id")),
                name=user_doc.get("full_name") or user_doc.get("name") or "Manager",
                email=user_doc.get("email", ""),
                role=user_doc.get("role", "manager"),
                phone=user_doc.get("phone"),
                profile_photo=pic
            )
    if fallback_user:
        pic = getattr(fallback_user, "profile_photo", None) or getattr(fallback_user, "profile_picture", None) or getattr(fallback_user, "avatar_url", None)
        return CleaningPlanManagerDetail(
            manager_id=str(fallback_user.id or fallback_user.email),
            name=getattr(fallback_user, "full_name", None) or getattr(fallback_user, "name", "Manager"),
            email=getattr(fallback_user, "email", ""),
            role=getattr(fallback_user, "role", "manager"),
            phone=getattr(fallback_user, "phone", None),
            profile_photo=pic
        )
    return None


async def _resolve_workers_data(worker_ids: List[str], db, assigned_workers_meta: Optional[List[dict]] = None) -> List[CleaningPlanWorkerDetail]:
    if not worker_ids:
        return []
    clean_wids = [str(w).strip() for w in worker_ids if str(w).strip()]
    obj_ids = [ObjectId(w) for w in clean_wids if ObjectId.is_valid(w)]
    or_clauses = [{"id": {"$in": clean_wids}}]
    if obj_ids:
        or_clauses.append({"_id": {"$in": obj_ids}})
    or_clauses.append({"_id": {"$in": clean_wids}})

    cursor = db["users"].find({"$or": or_clauses})
    raw_workers = await cursor.to_list(length=len(clean_wids) * 2)

    meta_positions = {}
    if assigned_workers_meta:
        for aw in assigned_workers_meta:
            if isinstance(aw, dict):
                aw_id = str(aw.get("worker_id") or "")
                if aw_id:
                    meta_positions[aw_id] = aw.get("position", "normal")

    workers = []
    found_wids = set()
    for w in raw_workers:
        wid = str(w.get("_id") or w.get("id"))
        found_wids.add(wid)
        w_name = w.get("full_name") or w.get("name") or "Worker"
        w_type = w.get("worker_type") or "employee"
        pos = meta_positions.get(wid) or w.get("position") or "normal"
        workers.append(CleaningPlanWorkerDetail(
            worker_id=wid,
            name=w_name,
            email=w.get("email"),
            role=w.get("role", "worker"),
            worker_type=w_type,
            position=pos,
            phone=w.get("phone"),
            profile_photo=w.get("profile_photo") or w.get("profile_picture") or w.get("avatar_url")
        ))

    if assigned_workers_meta:
        for aw in assigned_workers_meta:
            if isinstance(aw, dict):
                aw_id = str(aw.get("worker_id") or aw.get("id") or "")
                if aw_id and aw_id not in found_wids:
                    workers.append(CleaningPlanWorkerDetail(
                        worker_id=aw_id,
                        name=aw.get("name") or aw.get("full_name") or "Worker",
                        email=aw.get("email"),
                        role=aw.get("role", "worker"),
                        worker_type=aw.get("worker_type", "employee"),
                        position=aw.get("position", "normal"),
                        phone=aw.get("phone"),
                        profile_photo=aw.get("profile_photo") or aw.get("profile_picture")
                    ))
                    found_wids.add(aw_id)

    return workers


def _calculate_end_time(start_time_str: str, duration_minutes: int) -> str:
    if not start_time_str:
        return "04:00 PM"
    start_time_str = start_time_str.strip()
    is_12h = False
    st_upper = start_time_str.upper()
    if "AM" in st_upper or "PM" in st_upper:
        is_12h = True
        clean_str = st_upper.replace(" ", "")
        try:
            dt = datetime.strptime(clean_str, "%I:%M%p")
            start_mins = dt.hour * 60 + dt.minute
        except Exception:
            try:
                dt = datetime.strptime(start_time_str, "%I:%M %p")
                start_mins = dt.hour * 60 + dt.minute
            except Exception:
                start_mins = 8 * 60
    else:
        try:
            parts = start_time_str.split(":")
            start_mins = int(parts[0]) * 60 + int(parts[1])
        except Exception:
            start_mins = 8 * 60

    end_mins = (start_mins + duration_minutes) % (24 * 60)
    end_hour = end_mins // 60
    end_minute = end_mins % 60

    if is_12h:
        period = "AM" if end_hour < 12 else "PM"
        hour_12 = end_hour % 12
        if hour_12 == 0:
            hour_12 = 12
        return f"{hour_12:02d}:{end_minute:02d} {period}"
    else:
        return f"{end_hour:02d}:{end_minute:02d}"


async def _format_manager_cleaning_plan_detail(doc: dict, db, current_user: Optional[UserInDB] = None) -> ManagerCleaningPlanDetailResponse:
    pid = str(doc.get("_id") or doc.get("id"))
    title = doc.get("title") or doc.get("plan_name", "Cleaning Plan")
    desc = doc.get("description", "")
    shift_notes_val = doc.get("shift_notes") or desc
    manager_id = doc.get("manager_id")

    room_ids = doc.get("room_ids", [])
    worker_ids = doc.get("worker_ids", [])

    # Fetch room, client, worker, manager details
    rooms_data = await _resolve_rooms_data(room_ids, db)
    clients_data = await _resolve_clients_data(rooms_data, db)
    workers_data = await _resolve_workers_data(worker_ids, db, assigned_workers_meta=doc.get("assigned_workers", []))
    manager_data = await _resolve_manager_data(manager_id, db, fallback_user=current_user)

    # Format additional tasks (with task-connected photos)
    raw_add_tasks = doc.get("additional_tasks", []) or doc.get("tasks", [])
    add_tasks = _format_tasks_list(raw_add_tasks)
    pending_add_tasks = _format_pending_tasks_list(doc.get("pending_additional_tasks", []))

    # Compute accurate task & photo counts
    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)
    add_tasks_count = len(add_tasks)
    add_photos_count = sum(len(t.photo) for t in add_tasks)

    t_cnt = room_tasks_count + add_tasks_count
    p_cnt = room_photos_count + add_photos_count
    dur_mins = doc.get("duration_minutes") or (sum(r.duration for r in rooms_data) if rooms_data else 60)

    working_days_val = doc.get("working_days") or doc.get("frequency") or []
    if isinstance(working_days_val, str):
        working_days_val = [working_days_val]
    date_val = doc.get("date") or "2026-08-17"
    start_time_val = doc.get("start_time") or "08:00 AM"
    end_time_val = doc.get("end_time") or _calculate_end_time(start_time_val, dur_mins)
    repeat_shift_val = doc.get("repeat_shift") or "Standard working week"
    repeat_until_val = doc.get("repeat_until")

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    primary_cid = clients_data[0].client_id if clients_data else str(doc.get("client_id") or "")
    primary_cname = clients_data[0].company_name if clients_data else str(doc.get("company_name") or "")

    # Was accepted on create/update and stored on the plan doc, but never returned — fix.
    location_id_val = doc.get("location_id") or None
    location_name_val = doc.get("location_name") or None
    if location_id_val and not location_name_val:
        loc_doc = await db["locations"].find_one({"$or": [{"_id": location_id_val}, {"id": location_id_val}]})
        if loc_doc:
            location_name_val = loc_doc.get("name")

    return ManagerCleaningPlanDetailResponse(
        id=pid,
        title=title,
        shift_notes=shift_notes_val,
        client_id=primary_cid,
        company_name=primary_cname,
        clients_count=len(clients_data),
        clients=clients_data,
        manager=manager_data,
        location_id=location_id_val,
        location_name=location_name_val,
        room_ids=room_ids,
        rooms=rooms_data,
        rooms_count=len(rooms_data),
        worker_ids=worker_ids,
        workers=workers_data,
        workers_count=len(workers_data),
        additional_tasks=add_tasks,
        pending_additional_tasks=pending_add_tasks,
        total_tasks_count=t_cnt,
        total_photos_count=p_cnt,
        date=date_val,
        start_time=start_time_val,
        end_time=end_time_val,
        duration_minutes=dur_mins,
        repeat_shift=repeat_shift_val,
        repeat_until=repeat_until_val,
        working_days=working_days_val,
        timezone=doc.get("timezone", "Europe/Amsterdam"),
        status=doc.get("status", "draft"),
        is_active=doc.get("is_active", True),
        created_at=c_at,
        updated_at=u_at
    )


async def _format_manager_cleaning_plan_list_item(doc: dict, db) -> ManagerCleaningPlanListItemResponse:
    pid = str(doc.get("_id") or doc.get("id"))
    title = doc.get("title") or doc.get("plan_name", "Cleaning Plan")

    room_ids = doc.get("room_ids", [])
    worker_ids = doc.get("worker_ids", [])

    # Fetch room details & clients
    rooms_data = await _resolve_rooms_data(room_ids, db)
    clients_data = await _resolve_clients_data(rooms_data, db)
    client_names = [c.company_name for c in clients_data]

    # Fetch room names
    room_names = [r.room_name for r in rooms_data]

    # Fetch worker names
    worker_names = []
    if worker_ids:
        clean_wids = [str(w).strip() for w in worker_ids if str(w).strip()]
        obj_ids = [ObjectId(w) for w in clean_wids if ObjectId.is_valid(w)]
        or_clauses = [{"id": {"$in": clean_wids}}]
        if obj_ids:
            or_clauses.append({"_id": {"$in": obj_ids}})
        or_clauses.append({"_id": {"$in": clean_wids}})

        cursor_w = db["users"].find({"$or": or_clauses})
        workers_found = await cursor_w.to_list(length=len(clean_wids) * 2)
        worker_names = [w.get("full_name") or w.get("name", "Worker") for w in workers_found]

    # Calculate task and photo counts
    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)
    raw_add_tasks = doc.get("additional_tasks", []) or doc.get("tasks", [])
    add_tasks = _format_tasks_list(raw_add_tasks)
    add_tasks_count = len(add_tasks)
    add_photos_count = sum(len(t.photo) for t in add_tasks)

    t_cnt = room_tasks_count + add_tasks_count
    p_cnt = room_photos_count + add_photos_count

    dur_mins = doc.get("duration_minutes") or (sum(r.duration for r in rooms_data) if rooms_data else 60)
    working_days_val = doc.get("working_days") or doc.get("frequency") or []
    if isinstance(working_days_val, str):
        working_days_val = [working_days_val]
    date_val = doc.get("date") or "2026-08-17"
    start_time_val = doc.get("start_time") or "08:00 AM"
    end_time_val = doc.get("end_time") or _calculate_end_time(start_time_val, dur_mins)
    repeat_shift_val = doc.get("repeat_shift") or "Standard working week"
    repeat_until_val = doc.get("repeat_until")

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ManagerCleaningPlanListItemResponse(
        id=pid,
        title=title,
        location_id=doc.get("location_id") or None,
        location_name=doc.get("location_name") or None,
        clients_count=len(clients_data),
        client_names=client_names,
        rooms_count=len(room_ids),
        room_names=room_names,
        workers_count=len(worker_ids),
        worker_names=worker_names,
        total_tasks_count=t_cnt,
        total_photos_count=p_cnt,
        date=date_val,
        start_time=start_time_val,
        end_time=end_time_val,
        duration_minutes=dur_mins,
        repeat_shift=repeat_shift_val,
        repeat_until=repeat_until_val,
        working_days=working_days_val,
        timezone=doc.get("timezone", "Europe/Amsterdam"),
        status=doc.get("status", "draft"),
        is_active=doc.get("is_active", True),
        created_at=c_at,
        updated_at=u_at
    )


async def batch_format_manager_cleaning_plan_list_items(raw_plans: list, db) -> List[ManagerCleaningPlanListItemResponse]:
    """
    High-performance batch formatter that eliminates N+1 query loops.
    Pre-fetches all rooms, workers, locations, and clients in 2 parallel query rounds,
    reducing 50-60 DB queries down to 4 parallel queries.
    """
    if not raw_plans:
        return []

    # 1. Collect all room IDs and worker IDs
    all_room_ids = set()
    all_worker_ids = set()
    for p in raw_plans:
        for rid in p.get("room_ids", []):
            if rid:
                all_room_ids.add(str(rid))
        for wid in p.get("worker_ids", []):
            if wid:
                all_worker_ids.add(str(wid))

    # 2. Parallel fetch rooms and workers
    room_or = []
    if all_room_ids:
        r_list = list(all_room_ids)
        r_oids = [ObjectId(x) for x in r_list if ObjectId.is_valid(x)]
        room_or = [{"_id": {"$in": r_list}}, {"id": {"$in": r_list}}, {"room_id": {"$in": r_list}}]
        if r_oids:
            room_or.append({"_id": {"$in": r_oids}})

    worker_or = []
    if all_worker_ids:
        w_list = list(all_worker_ids)
        w_oids = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
        worker_or = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
        if w_oids:
            worker_or.append({"_id": {"$in": w_oids}})

    raw_rooms, raw_workers = await asyncio.gather(
        db["rooms"].find({"$or": room_or}).to_list(length=1000) if room_or else asyncio.sleep(0, result=[]),
        db["users"].find({"$or": worker_or}).to_list(length=1000) if worker_or else asyncio.sleep(0, result=[])
    )

    # Index rooms and workers in memory maps
    rooms_map = {}
    location_ids_needed = set()
    for r in raw_rooms:
        for k in (r.get("_id"), r.get("id"), r.get("room_id")):
            if k:
                rooms_map[str(k)] = r
        if r.get("location_id"):
            location_ids_needed.add(str(r["location_id"]))

    workers_map = {}
    for w in raw_workers:
        for k in (w.get("_id"), w.get("id")):
            if k:
                workers_map[str(k)] = w.get("full_name") or w.get("name", "Worker")

    # 3. Fetch referenced locations & clients in parallel
    locations_map = {}
    clients_map = {}
    if location_ids_needed:
        loc_list = list(location_ids_needed)
        loc_oids = [ObjectId(x) for x in loc_list if ObjectId.is_valid(x)]
        loc_or = [{"_id": {"$in": loc_list}}, {"id": {"$in": loc_list}}]
        if loc_oids:
            loc_or.append({"_id": {"$in": loc_oids}})
        loc_docs = await db["locations"].find({"$or": loc_or}).to_list(length=1000)
        client_ids_needed = set()
        for l in loc_docs:
            for k in (l.get("_id"), l.get("id")):
                if k:
                    locations_map[str(k)] = l
            if l.get("client_id"):
                client_ids_needed.add(str(l["client_id"]))

        if client_ids_needed:
            cli_list = list(client_ids_needed)
            cli_oids = [ObjectId(x) for x in cli_list if ObjectId.is_valid(x)]
            cli_or = [{"_id": {"$in": cli_list}}, {"id": {"$in": cli_list}}]
            if cli_oids:
                cli_or.append({"_id": {"$in": cli_oids}})
            cli_docs = await db["client_list"].find({"$or": cli_or}).to_list(length=1000)
            for c in cli_docs:
                for k in (c.get("_id"), c.get("id")):
                    if k:
                        clients_map[str(k)] = c.get("company_name", "Client")

    # 4. Format all plans synchronously in memory
    results = []
    for doc in raw_plans:
        pid = str(doc.get("_id") or doc.get("id"))
        title = doc.get("title") or doc.get("plan_name", "Cleaning Plan")
        p_room_ids = doc.get("room_ids", [])
        p_worker_ids = doc.get("worker_ids", [])

        # Resolve rooms
        plan_rooms = [rooms_map[str(rid)] for rid in p_room_ids if str(rid) in rooms_map]
        room_names = [r.get("name", "Room") for r in plan_rooms]

        # Resolve clients
        plan_client_names = set()
        for r in plan_rooms:
            lid = str(r.get("location_id") or "")
            loc_doc = locations_map.get(lid)
            if loc_doc:
                cid = str(loc_doc.get("client_id") or "")
                cname = clients_map.get(cid)
                if cname:
                    plan_client_names.add(cname)
        if not plan_client_names:
            cname = doc.get("client_name") or doc.get("company_name")
            if cname:
                plan_client_names.add(cname)

        # Resolve workers
        worker_names = [workers_map[str(wid)] for wid in p_worker_ids if str(wid) in workers_map]

        # Count tasks and photos
        room_tasks_count = sum(len(r.get("tasks", [])) for r in plan_rooms)
        room_photos_count = sum(sum(len(t.get("photo", []) or t.get("photos", [])) for t in r.get("tasks", [])) for r in plan_rooms)

        raw_add_tasks = doc.get("additional_tasks", []) or doc.get("tasks", [])
        add_tasks = _format_tasks_list(raw_add_tasks)
        add_tasks_count = len(add_tasks)
        add_photos_count = sum(len(t.photo) for t in add_tasks)

        t_cnt = room_tasks_count + add_tasks_count
        p_cnt = room_photos_count + add_photos_count

        dur_mins = doc.get("duration_minutes") or (sum(r.get("est_cleaning_duration_minutes", 30) for r in plan_rooms) if plan_rooms else 60)
        working_days_val = doc.get("working_days") or doc.get("frequency") or []
        if isinstance(working_days_val, str):
            working_days_val = [working_days_val]
        date_val = doc.get("date") or "2026-08-17"
        start_time_val = doc.get("start_time") or "08:00 AM"
        end_time_val = doc.get("end_time") or _calculate_end_time(start_time_val, dur_mins)
        repeat_shift_val = doc.get("repeat_shift") or "Standard working week"
        repeat_until_val = doc.get("repeat_until")

        c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
        u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

        results.append(ManagerCleaningPlanListItemResponse(
            id=pid,
            title=title,
            location_id=doc.get("location_id") or None,
            location_name=doc.get("location_name") or None,
            clients_count=len(plan_client_names),
            client_names=list(plan_client_names),
            rooms_count=len(p_room_ids),
            room_names=room_names,
            workers_count=len(p_worker_ids),
            worker_names=worker_names,
            total_tasks_count=t_cnt,
            total_photos_count=p_cnt,
            date=date_val,
            start_time=start_time_val,
            end_time=end_time_val,
            duration_minutes=dur_mins,
            repeat_shift=repeat_shift_val,
            repeat_until=repeat_until_val,
            working_days=working_days_val,
            timezone=doc.get("timezone", "Europe/Amsterdam"),
            status=doc.get("status", "draft"),
            is_active=doc.get("is_active", True),
            created_at=c_at,
            updated_at=u_at
        ))

    return results
