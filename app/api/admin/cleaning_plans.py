import uuid
import asyncio
from bson import ObjectId
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
    RejectPendingAdditionalTaskRequest
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
    _format_manager_cleaning_plan_list_item,
    batch_format_manager_cleaning_plan_list_items
)
from app.services.chat_service import sync_cleaning_plan_group_conversation

cleaning_plan_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Cleaning Plan Management"])


# ============================================================================
# Cleaning Plan Management Endpoints (CRUD & Assignment)
# ============================================================================

@cleaning_plan_mgmt_router.post(
    "/cleaning-plans",
    response_model=ManagerCleaningPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Cleaning Plan",
    description="""
### Create Cleaning Plan / Recurring Shift (Draft)
Creates a cleaning plan / shift draft with automated duration calculation, single-client enforcement, and unified task & photo hierarchy.

#### Supported Field Values & Options:
- **`date`** / **`repeat_until`**: define the plan's active date window (`date` through `repeat_until`, inclusive). On the manager roster (`GET /manager/roster/daily|weekly|monthly`), this window alone decides which days the plan/shift shows up on — a plan with no `repeat_until` is active only on `date`.
- **`working_days`**: `["mon", "tue", "wed", "thu", "fri", "sat", "sun"]` (e.g. `["sun"]` for Monthly Sunday deep cleaning). Still used by the general plan-active check (client cleaning-plan views, dashboards, worker home) — but the **manager roster ignores it entirely**; on the roster, which specific tasks show up on which day is instead decided per-task by each room task's own `frequency_type`/`weekly_days`/`monthly_dates`/`fixed_date`.
- **`repeat_shift`**: display-only free text (e.g. `"Does not repeat"`, `"Weekly"`) — stored as sent but never used to determine recurrence anywhere; `working_days` and `date`/`repeat_until` are the only fields that do that.
- **`additional_tasks[].frequency_type`**: `"every_visit"`, `"weekly"`, `"monthly"`, `"yearly"`
- **`additional_tasks[].is_photo_req`**: `true` | `false` (automatically set to `true` when `photo` list is provided)
- **`additional_tasks[].photo`**: List of required photo requirements attached directly to this specific task, e.g.:
  ```json
  "photo": [
    {"name": "After Deep Floor scrubbing"},
    {"name": "Before Deep Floor scrubbing"}
  ]
  ```
- **`start_time`**: e.g. `"08:00 AM"` (used with room durations to compute the plan's total `duration_minutes` internally; the response's `end_date` field is a calendar date, not a time — see `date`/`repeat_until` above)
- **`room_ids`**: List of valid room IDs belonging to a single client (e.g. `["room_a366ecf17c", "room_848a13ff0c"]`)
"""
)
async def create_manager_cleaning_plan(
    plan_in: ManagerCleaningPlanCreate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Create Cleaning Plan Endpoint.
    """
    db = get_database()

    # 1. Dedup room IDs while preserving input order
    raw_room_ids = plan_in.room_ids or []
    deduped_room_ids = list(dict.fromkeys([str(r).strip() for r in raw_room_ids if str(r).strip()]))
    if not deduped_room_ids:
        raise HTTPException(status_code=400, detail="At least one room_id must be provided to create a cleaning plan.")

    # 2. Resolve room details & validate existence
    rooms_data = await _resolve_rooms_data(deduped_room_ids, db)
    found_rids = {r.room_id for r in rooms_data}
    missing_rids = [rid for rid in deduped_room_ids if rid not in found_rids]
    if missing_rids:
        raise HTTPException(
            status_code=404,
            detail=f"The following room ID(s) were not found: {', '.join(missing_rids)}"
        )

    # 3. Resolve client information from rooms and enforce Single Client constraint
    clients_data = await _resolve_clients_data(rooms_data, db)
    if len(clients_data) > 1:
        client_summary = ", ".join([f"'{c.company_name}' (ID: {c.client_id})" for c in clients_data])
        raise HTTPException(
            status_code=400,
            detail=f"Only one client is allowed per cleaning plan. The selected rooms belong to {len(clients_data)} different clients: {client_summary}. Please select rooms belonging to a single client."
        )

    client_ids = [c.client_id for c in clients_data]
    client_names = [c.company_name for c in clients_data]

    location_id = plan_in.location_id
    location_name = ""
    if location_id:
        l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
        if l_doc:
            location_name = l_doc.get("name", "")
    elif deduped_room_ids:
        raw_r = await db["rooms"].find_one({"$or": [{"_id": {"$in": deduped_room_ids}}, {"id": {"$in": deduped_room_ids}}, {"room_id": {"$in": deduped_room_ids}}]})
        if raw_r:
            location_id = str(raw_r.get("location_id") or "")
            location_name = raw_r.get("location_name", "")
            if location_id and not location_name:
                l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
                if l_doc:
                    location_name = l_doc.get("name", "")

    # 4. Process additional tasks with embedded photos and IDs
    add_tasks_dicts = []
    for t in (plan_in.additional_tasks or []):
        t_dict = t.model_dump() if hasattr(t, "model_dump") else dict(t)
        if not t_dict.get("id"):
            t_dict["id"] = uuid.uuid4().hex[:8]

        raw_photos = t_dict.get("photo") or []
        processed_photos = []
        for p in raw_photos:
            p_dict = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else {"name": str(p)})
            if not p_dict.get("id"):
                p_dict["id"] = uuid.uuid4().hex[:8]
            processed_photos.append(p_dict)
        t_dict["photo"] = processed_photos
        if processed_photos and not t_dict.get("is_photo_req"):
            t_dict["is_photo_req"] = True
        add_tasks_dicts.append(t_dict)

    # 5. Calculate duration (sum from rooms + any additional-task durations, if not supplied)
    add_tasks_duration = sum(int(t.get("duration_minutes") or 0) for t in add_tasks_dicts)
    if plan_in.duration_minutes is not None:
        duration_minutes = plan_in.duration_minutes
    else:
        duration_minutes = sum(r.duration for r in rooms_data) + add_tasks_duration
        if duration_minutes == 0:
            duration_minutes = 60

    # 6. Calculate total tasks and photos across rooms and additional tasks
    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)
    add_tasks_count = len(add_tasks_dicts)
    add_photos_count = sum(len(t.get("photo", [])) for t in add_tasks_dicts)

    total_tasks_count = room_tasks_count + add_tasks_count
    total_photos_count = room_photos_count + add_photos_count

    # 7. Frequency days — driven entirely by working_days now; repeat_shift is
    # stored as given (may be null) purely for display, never used to derive this.
    date_val = plan_in.date or "2026-08-17"
    working_days = resolve_plan_working_days(
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
        "room_ids": deduped_room_ids,
        "worker_ids": [],
        "date": date_val,
        "start_time": start_time_val,
        "end_time": end_time_val,
        "repeat_shift": plan_in.repeat_shift,
        "repeat_until": repeat_until_val,
        "working_days": working_days,
        "duration_minutes": duration_minutes,
        "timezone": plan_in.timezone or "Europe/Amsterdam",
        "additional_tasks": add_tasks_dicts,
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
    summary="List Cleaning Plans",
    description="""
### List Cleaning Plans (Paginated)
Retrieves paginated cleaning plans with filtering by `client_id`, `location_id`, `room_id`, `worker_id`, or text `search`.
Computes aggregate room counts, worker counts, `total_tasks_count`, and `total_photos_count`.
"""
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
    """
    List Cleaning Plans Endpoint.
    """
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

    skip = (page - 1) * limit
    total_count, raw_plans = await asyncio.gather(
        db["cleaning_plans"].count_documents(query),
        db["cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    )

    plan_items = await batch_format_manager_cleaning_plan_list_items(raw_plans, db)

    return ManagerCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        plans=plan_items
    )


@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Get Full Cleaning Plan Details",
    description="""
### Get Full Cleaning Plan Details
Returns complete cleaning plan details including assigned rooms, room tasks, custom additional tasks, task-level photo requirements, client company profile, and manager info.
"""
)
async def get_manager_cleaning_plan_detail(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Get Full Cleaning Plan Details Endpoint.
    """
    db = get_database()
    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": plan_id}, {"id": plan_id}]})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    return await _format_manager_cleaning_plan_detail(plan_doc, db, current_user=current_user)


@cleaning_plan_mgmt_router.patch(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Update Cleaning Plan",
    description="""
### Update Cleaning Plan
Updates cleaning plan title, shift notes, rooms, schedule, working days, or additional tasks with embedded photos.
Automatically recalculates duration, end time, and task/photo counts.
"""
)
async def update_manager_cleaning_plan(
    plan_id: str,
    plan_in: ManagerCleaningPlanUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Update Cleaning Plan Endpoint.
    """
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
    # repeat_shift, if sent, is stored as-is (see update_fields above via model_dump)
    # purely for display — it no longer derives working_days or affects recurrence.
    if "description" in update_fields:
        if "shift_notes" not in update_fields:
            update_fields["shift_notes"] = update_fields.pop("description")
        else:
            update_fields.pop("description", None)

    # Resolve location_name whenever location_id changes — previously stored on the
    # doc but never actually returned by the response schema; now fixed both ways.
    if "location_id" in update_fields:
        new_loc_id = update_fields["location_id"]
        if new_loc_id:
            l_doc = await db["locations"].find_one({"$or": [{"_id": new_loc_id}, {"id": new_loc_id}]})
            update_fields["location_name"] = l_doc.get("name", "") if l_doc else ""
        else:
            update_fields["location_name"] = ""

    # Process updated additional tasks if supplied
    if "additional_tasks" in update_fields and isinstance(update_fields["additional_tasks"], list):
        processed_add_tasks = []
        for t in update_fields["additional_tasks"]:
            t_dict = t if isinstance(t, dict) else (t.model_dump() if hasattr(t, "model_dump") else dict(t))
            if not t_dict.get("id"):
                t_dict["id"] = uuid.uuid4().hex[:8]
            raw_photos = t_dict.get("photo") or []
            processed_photos = []
            for p in raw_photos:
                p_dict = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else {"name": str(p)})
                if not p_dict.get("id"):
                    p_dict["id"] = uuid.uuid4().hex[:8]
                processed_photos.append(p_dict)
            t_dict["photo"] = processed_photos
            if processed_photos and not t_dict.get("is_photo_req"):
                t_dict["is_photo_req"] = True
            processed_add_tasks.append(t_dict)
        update_fields["additional_tasks"] = processed_add_tasks

    # Re-calculate counts if room_ids changed
    if "room_ids" in update_fields and isinstance(update_fields["room_ids"], list):
        deduped_room_ids = list(dict.fromkeys([str(r).strip() for r in update_fields["room_ids"] if str(r).strip()]))
        if not deduped_room_ids:
            raise HTTPException(status_code=400, detail="At least one room_id must be provided in 'room_ids'.")

        rooms_data = await _resolve_rooms_data(deduped_room_ids, db)
        found_rids = {r.room_id for r in rooms_data}
        missing_rids = [rid for rid in deduped_room_ids if rid not in found_rids]
        if missing_rids:
            raise HTTPException(
                status_code=404,
                detail=f"The following room ID(s) were not found: {', '.join(missing_rids)}"
            )

        clients_data = await _resolve_clients_data(rooms_data, db)
        if len(clients_data) > 1:
            client_summary = ", ".join([f"'{c.company_name}' (ID: {c.client_id})" for c in clients_data])
            raise HTTPException(
                status_code=400,
                detail=f"Only one client is allowed per cleaning plan. The selected rooms belong to {len(clients_data)} different clients: {client_summary}. Please select rooms belonging to a single client."
            )

        update_fields["room_ids"] = deduped_room_ids
        if clients_data:
            update_fields["client_id"] = clients_data[0].client_id
            update_fields["client_name"] = clients_data[0].company_name
            update_fields["company_name"] = clients_data[0].company_name
            update_fields["client_ids"] = [clients_data[0].client_id]
            update_fields["client_names"] = [clients_data[0].company_name]
    else:
        merged_room_ids = plan_doc.get("room_ids", [])
        rooms_data = await _resolve_rooms_data(merged_room_ids, db)

    merged_add_tasks = update_fields.get("additional_tasks", plan_doc.get("additional_tasks", []))

    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)
    add_tasks_count = len(merged_add_tasks)
    add_photos_count = sum(len(t.get("photo", [])) for t in merged_add_tasks)

    update_fields["total_tasks_count"] = room_tasks_count + add_tasks_count
    update_fields["total_photos_count"] = room_photos_count + add_photos_count

    # Recalculate duration and end_time (rooms + any additional-task durations)
    add_tasks_duration = sum(int(t.get("duration_minutes") or 0) for t in merged_add_tasks)
    if "duration_minutes" not in update_fields:
        new_dur = (sum(r.duration for r in rooms_data) + add_tasks_duration) if rooms_data else plan_doc.get("duration_minutes", 60)
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
    summary="Delete Cleaning Plan",
    description="""
### Delete Cleaning Plan
Deletes a cleaning plan and decrements the cleaning plans counter on the associated location.
"""
)
async def delete_manager_cleaning_plan(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Delete Cleaning Plan Endpoint.
    """
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
# Worker Assignment for Cleaning Plans
# ============================================================================

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
"""
)
async def assign_workers_to_cleaning_plan(
    plan_id: str,
    assign_in: AssignWorkersToCleaningPlanRequest,
    force: bool = True,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Assign Workers to Cleaning Plan Endpoint.
    """
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
    obj_ids = [ObjectId(wid) for wid in incoming_ids if ObjectId.is_valid(wid)]
    or_clauses = [{"id": {"$in": incoming_ids}}]
    if obj_ids:
        or_clauses.append({"_id": {"$in": obj_ids}})
    or_clauses.append({"_id": {"$in": incoming_ids}})

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
        existing_assigned = plan_doc.get("assigned_workers", [])
        if isinstance(existing_assigned, list) and existing_assigned:
            for ew in existing_assigned:
                if isinstance(ew, dict):
                    ew_id = str(ew.get("worker_id") or "")
                    if ew_id:
                        final_assigned_map[ew_id] = ew.get("position", "normal")
        else:
            for ew_id in plan_doc.get("worker_ids", []):
                if str(ew_id).strip():
                    final_assigned_map[str(ew_id).strip()] = "normal"

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

    # 5. Automatically create / sync group chat conversation for cleaning plan
    try:
        await sync_cleaning_plan_group_conversation(
            db=db,
            plan_doc=updated_doc,
            current_manager=current_user
        )
    except Exception as e:
        print(f"Error syncing cleaning plan group conversation: {e}")

    # 6. Fire rich push & in-app notifications to all newly assigned workers
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


# ============================================================================
# Client-Requested Additional Task Approval (submitted via
# POST /client/cleaning-plan/{plan_id}/additional-tasks)
# ============================================================================

async def _recompute_plan_totals(plan_doc: dict, additional_tasks: list, db) -> dict:
    """
    Shared duration/count recompute — same formula used when a plan is created or
    updated: room durations + every additional task's duration_minutes.
    """
    rooms_data = await _resolve_rooms_data(plan_doc.get("room_ids", []), db)
    add_tasks_duration = sum(int(t.get("duration_minutes") or 0) for t in additional_tasks)
    new_duration = sum(r.duration for r in rooms_data) + add_tasks_duration
    st_time = plan_doc.get("start_time", "08:00 AM")
    new_end_time = _calculate_end_time(st_time, new_duration)

    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)
    add_photos_count = sum(len(t.get("photo", [])) for t in additional_tasks)

    return {
        "duration_minutes": new_duration,
        "end_time": new_end_time,
        "total_tasks_count": room_tasks_count + len(additional_tasks),
        "total_photos_count": room_photos_count + add_photos_count,
    }


async def _notify_requester(db, requester_id: Optional[str], title: str, message: str, notif_type: str, plan_id: str, task_id: str):
    if not requester_id:
        return
    try:
        from app.services.notification_service import NotificationService
        requester_doc = await db["users"].find_one({"$or": [{"_id": requester_id}, {"id": requester_id}]})
        player_ids = None
        if requester_doc and requester_doc.get("onesignal_player_id"):
            player_ids = [requester_doc["onesignal_player_id"]]
        await NotificationService().create_notification(
            title=title,
            message=message,
            notification_type=notif_type,
            route_type="cleaning_plans",
            recipient_type="specific",
            user_id=requester_id,
            player_ids=player_ids,
            data={"plan_id": plan_id, "task_id": task_id}
        )
    except Exception as e:
        print(f"Error notifying client ({notif_type}): {e}")


@cleaning_plan_mgmt_router.post(
    "/cleaning-plans/{plan_id}/additional-tasks/{task_id}/approve",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Approve Client-Requested Additional Task",
    description="""
### Approve a Client-Submitted Additional Task Request
Moves a `pending` entry from the plan's `pending_additional_tasks` into its real
`additional_tasks` — from this point it behaves exactly like a manager-created additional
task, and its `duration_minutes` (if any) is folded into the plan's total duration/end time.
The pending entry is kept (status flips to `approved`) as an audit trail, not deleted.
"""
)
async def approve_pending_additional_task(
    plan_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    pending_list = plan_doc.get("pending_additional_tasks", []) or []
    target = next((t for t in pending_list if str(t.get("id")) == task_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Pending additional task not found")
    if target.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already reviewed (status: {target.get('status')})")

    now = datetime.now(timezone.utc)
    manager_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", ""))
    manager_name = getattr(current_user, "full_name", None) or "Manager"

    approval_only_fields = {
        "status", "requested_by", "requested_by_name", "requested_at",
        "reviewed_by", "reviewed_by_name", "reviewed_at", "rejection_reason"
    }
    merged_task = {k: v for k, v in target.items() if k not in approval_only_fields}

    existing_additional_tasks = plan_doc.get("additional_tasks", []) or []
    new_additional_tasks = existing_additional_tasks + [merged_task]

    updated_pending = []
    for t in pending_list:
        if str(t.get("id")) == task_id:
            t = dict(t)
            t["status"] = "approved"
            t["reviewed_by"] = manager_id
            t["reviewed_by_name"] = manager_name
            t["reviewed_at"] = now
        updated_pending.append(t)

    totals = await _recompute_plan_totals(plan_doc, new_additional_tasks, db)

    update_fields = {
        "additional_tasks": new_additional_tasks,
        "pending_additional_tasks": updated_pending,
        "updated_at": now,
        **totals,
    }
    await db["cleaning_plans"].update_one(query, {"$set": update_fields})
    updated_doc = await db["cleaning_plans"].find_one(query)

    await _notify_requester(
        db, target.get("requested_by"),
        title="Additional Task Approved",
        message=f"Your request '{target.get('name')}' was approved and added to your cleaning plan.",
        notif_type="additional_task_approved",
        plan_id=plan_id, task_id=task_id
    )

    return await _format_manager_cleaning_plan_detail(updated_doc, db, current_user=current_user)


@cleaning_plan_mgmt_router.post(
    "/cleaning-plans/{plan_id}/additional-tasks/{task_id}/reject",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Reject Client-Requested Additional Task",
    description="""
### Reject a Client-Submitted Additional Task Request
Marks a `pending` entry in the plan's `pending_additional_tasks` as `rejected` with an
optional reason. It is never added to the plan's real `additional_tasks` and never affects
plan duration/end time — kept in place for the client to see why it was declined.
"""
)
async def reject_pending_additional_task(
    plan_id: str,
    task_id: str,
    reject_in: RejectPendingAdditionalTaskRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    pending_list = plan_doc.get("pending_additional_tasks", []) or []
    target = next((t for t in pending_list if str(t.get("id")) == task_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Pending additional task not found")
    if target.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already reviewed (status: {target.get('status')})")

    now = datetime.now(timezone.utc)
    manager_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", ""))
    manager_name = getattr(current_user, "full_name", None) or "Manager"

    updated_pending = []
    for t in pending_list:
        if str(t.get("id")) == task_id:
            t = dict(t)
            t["status"] = "rejected"
            t["reviewed_by"] = manager_id
            t["reviewed_by_name"] = manager_name
            t["reviewed_at"] = now
            t["rejection_reason"] = reject_in.reason
        updated_pending.append(t)

    await db["cleaning_plans"].update_one(
        query,
        {"$set": {"pending_additional_tasks": updated_pending, "updated_at": now}}
    )
    updated_doc = await db["cleaning_plans"].find_one(query)

    reason_suffix = f" Reason: {reject_in.reason}" if reject_in.reason else ""
    await _notify_requester(
        db, target.get("requested_by"),
        title="Additional Task Rejected",
        message=f"Your request '{target.get('name')}' was not approved.{reason_suffix}",
        notif_type="additional_task_rejected",
        plan_id=plan_id, task_id=task_id
    )

    return await _format_manager_cleaning_plan_detail(updated_doc, db, current_user=current_user)
