import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional, Union
from app.core.database import get_database
from app.schemas.client_list import (
    CleaningPlanRoomDetail, CleaningPlanWorkerDetail,
    CleaningPlanClientDetail, CleaningPlanManagerDetail,
    ManagerCleaningPlanCreate, ManagerCleaningPlanUpdate,
    ManagerCleaningPlanDetailResponse, ManagerCleaningPlanListItemResponse,
    ManagerCleaningPlanPaginatedResponse,
    CleaningPlanRoomDropdownItem, CleaningPlanRoomDropdownPaginatedResponse,
    CleaningPlanWorkerDropdownItem, CleaningPlanWorkerDropdownPaginatedResponse,
    AssignWorkersToCleaningPlanRequest,
    CleaningTaskCreate, CleaningTaskResponse,
    RequiredPhotoCreate, RequiredPhotoResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.cleaning_plan_worker_utils import (
    parse_time_to_minutes, calculate_worker_month_stats, check_worker_time_availability,
    normalize_worker_position, resolve_plan_working_days, calculate_worker_last_work_ended,
    sort_workers_by_suitability, send_cleaning_plan_assignment_notifications
)
from app.api.admin.cleaning_plan_formatters import (
    _format_tasks_list, _format_photos_list, _resolve_rooms_data,
    _resolve_clients_data, _resolve_manager_data, _resolve_workers_data,
    _calculate_end_time, _format_manager_cleaning_plan_detail,
    _format_manager_cleaning_plan_list_item
)

cleaning_plan_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Cleaning Plan Management"])


# ============================================================================
# 1. Room Dropdown for Cleaning Plan Creation (Paginated + Filter)
# ============================================================================

@cleaning_plan_mgmt_router.get(
    "/dropdowns/rooms",
    response_model=CleaningPlanRoomDropdownPaginatedResponse,
    summary="Get Room Dropdowns for Cleaning Plan"
)
async def get_cleaning_plan_room_dropdowns(
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}

    if location_id:
        query["location_id"] = location_id
    elif client_id:
        loc_cursor = db["locations"].find({"$or": [{"client_id": client_id}, {"id": client_id}]})
        loc_docs = await loc_cursor.to_list(length=1000)
        loc_ids = [str(l.get("_id") or l.get("id")) for l in loc_docs]
        query["$or"] = [
            {"client_id": client_id},
            {"location_id": {"$in": loc_ids}}
        ]

    if search:
        search_filter = [
            {"room_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"room_type": {"$regex": search, "$options": "i"}}
        ]
        if "$or" in query:
            query = {"$and": [query, {"$or": search_filter}]}
        else:
            query["$or"] = search_filter

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    rooms_list = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
        rname = r.get("room_name") or r.get("name") or "Room"
        rtype = r.get("room_type") or r.get("type") or "standard"
        photo_num = len(r.get("required_photos", [])) if r.get("required_photos") else (r.get("photo_number") or r.get("required_photos_count", 0))
        task_num = len(r.get("tasks", [])) if r.get("tasks") else (r.get("task_number") or r.get("tasks_count", 0))

        rooms_list.append(CleaningPlanRoomDropdownItem(
            room_id=rid,
            room_name=rname,
            room_type=rtype,
            photo_number=photo_num,
            task_number=task_num
        ))

    return CleaningPlanRoomDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=rooms_list
    )


@cleaning_plan_mgmt_router.post(
    "/cleaning-plans",
    response_model=ManagerCleaningPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Cleaning Plan",
    description="""
### Create Cleaning Plan / Recurring Shift (Draft)
Creates a cleaning plan / shift draft with automated duration calculation, multi-client aggregation, and auto-computed end time.

**Key Parameters**:
- `title`: Plan title
- `room_ids`: List of room IDs across multiple clients
- `date`: Base shift date (`MM/DD/YYYY` or `YYYY-MM-DD`)
- `start_time`: e.g. `08:00 AM` (auto-computes `end_time`)
- `repeat_shift`: `Does not repeat`, `Every day`, `Standard working week`, `Weekly`, `Monthly`
- `repeat_until`: Optional recurrence end date
- `working_days`: Active shift days (e.g. `["sun"]` for Monthly Sunday cleaning)
- `shift_notes`: Shift notes and instructions
- `additional_tasks`, `additional_required_photos`: Extra tasks and photos outside room defaults
"""
)
async def create_manager_cleaning_plan(
    plan_in: ManagerCleaningPlanCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()

    # 1. Resolve room details
    rooms_data = await _resolve_rooms_data(plan_in.room_ids, db)

    # 2. Resolve client information from rooms
    clients_data = await _resolve_clients_data(rooms_data, db)
    client_ids = [c.client_id for c in clients_data]
    client_names = [c.company_name for c in clients_data]

    location_id = plan_in.location_id
    location_name = ""
    if location_id:
        l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
        if l_doc:
            location_name = l_doc.get("name", "")
    elif plan_in.room_ids:
        raw_r = await db["rooms"].find_one({"$or": [{"_id": {"$in": plan_in.room_ids}}, {"id": {"$in": plan_in.room_ids}}, {"room_id": {"$in": plan_in.room_ids}}]})
        if raw_r:
            location_id = str(raw_r.get("location_id") or "")
            location_name = raw_r.get("location_name", "")
            if location_id and not location_name:
                l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
                if l_doc:
                    location_name = l_doc.get("name", "")

    # 3. Calculate duration (sum from rooms if not supplied)
    if plan_in.duration_minutes is not None:
        duration_minutes = plan_in.duration_minutes
    else:
        duration_minutes = sum(r.duration for r in rooms_data)
        if duration_minutes == 0:
            duration_minutes = 60

    # 4. Process additional tasks and photos with assigned IDs
    add_tasks_dicts = []
    for t in (plan_in.additional_tasks or []):
        t_dict = t.model_dump()
        if not t_dict.get("id"):
            t_dict["id"] = uuid.uuid4().hex[:8]
        add_tasks_dicts.append(t_dict)

    add_photos_dicts = []
    for p in (plan_in.additional_required_photos or []):
        p_dict = p.model_dump()
        if not p_dict.get("id"):
            p_dict["id"] = uuid.uuid4().hex[:8]
        add_photos_dicts.append(p_dict)

    # 5. Calculate total tasks and photos
    total_tasks_count = sum(len(r.tasks) for r in rooms_data) + len(add_tasks_dicts)
    total_photos_count = sum(len(r.required_photos) for r in rooms_data) + len(add_photos_dicts)

    # 6. Frequency days & Repeat shift
    date_val = plan_in.date or "2026-08-17"
    repeat_shift_val = plan_in.repeat_shift or "Standard working week"
    working_days = resolve_plan_working_days(
        repeat_shift=repeat_shift_val,
        working_days=plan_in.working_days,
        frequency=None,
        date_str=date_val
    )

    start_time_val = plan_in.start_time or "08:00 AM"
    end_time_val = _calculate_end_time(start_time_val, duration_minutes)
    repeat_until_val = plan_in.repeat_until
    shift_notes_val = plan_in.shift_notes or plan_in.description or ""

    manager_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", ""))

    now = datetime.now(timezone.utc)
    plan_id = f"plan_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": plan_id,
        "id": plan_id,
        "title": plan_in.title,
        "plan_name": plan_in.title,
        "shift_notes": shift_notes_val,
        "manager_id": manager_id,
        "client_ids": client_ids,
        "client_names": client_names,
        "client_id": client_ids[0] if client_ids else "",
        "company_name": client_names[0] if client_names else "",
        "location_id": location_id or "",
        "location_name": location_name or "",
        "room_ids": plan_in.room_ids,
        "worker_ids": [],
        "date": date_val,
        "start_time": start_time_val,
        "end_time": end_time_val,
        "repeat_shift": repeat_shift_val,
        "repeat_until": repeat_until_val,
        "working_days": working_days,
        "duration_minutes": duration_minutes,
        "additional_tasks": add_tasks_dicts,
        "additional_required_photos": add_photos_dicts,
        "total_tasks_count": total_tasks_count,
        "total_photos_count": total_photos_count,
        "status": "draft",
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["cleaning_plans"].insert_one(doc)
    if location_id:
        await db["locations"].update_one(
            {"$or": [{"_id": location_id}, {"id": location_id}]},
            {"$inc": {"cleaning_plans_count": 1}}
        )

    return await _format_manager_cleaning_plan_detail(doc, db, current_user=current_user)


@cleaning_plan_mgmt_router.get(
    "/cleaning-plans",
    response_model=ManagerCleaningPlanPaginatedResponse,
    summary="List Cleaning Plans"
)
async def list_manager_cleaning_plans(
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    room_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["$or"] = [
            {"client_id": client_id},
            {"client_ids": client_id}
        ]
    if location_id:
        query["location_id"] = location_id
    if room_id:
        query["room_ids"] = room_id
    if worker_id:
        query["worker_ids"] = worker_id
    if search:
        search_filter = [
            {"title": {"$regex": search, "$options": "i"}},
            {"plan_name": {"$regex": search, "$options": "i"}},
            {"location_name": {"$regex": search, "$options": "i"}},
            {"company_name": {"$regex": search, "$options": "i"}},
            {"client_name": {"$regex": search, "$options": "i"}},
            {"client_names": {"$regex": search, "$options": "i"}}
        ]
        if "$or" in query:
            query = {"$and": [query, {"$or": search_filter}]}
        else:
            query["$or"] = search_filter

    total_count = await db["cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_plans = await cursor.to_list(length=limit)

    plan_items = [await _format_manager_cleaning_plan_list_item(p, db) for p in raw_plans]

    return ManagerCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        plans=plan_items
    )


@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Get Full Cleaning Plan Details"
)
async def get_manager_cleaning_plan_detail(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": plan_id}, {"id": plan_id}]})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    return await _format_manager_cleaning_plan_detail(plan_doc, db, current_user=current_user)


@cleaning_plan_mgmt_router.patch(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Update Cleaning Plan"
)
async def update_manager_cleaning_plan(
    plan_id: str,
    plan_in: ManagerCleaningPlanUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    update_fields = plan_in.model_dump(exclude_unset=True)
    update_fields["updated_at"] = datetime.now(timezone.utc)

    if "title" in update_fields:
        update_fields["plan_name"] = update_fields["title"]
    if "working_days" in update_fields and isinstance(update_fields["working_days"], list):
        update_fields["working_days"] = [str(d).strip().lower() for d in update_fields["working_days"] if str(d).strip()]
    elif "repeat_shift" in update_fields:
        r_days = resolve_plan_working_days(
            repeat_shift=update_fields["repeat_shift"],
            working_days=None,
            frequency=None,
            date_str=update_fields.get("date", plan_doc.get("date"))
        )
        update_fields["working_days"] = r_days
    if "description" in update_fields:
        if "shift_notes" not in update_fields:
            update_fields["shift_notes"] = update_fields.pop("description")
        else:
            update_fields.pop("description", None)

    # Re-calculate counts if room_ids or additional tasks/photos changed
    merged_room_ids = update_fields.get("room_ids", plan_doc.get("room_ids", []))
    merged_add_tasks = update_fields.get("additional_tasks", plan_doc.get("additional_tasks", []))
    merged_add_photos = update_fields.get("additional_required_photos", plan_doc.get("additional_required_photos", []))

    rooms_data = await _resolve_rooms_data(merged_room_ids, db)
    update_fields["total_tasks_count"] = sum(len(r.tasks) for r in rooms_data) + len(merged_add_tasks)
    update_fields["total_photos_count"] = sum(len(r.required_photos) for r in rooms_data) + len(merged_add_photos)

    # Recalculate duration and end_time
    if "duration_minutes" not in update_fields:
        new_dur = sum(r.duration for r in rooms_data) if rooms_data else plan_doc.get("duration_minutes", 60)
        update_fields["duration_minutes"] = new_dur
    else:
        new_dur = update_fields["duration_minutes"]

    st_time = update_fields.get("start_time", plan_doc.get("start_time", "08:00 AM"))
    update_fields["end_time"] = _calculate_end_time(st_time, new_dur)

    await db["cleaning_plans"].update_one(query, {"$set": update_fields})
    updated_doc = await db["cleaning_plans"].find_one(query)

    return await _format_manager_cleaning_plan_detail(updated_doc, db, current_user=current_user)


@cleaning_plan_mgmt_router.delete(
    "/cleaning-plans/{plan_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Cleaning Plan"
)
async def delete_manager_cleaning_plan(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    loc_id = plan_doc.get("location_id")
    await db["cleaning_plans"].delete_one(query)

    if loc_id:
        await db["locations"].update_one(
            {"$or": [{"_id": loc_id}, {"id": loc_id}]},
            {"$inc": {"cleaning_plans_count": -1}}
        )

    return {"message": "Cleaning plan deleted successfully"}


# ============================================================================
# Worker Dropdown & Worker Assignment for Cleaning Plans
# ============================================================================

@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/{plan_id}/workers-dropdown",
    response_model=CleaningPlanWorkerDropdownPaginatedResponse,
    summary="Get Worker Dropdown for Cleaning Plan Assignment",
    description="""
### Worker Dropdown List for Draft Cleaning Plan

Retrieves a paginated list of workers with real-time availability and monthly average work metrics for the target cleaning plan / shift.

#### Path Parameter:
- **`plan_id`** (`str`, **Required**): The ID of the cleaning plan to assign workers to.

#### Query Parameters:
- **`search`** (`str`, *Optional*): Filter by worker full name, email, phone, or position.
- **`worker_type`** (`str`, *Optional*): Filter by `all`, `employee`, or `freelancer`.
- **`page`** (`int`, *Optional*, default: `1`): Page number.
- **`limit`** (`int`, *Optional*, default: `10`): Items per page.

#### Returned Worker Metrics:
- **`is_available`** (`bool`): `true` if the worker has no conflicting shift/cleaning plan on the plan's date and time window.
- **`unavailable_reason`** (`str` | `null`): Reason if unavailable (e.g. `"Assigned to 'Shift A' (08:00 AM - 12:00 PM)"`).
- **`avg_daily_work_minutes`** (`int`): Daily average minutes worked in the current month (`total_work_minutes / current_day_of_month`).
- **`total_shifts_this_month`** (`int`): Total shifts/plans worked in the current calendar month.
- **`total_work_minutes_this_month`** (`int`): Total work duration in minutes across all shifts this month.
- **`formatted_avg_work`** (`str`): Human-readable string (e.g. `"231 mins/day"`).
"""
)
async def get_cleaning_plan_worker_dropdown(
    plan_id: str,
    search: Optional[str] = None,
    worker_type: Optional[str] = None,
    sort_by: Optional[str] = "smart",
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": plan_id}, {"id": plan_id}]})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    plan_date = plan_doc.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan_start = plan_doc.get("start_time", "08:00 AM")
    plan_dur = plan_doc.get("duration_minutes", 60)
    plan_start_mins = parse_time_to_minutes(plan_start)
    plan_end_mins = (plan_start_mins + plan_dur) % 1440
    plan_end = plan_doc.get("end_time") or _calculate_end_time(plan_start, plan_dur)
    plan_time_window = f"{plan_start} - {plan_end}"

    # Build query for workers
    query = {"role": "worker", "account_status": {"$ne": "deleted"}}
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}}
        ]
        query["$or"] = search_filter

    cursor = db["users"].find(query)
    raw_workers = await cursor.to_list(length=500)

    now_utc = datetime.now(timezone.utc)
    all_worker_items = []

    for w in raw_workers:
        wid = str(w.get("_id") or w.get("id"))
        w_name = w.get("full_name") or w.get("name") or "Worker"
        w_type = str(w.get("worker_type") or "employee")
        w_pos = w.get("position") or "Cleaner"
        w_pic = w.get("profile_photo") or w.get("profile_picture")
        w_email = w.get("email")
        w_phone = w.get("phone")

        # 1. Monthly stats
        stats = await calculate_worker_month_stats(wid, db, current_dt=now_utc)

        # 2. Availability check
        is_avail, unavail_reason = await check_worker_time_availability(
            worker_id=wid,
            plan_date=plan_date,
            plan_start_mins=plan_start_mins,
            plan_end_mins=plan_end_mins,
            exclude_plan_id=plan_id,
            db=db
        )

        # 3. Last assigned work end time & relative duration
        last_work_info = await calculate_worker_last_work_ended(
            worker_id=wid,
            target_date_str=plan_date,
            target_start_mins=plan_start_mins,
            exclude_plan_id=plan_id,
            db=db
        )

        all_worker_items.append(CleaningPlanWorkerDropdownItem(
            worker_id=wid,
            name=w_name,
            profile_photo=w_pic,
            worker_type=w_type,
            position=w_pos,
            email=w_email,
            phone=w_phone,
            is_available=is_avail,
            unavailable_reason=unavail_reason,
            avg_daily_work_minutes=stats["avg_daily_work_minutes"],
            total_shifts_this_month=stats["total_shifts_this_month"],
            total_work_minutes_this_month=stats["total_work_minutes_this_month"],
            formatted_avg_work=stats["formatted_avg_work"],
            last_work_end_time=last_work_info["last_work_end_time"],
            last_work_ended_ago=last_work_info["last_work_ended_ago"],
            minutes_since_last_work=last_work_info["minutes_since_last_work"]
        ))

    # Apply smart suitability sorting across all worker candidates
    sorted_workers = sort_workers_by_suitability(all_worker_items, sort_by=sort_by or "smart")

    # Apply pagination on the ranked worker list
    total_count = len(sorted_workers)
    skip = (page - 1) * limit
    paginated_workers = sorted_workers[skip: skip + limit]

    return CleaningPlanWorkerDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        plan_id=plan_id,
        plan_date=plan_date,
        plan_time_window=plan_time_window,
        workers=paginated_workers
    )


@cleaning_plan_mgmt_router.post(
    "/cleaning-plans/{plan_id}/assign-workers",
    response_model=ManagerCleaningPlanDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Workers to Cleaning Plan",
    description="""
### Assign Workers to Draft Cleaning Plan

Assigns one or more workers to a cleaning plan with assigned positions (`teamleader`, `co_leader`, `normal`).

#### Behavior When Plan Already Has Workers:
- **`action: "append"`** (*Default*): Merges newly assigned workers with previously assigned workers without removing existing ones. If an already assigned worker is provided again, their position is updated.
- **`action: "replace"`**: Replaces the existing assigned workers with the newly provided list.

#### Supported Worker Positions:
- **`"teamleader"`**: Primary shift/team leader
- **`"co_leader"`**: Co-leader / assistant leader
- **`"normal"`**: Standard cleaner/worker

#### Path Parameter:
- **`plan_id`** (`str`, **Required**): Target cleaning plan ID.

#### Request Body:
```json
{
  "workers": [
    {"worker_id": "w_101", "position": "teamleader"},
    {"worker_id": "w_102", "position": "co_leader"},
    {"worker_id": "w_103", "position": "normal"}
  ],
  "action": "append"
}
```

#### Query Parameter:
- **`force`** (`bool`, *Optional*, default: `true`): If `false`, strictly prevents assignment if any selected worker has a time conflict.
"""
)
async def assign_workers_to_cleaning_plan(
    plan_id: str,
    assign_in: AssignWorkersToCleaningPlanRequest,
    force: bool = True,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    # 1. Parse incoming worker items and positions
    new_worker_map = {}  # worker_id -> position
    for w_item in assign_in.workers:
        wid = str(w_item.worker_id).strip()
        if wid:
            new_worker_map[wid] = normalize_worker_position(w_item.position)

    if not new_worker_map:
        raise HTTPException(status_code=400, detail="At least one worker must be provided in 'workers'")

    # Validate that incoming worker IDs exist in users collection
    incoming_ids = list(new_worker_map.keys())
    cursor_w = db["users"].find({"$or": [{"_id": {"$in": incoming_ids}}, {"id": {"$in": incoming_ids}}]})
    valid_workers = await cursor_w.to_list(length=len(incoming_ids) * 2)
    valid_id_map = {str(w.get("_id") or w.get("id")): w for w in valid_workers}

    filtered_new_map = {wid: pos for wid, pos in new_worker_map.items() if wid in valid_id_map}
    if not filtered_new_map:
        raise HTTPException(status_code=400, detail="None of the provided worker IDs exist in the system")

    # 2. Check for time conflicts if force is False
    if not force:
        plan_date = plan_doc.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        plan_start = plan_doc.get("start_time", "08:00 AM")
        plan_dur = plan_doc.get("duration_minutes", 60)
        plan_start_mins = parse_time_to_minutes(plan_start)
        plan_end_mins = (plan_start_mins + plan_dur) % 1440

        conflicts = []
        for wid in filtered_new_map.keys():
            is_avail, unavail_reason = await check_worker_time_availability(
                worker_id=wid,
                plan_date=plan_date,
                plan_start_mins=plan_start_mins,
                plan_end_mins=plan_end_mins,
                exclude_plan_id=plan_id,
                db=db
            )
            if not is_avail:
                conflicts.append(f"Worker {wid}: {unavail_reason}")
        if conflicts:
            raise HTTPException(status_code=400, detail=f"Time conflicts detected: {'; '.join(conflicts)}")

    # 3. Merge or replace with existing assigned workers
    action = (assign_in.action or "append").strip().lower()
    final_assigned_map = {}  # worker_id -> position

    if action == "append":
        # Keep existing workers and their positions
        existing_assigned = plan_doc.get("assigned_workers", [])
        if isinstance(existing_assigned, list) and existing_assigned:
            for ew in existing_assigned:
                if isinstance(ew, dict):
                    ew_id = str(ew.get("worker_id") or "")
                    if ew_id:
                        final_assigned_map[ew_id] = ew.get("position", "normal")
        else:
            # Fallback to existing worker_ids
            for ew_id in plan_doc.get("worker_ids", []):
                if str(ew_id).strip():
                    final_assigned_map[str(ew_id).strip()] = "normal"

    # Apply/overwrite with new worker assignments
    final_assigned_map.update(filtered_new_map)

    final_worker_ids = list(final_assigned_map.keys())
    final_assigned_workers = [
        {"worker_id": wid, "position": pos}
        for wid, pos in final_assigned_map.items()
    ]

    # 4. Automatically update cleaning plan status to "assigned"
    update_data = {
        "worker_ids": final_worker_ids,
        "assigned_workers": final_assigned_workers,
        "status": "assigned",
        "updated_at": datetime.now(timezone.utc)
    }

    await db["cleaning_plans"].update_one(query, {"$set": update_data})
    updated_doc = await db["cleaning_plans"].find_one(query)

    # 5. Fire rich push & in-app notifications to all newly assigned workers
    try:
        newly_assigned_items = [{"worker_id": wid, "position": pos} for wid, pos in filtered_new_map.items()]
        await send_cleaning_plan_assignment_notifications(
            plan_doc=updated_doc,
            assigned_workers=newly_assigned_items,
            db=db
        )
    except Exception as e:
        print(f"Error sending assignment notifications: {e}")

    return await _format_manager_cleaning_plan_detail(updated_doc, db, current_user=current_user)


