import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.extra_services_helper import format_extra_service_response
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
Returns a paginated list of extra service requests across all clients for Manager review, with status and search filters.
"""
)
async def list_admin_extra_services(
    status_val: Optional[str] = None,
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

    requests_res = [format_extra_service_response(d) for d in raw_docs]
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

    worker_filter = {"role": "worker"}
    if worker_type and worker_type.lower() != "all":
        worker_filter["worker_type"] = worker_type.lower()

    if search:
        worker_filter["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}}
        ]

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

    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/complete-approve",
    response_model=ExtraServiceResponse,
    summary="Admin Final Approve & Credit Working Hours",
    description="""
### Admin Final Approve & Credit Working Hours
Finalizes and approves extra service completion. Calculates worked hours and credits them directly to assigned workers' `total_working_hours` in the users collection.
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

    now = datetime.now(timezone.utc)

    start_dt = doc.get("actual_start_time")
    finish_dt = doc.get("actual_finish_time") or now
    est_hours = float(doc.get("estimated_hours", 2.0))

    if start_dt and isinstance(start_dt, datetime):
        if finish_dt.tzinfo is None:
            finish_dt = finish_dt.replace(tzinfo=timezone.utc)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        hours_worked = max(0.5, round((finish_dt - start_dt).total_seconds() / 3600.0, 2))
    else:
        hours_worked = est_hours if est_hours > 0 else 2.0

    # Credit working hours to assigned workers in users collection
    workers = doc.get("assigned_workers", [])
    for w in workers:
        w_id = str(w.get("worker_id"))
        w_query = {"_id": ObjectId(w_id)} if ObjectId.is_valid(w_id) else {"_id": w_id}
        await db["users"].update_one(w_query, {"$inc": {"total_working_hours": hours_worked}})

    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "completed",
            "actual_finish_time": finish_dt,
            "hours_credited": hours_worked,
            "updated_at": now
        }}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)
