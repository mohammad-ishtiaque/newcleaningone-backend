import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.extra_services_helper import (
    format_extra_service_response, format_extra_service_list_item, finalize_extra_service_completion
)
from app.schemas.extra_services import (
    ExtraServiceApproveRequest, ExtraServiceRejectRequest,
    ExtraServiceResponse, ExtraServicePaginatedResponse,
    AssignWorkersToExtraServiceRequest, ExtraServiceWorkerDropdownPaginatedResponse
)
from app.schemas.client_list import (
    CleaningPlanWorkerDropdownItem, AssignWorkersToCleaningPlanRequest
)
from app.api.admin.profile_company import require_manager
from app.api.admin.cleaning_plan_worker_utils import (
    parse_time_to_minutes, normalize_worker_position,
    batch_compute_worker_metrics, sort_workers_by_suitability,
    check_worker_time_availability, send_cleaning_plan_assignment_notifications
)

router = APIRouter(prefix="/manager/extra-services", tags=["Manager Extra Service Management"])


@router.get(
    "",
    response_model=ExtraServicePaginatedResponse,
    summary="Admin List Extra Service Requests",
    description="""
### Admin / Manager List Extra Service Requests (Paginated)
Returns a paginated list of extra service requests across all clients for Manager review, with status, client, and search filters.
"""
)
async def list_admin_extra_services(
    status_val: Optional[str] = None,
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin List Extra Services Endpoint.
    """
    db = get_database()
    query = {}

    if status_val and status_val.lower() != "all":
        query["status"] = status_val.lower()

    if client_id:
        query["client_id"] = client_id

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"title": search_regex},
            {"client_name": search_regex},
            {"description": search_regex}
        ]

    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [format_extra_service_list_item(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get(
    "/{request_id}",
    response_model=ExtraServiceResponse,
    summary="Admin Get Single Extra Service Request",
    description="""
### Admin Get Single Extra Service Request Detail
Retrieves detailed information for a single extra service request by ID including hierarchical tasks, photos, and assigned workers.
"""
)
async def get_admin_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Get Single Extra Service Detail Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return format_extra_service_response(doc)


@router.post(
    "/{request_id}/reject",
    response_model=ExtraServiceResponse,
    summary="Admin Reject Extra Service Request",
    description="""
### Admin Reject Extra Service Request
Rejects an extra service request with a specified rejection reason.
"""
)
async def reject_extra_service_request(
    request_id: str,
    reject_in: ExtraServiceRejectRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Reject Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    now = datetime.now(timezone.utc)
    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "rejected",
            "rejection_reason": reject_in.reason,
            "updated_at": now
        }}
    )
    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/approve",
    response_model=ExtraServiceResponse,
    summary="Admin Approve Extra Service & Assign Worker",
    description="""
### Admin Approve Extra Service Request
Approves an extra service request, sets estimated hours, and optionally assigns workers with positions (`teamleader`, `co_leader`, `normal`).
"""
)
async def approve_extra_service_request(
    request_id: str,
    approve_in: ExtraServiceApproveRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Approve Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    # Parse worker items with position
    worker_map = {}
    if approve_in.workers:
        for w in approve_in.workers:
            wid = str(w.worker_id).strip()
            if wid:
                worker_map[wid] = normalize_worker_position(w.position)
    elif approve_in.worker_ids:
        for wid_raw in approve_in.worker_ids:
            wid = str(wid_raw).strip()
            if wid:
                worker_map[wid] = "normal"

    assigned_workers = []
    if worker_map:
        w_ids = list(worker_map.keys())
        obj_ids = [ObjectId(w) for w in w_ids if ObjectId.is_valid(w)]
        or_clauses = [{"id": {"$in": w_ids}}, {"_id": {"$in": w_ids}}]
        if obj_ids:
            or_clauses.append({"_id": {"$in": obj_ids}})

        cursor = db["users"].find({"$or": or_clauses})
        found_users = await cursor.to_list(length=len(w_ids) * 2)
        found_map = {}
        for u in found_users:
            if "_id" in u:
                found_map[str(u["_id"])] = u
            if "id" in u and u["id"]:
                found_map[str(u["id"])] = u

        for wid, pos in worker_map.items():
            u_doc = found_map.get(wid) or {}
            assigned_workers.append({
                "worker_id": wid,
                "name": u_doc.get("full_name") or u_doc.get("name") or "Worker",
                "role": "worker",
                "worker_type": u_doc.get("worker_type", "employee"),
                "position": pos,
                "profile_photo": u_doc.get("profile_photo") or u_doc.get("profile_picture")
            })

    photo_requirements = []
    for p_name in (approve_in.required_photos or []):
        photo_requirements.append({
            "id": f"p_{uuid.uuid4().hex[:6]}",
            "name": p_name,
            "photo_url": None,
            "is_uploaded": False,
            "uploaded_at": None
        })

    now = datetime.now(timezone.utc)
    update_payload = {
        "status": "approved",
        "estimated_hours": approve_in.estimated_hours,
        "updated_at": now
    }
    if assigned_workers:
        update_payload["assigned_workers"] = assigned_workers
    if photo_requirements:
        update_payload["required_photos"] = photo_requirements

    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": update_payload}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.get(
    "/{request_id}/workers-dropdown",
    response_model=ExtraServiceWorkerDropdownPaginatedResponse,
    summary="Get Worker Dropdown for Extra Service Assignment",
    description="""
### Worker Dropdown List for Extra Service Assignment
Retrieves a paginated list of workers with real-time availability and monthly average work metrics for the target extra service date.

#### Path Parameter:
- **`request_id`** (`str`, **Required**): Target extra service request ID.

#### Query Parameters:
- **`search`** (`str`, *Optional*): Filter by worker name, email, phone, or position.
- **`worker_type`** (`str`, *Optional*): Filter by `all`, `employee`, or `freelancer`.
- **`page`** (`int`, *Optional*, default: `1`): Page number.
- **`limit`** (`int`, *Optional*, default: `10`): Items per page.
"""
)
async def get_extra_service_worker_dropdown(
    request_id: str,
    search: Optional[str] = None,
    worker_type: Optional[str] = None,
    sort_by: Optional[str] = "smart",
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Get Worker Dropdown for Extra Service Assignment Endpoint.
    """
    db = get_database()
    query_es = {"$or": [{"_id": request_id}, {"id": request_id}]}
    es_doc = await db["extra_services"].find_one(query_es)
    if not es_doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    preferred_date = es_doc.get("preferred_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    est_hours = float(es_doc.get("estimated_hours", 2.0))
    duration_minutes = int(est_hours * 60)
    start_time_str = "08:00 AM"
    start_mins = parse_time_to_minutes(start_time_str)
    end_mins = (start_mins + duration_minutes) % 1440

    worker_filter = {
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }
    if worker_type and worker_type.lower() != "all":
        worker_filter["worker_type"] = worker_type.lower()

    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]
        worker_filter = {"$and": [worker_filter, {"$or": search_filter}]}

    worker_projection = {
        "_id": 1, "id": 1, "full_name": 1, "name": 1, "email": 1,
        "phone": 1, "profile_photo": 1, "worker_type": 1, "position": 1
    }
    cursor = db["users"].find(worker_filter, worker_projection).sort("full_name", 1)
    all_workers = await cursor.to_list(length=1000)

    now = datetime.now(timezone.utc)
    worker_ids = [str(w.get("_id") or w.get("id")) for w in all_workers]
    metrics_map = await batch_compute_worker_metrics(
        worker_ids=worker_ids,
        plan_id=request_id,
        plan_date=preferred_date,
        plan_start_mins=start_mins,
        plan_end_mins=end_mins,
        db=db,
        current_dt=now
    )

    computed_workers: List[CleaningPlanWorkerDropdownItem] = []

    for w in all_workers:
        wid = str(w.get("_id") or w.get("id"))
        w_name = w.get("full_name") or w.get("name") or "Worker"
        w_email = w.get("email") or ""
        w_phone = w.get("phone") or ""
        w_photo = w.get("profile_photo")
        w_type = w.get("worker_type", "employee")
        w_pos = normalize_worker_position(w.get("position", ""))

        w_metric = metrics_map.get(wid, {})
        m_stats = w_metric.get("month_stats", {})
        l_ended = w_metric.get("last_ended", {})

        computed_workers.append(CleaningPlanWorkerDropdownItem(
            worker_id=wid,
            name=w_name,
            email=w_email,
            phone=w_phone,
            profile_photo=w_photo,
            worker_type=w_type,
            position=w_pos,
            is_available=w_metric.get("is_available", True),
            unavailable_reason=w_metric.get("unavailable_reason"),
            avg_daily_work_minutes=m_stats.get("avg_daily_work_minutes", 0),
            total_shifts_this_month=m_stats.get("total_shifts_this_month", 0),
            total_work_minutes_this_month=m_stats.get("total_work_minutes_this_month", 0),
            formatted_avg_work=m_stats.get("formatted_avg_work", "0 mins/day"),
            last_work_end_time=l_ended.get("last_work_end_time"),
            last_work_ended_ago=l_ended.get("last_work_ended_ago"),
            minutes_since_last_work=l_ended.get("minutes_since_last_work")
        ))

    computed_workers = sort_workers_by_suitability(computed_workers, sort_by=sort_by)

    total_count = len(computed_workers)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_items = computed_workers[start_idx:end_idx]

    end_time_str = f"{(end_mins // 60) % 24:02d}:{end_mins % 60:02d}"
    time_window_str = f"{start_time_str} - {end_time_str}"

    return ExtraServiceWorkerDropdownPaginatedResponse(
        request_id=request_id,
        preferred_date=preferred_date,
        time_window=time_window_str,
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        workers=paginated_items
    )


@router.post(
    "/{request_id}/assign-workers",
    response_model=ExtraServiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Workers to Extra Service",
    description="""
### Assign Workers to Extra Service Request
Assigns one or more workers to an extra service request with specific positions (`teamleader`, `co_leader`, `normal`).

#### Behavior:
- **`action: "append"`** (*Default*): Merges newly assigned workers with previously assigned workers.
- **`action: "replace"`**: Replaces the existing assigned workers with the newly provided list.

#### Supported Worker Positions:
- **`"teamleader"`**: Primary shift / extra service team leader
- **`"co_leader"`**: Co-leader / assistant leader
- **`"normal"`**: Standard cleaner / worker
"""
)
async def assign_workers_to_extra_service(
    request_id: str,
    assign_in: AssignWorkersToExtraServiceRequest,
    force: bool = True,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Assign Workers to Extra Service Endpoint.
    """
    db = get_database()
    query = {"$or": [{"_id": request_id}, {"id": request_id}]}
    doc = await db["extra_services"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    new_worker_map = {}
    for w_item in assign_in.workers:
        wid = str(w_item.worker_id).strip()
        if wid:
            new_worker_map[wid] = normalize_worker_position(w_item.position)

    if not new_worker_map:
        raise HTTPException(status_code=400, detail="At least one worker must be provided in 'workers'")

    incoming_ids = list(new_worker_map.keys())
    obj_ids = [ObjectId(wid) for wid in incoming_ids if ObjectId.is_valid(wid)]
    or_clauses = [{"id": {"$in": incoming_ids}}, {"_id": {"$in": incoming_ids}}]
    if obj_ids:
        or_clauses.append({"_id": {"$in": obj_ids}})

    cursor_w = db["users"].find({"$or": or_clauses})
    valid_workers = await cursor_w.to_list(length=len(incoming_ids) * 2)
    valid_id_map = {}
    for w in valid_workers:
        if "_id" in w:
            valid_id_map[str(w["_id"])] = w
        if "id" in w and w["id"]:
            valid_id_map[str(w["id"])] = w

    filtered_new_map = {wid: pos for wid, pos in new_worker_map.items() if wid in valid_id_map}
    if not filtered_new_map:
        raise HTTPException(status_code=400, detail="None of the provided worker IDs exist in the system")

    # Time conflict check if force is False
    if not force:
        p_date = doc.get("preferred_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        est_hours = float(doc.get("estimated_hours", 2.0))
        dur_mins = int(est_hours * 60)
        s_mins = parse_time_to_minutes("08:00 AM")
        e_mins = (s_mins + dur_mins) % 1440

        conflicts = []
        for wid in filtered_new_map.keys():
            is_avail, unavail_reason = await check_worker_time_availability(
                worker_id=wid,
                plan_date=p_date,
                plan_start_mins=s_mins,
                plan_end_mins=e_mins,
                exclude_plan_id=request_id,
                db=db
            )
            if not is_avail:
                conflicts.append(f"Worker {wid}: {unavail_reason}")
        if conflicts:
            raise HTTPException(status_code=400, detail=f"Time conflicts detected: {'; '.join(conflicts)}")

    # Merge or replace workers
    action = (assign_in.action or "append").strip().lower()
    final_assigned_map = {}

    if action == "append":
        existing_assigned = doc.get("assigned_workers", [])
        for ew in existing_assigned:
            if isinstance(ew, dict):
                ew_id = str(ew.get("worker_id") or ew.get("id") or "")
                if ew_id:
                    final_assigned_map[ew_id] = ew.get("position", "normal")

    final_assigned_map.update(filtered_new_map)

    final_assigned_workers = []
    for wid, pos in final_assigned_map.items():
        u_doc = valid_id_map.get(wid) or {}
        final_assigned_workers.append({
            "worker_id": wid,
            "name": u_doc.get("full_name") or u_doc.get("name") or "Worker",
            "role": "worker",
            "worker_type": u_doc.get("worker_type", "employee"),
            "position": pos,
            "profile_photo": u_doc.get("profile_photo") or u_doc.get("profile_picture")
        })

    now = datetime.now(timezone.utc)
    update_data = {
        "assigned_workers": final_assigned_workers,
        "status": "approved",
        "updated_at": now
    }
    if assign_in.estimated_hours is not None:
        update_data["estimated_hours"] = assign_in.estimated_hours

    await db["extra_services"].update_one(query, {"$set": update_data})
    updated_doc = await db["extra_services"].find_one(query)

    # Fire rich push & in-app notifications to newly assigned workers
    try:
        newly_assigned_items = [{"worker_id": wid, "position": pos} for wid, pos in filtered_new_map.items()]
        await send_extra_service_assignment_notifications(
            es_doc=updated_doc,
            assigned_workers=newly_assigned_items,
            db=db
        )
    except Exception as e:
        print(f"Error sending extra service assignment notifications: {e}")

    return format_extra_service_response(updated_doc)


async def send_extra_service_assignment_notifications(
    es_doc: dict,
    assigned_workers: List[dict],
    db
) -> List[dict]:
    """
    Fires rich push notifications (via OneSignal), saves in-app notifications,
    and broadcasts real-time WebSocket event for all workers assigned to an extra service.
    """
    from app.services.notification_service import NotificationService
    notif_service = NotificationService()

    req_id = str(es_doc.get("id") or es_doc.get("_id"))
    es_title = es_doc.get("title") or "Extra Cleaning Service"
    pref_date = es_doc.get("preferred_date") or ""
    client_name = es_doc.get("client_name") or (es_doc.get("client", {}) or {}).get("name", "")
    location_name = es_doc.get("location_name") or (es_doc.get("location", {}) or {}).get("name", "")
    room_name = es_doc.get("room_name") or (es_doc.get("room", {}) or {}).get("name", "")
    priority = es_doc.get("priority", "Medium Priority")
    description = es_doc.get("description", "")
    est_hours = float(es_doc.get("estimated_hours", 2.0))
    tasks_count = len(es_doc.get("tasks", []))
    photos_count = len(es_doc.get("required_photos", []))

    start_time = "08:00 AM"
    duration_mins = int(est_hours * 60)
    start_mins = parse_time_to_minutes(start_time)
    end_mins = (start_mins + duration_mins) % 1440
    end_hour = end_mins // 60
    end_ampm = "AM" if end_hour < 12 else "PM"
    end_hour_12 = end_hour % 12 or 12
    end_time_str = f"{end_hour_12:02d}:{end_mins % 60:02d} {end_ampm}"

    sent_notifications = []

    for w_entry in assigned_workers:
        wid = str(w_entry.get("worker_id") or "")
        if not wid:
            continue
        position = normalize_worker_position(w_entry.get("position"))
        position_display = "Team Leader" if position == "teamleader" else ("Co-Leader" if position == "co_leader" else "Cleaner")

        user_doc = await db["users"].find_one({"$or": [{"_id": wid}, {"id": wid}]})
        if not user_doc and ObjectId.is_valid(wid):
            try:
                user_doc = await db["users"].find_one({"_id": ObjectId(wid)})
            except Exception:
                pass

        player_ids = []
        if user_doc:
            p_id = user_doc.get("onesignal_player_id") or user_doc.get("onesignal_id") or user_doc.get("player_id")
            if p_id:
                player_ids.append(str(p_id))
            for pid_item in user_doc.get("player_ids", []):
                if pid_item and str(pid_item) not in player_ids:
                    player_ids.append(str(pid_item))

        title = f"New Extra Service Assigned: {es_title}"
        message = (
            f"You have been assigned as {position_display} for extra service '{es_title}' "
            f"on {pref_date} ({start_time} - {end_time_str})."
        )

        rich_data = {
            "request_id": req_id,
            "extra_service_id": req_id,
            "shift_id": req_id,
            "service_kind": "extra_service",
            "deeplink": f"cleaningone://worker/shifts/{req_id}",
            "route": f"/worker/shifts/{req_id}",
            "title": es_title,
            "position": position,
            "position_display": position_display,
            "date": pref_date,
            "preferred_date": pref_date,
            "start_time": start_time,
            "end_time": end_time_str,
            "estimated_hours": est_hours,
            "client_name": client_name,
            "location_name": location_name,
            "room_name": room_name,
            "priority": priority,
            "description": description,
            "tasks_count": tasks_count,
            "total_photos_count": photos_count
        }

        notif_doc = await notif_service.create_notification(
            title=title,
            message=message,
            notification_type="extra_service_assignment",
            route_type="extra_services",
            recipient_type="worker",
            user_id=wid,
            player_ids=player_ids if player_ids else None,
            plan_id=req_id,
            data=rich_data
        )
        sent_notifications.append(notif_doc)

        try:
            from app.api.chat import ws_manager
            await ws_manager.broadcast_to_users({
                "type": "extra_service_assignment",
                "request_id": req_id,
                "extra_service_id": req_id,
                "shift_id": req_id,
                "service_kind": "extra_service",
                "deeplink": f"cleaningone://worker/shifts/{req_id}",
                "route": f"/worker/shifts/{req_id}",
                "title": title,
                "message": message,
                "data": rich_data
            }, [wid])
        except Exception as ws_err:
            print(f"Error broadcasting extra service websocket: {ws_err}")

    return sent_notifications


@router.post(
    "/{request_id}/complete-approve",
    response_model=ExtraServiceResponse,
    summary="Admin Final Approve & Credit Working Hours (Manual Override)",
    description="""
### Admin Final Approve & Credit Working Hours (Manual Override)
Extra services are now auto-completed (worked hours auto-credited) the moment the worker submits — this endpoint
is only a manual fallback (e.g. to force-close a request the worker never submitted). It is idempotent: calling it
on an already-completed request just returns the existing result and never credits hours twice.
"""
)
async def final_approve_extra_service_completion(
    request_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Final Approve & Credit Working Hours Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    updated_doc = await finalize_extra_service_completion(doc, db)
    return format_extra_service_response(updated_doc)
