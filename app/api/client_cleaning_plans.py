import uuid
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, Query, Path
from typing import Optional, List, Dict, Any, Union
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.client_cleaning_plan import (
    ClientCleaningPlanListItem, ClientCleaningPlanPaginatedResponse,
    ClientCleaningPlanDetailResponse
)
from app.schemas.client_list import (
    CleaningPlanRoomDetail, CleaningPlanWorkerDetail,
    CleaningTaskResponse, TaskPhotoResponse, CleaningTaskCreate
)
from app.services.client_helper import resolve_client_id_aliases
from app.api.worker_shift_utils import (
    is_plan_active_on_date, calculate_cleaning_plan_progress,
    resolve_shift_execution
)
from app.api.admin.cleaning_plan_formatters import (
    _resolve_rooms_data, _format_tasks_list, _resolve_workers_data,
    _format_pending_tasks_list
)

router = APIRouter(tags=["Client Live Status Management"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client and current_user.role != "client":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


async def _format_plan_list_item(
    plan_doc: dict,
    db,
    target_date: Optional[str] = None,
    client_timezone: str = "Europe/Amsterdam",
    location_cache: Optional[dict] = None,
    rooms_map: Optional[dict] = None,
    workers_map: Optional[dict] = None
) -> ClientCleaningPlanListItem:
    """Formats a cleaning_plans or shift_executions MongoDB document into ClientCleaningPlanListItem."""
    pid = str(plan_doc.get("id") or plan_doc.get("_id"))
    title = plan_doc.get("title") or plan_doc.get("plan_name") or "Cleaning Plan"
    shift_notes = plan_doc.get("shift_notes") or plan_doc.get("description", "")
    loc_id = plan_doc.get("location_id")
    loc_name = plan_doc.get("location_name")

    room_ids = plan_doc.get("room_ids", [])
    worker_ids = plan_doc.get("worker_ids", [])
    workers_meta = plan_doc.get("assigned_workers") or plan_doc.get("workers") or []

    # Check for daily shift execution if target_date is given or today
    exec_doc = None
    if target_date:
        exec_id = f"exec_{pid}_{target_date}"
        exec_doc = await db["shift_executions"].find_one({"$or": [{"_id": exec_id}, {"id": exec_id}, {"cleaning_plan_id": pid, "date": target_date}]})

    active_doc = exec_doc if exec_doc else plan_doc

    # Resolve room details, names, tasks & photos count
    if rooms_map is not None:
        rooms_data = [rooms_map[str(rid)] for rid in room_ids if str(rid) in rooms_map]
    else:
        rooms_data = await _resolve_rooms_data(room_ids, db)
    room_names = [r.room_name for r in rooms_data]
    room_tasks_count = sum(len(r.tasks) for r in rooms_data)
    room_photos_count = sum(sum(len(t.photo) for t in r.tasks) for r in rooms_data)

    raw_add_tasks = active_doc.get("additional_tasks", []) or active_doc.get("tasks", [])
    add_tasks = _format_tasks_list(raw_add_tasks)
    add_tasks_count = len(add_tasks)
    add_photos_count = sum(len(t.photo) for t in add_tasks)

    tot_tasks = room_tasks_count + add_tasks_count
    tot_photos = room_photos_count + add_photos_count

    # Resolve worker names accurately
    raw_wids = list(worker_ids) + [str(w.get("worker_id") or w.get("id")) for w in workers_meta if isinstance(w, dict) and (w.get("worker_id") or w.get("id"))]
    if workers_map is not None:
        workers_data = [workers_map[str(wid)] for wid in raw_wids if str(wid) in workers_map]
    else:
        workers_data = await _resolve_workers_data(raw_wids, db, assigned_workers_meta=workers_meta)
    worker_names = [w.name for w in workers_data]

    # Calculate real-time progress if executed
    progress = calculate_cleaning_plan_progress(active_doc)
    comp_tasks = progress.get("completed_tasks_count", 0) if exec_doc else 0
    pct_progress = progress.get("overall_progress_percentage", 0.0) if exec_doc else 0.0

    # Resolve location name if missing
    if loc_id and not loc_name:
        if location_cache is not None and loc_id in location_cache:
            loc_name = location_cache[loc_id]
        else:
            l_doc = await db["locations"].find_one({"$or": [{"_id": loc_id}, {"id": loc_id}]})
            if l_doc:
                loc_name = l_doc.get("name")
                if location_cache is not None:
                    location_cache[loc_id] = loc_name

    # Real-time status resolution
    status_str = active_doc.get("status", "scheduled")
    if not exec_doc and status_str in ["assigned", "scheduled", "draft"]:
        if len(worker_names) > 0 or plan_doc.get("workers_count", 0) > 0 or len(worker_ids) > 0 or len(workers_meta) > 0:
            status_str = "assigned"
        else:
            status_str = plan_doc.get("status", "draft")

    dur_mins = active_doc.get("duration_minutes") or plan_doc.get("duration_minutes", 60)
    st_time = active_doc.get("start_time") or plan_doc.get("start_time", "08:00 AM")
    date_val = target_date or active_doc.get("date") or plan_doc.get("date")
    end_date_val = plan_doc.get("repeat_until") or date_val

    return ClientCleaningPlanListItem(
        id=pid,
        title=title,
        service_kind="cleaning_plan",
        priority=None,
        date=date_val,
        start_time=st_time,
        end_date=end_date_val,
        duration_minutes=dur_mins,
        status=status_str,
        location_id=loc_id,
        location_name=loc_name,
        rooms_count=len(rooms_data) or len(room_ids) or plan_doc.get("rooms_count", 0),
        room_names=room_names,
        workers_count=len(worker_names),
        worker_names=worker_names,
        total_tasks_count=tot_tasks,
        completed_tasks_count=comp_tasks,
        total_photos_count=tot_photos,
        overall_progress_percentage=pct_progress,
        repeat_shift=plan_doc.get("repeat_shift"),
        repeat_until=plan_doc.get("repeat_until"),
        working_days=plan_doc.get("working_days", []),
        timezone=plan_doc.get("timezone", client_timezone),
        is_active=plan_doc.get("is_active", True),
        created_at=plan_doc.get("created_at"),
        updated_at=plan_doc.get("updated_at")
    )


async def _format_extra_service_list_item(
    es_doc: dict,
    db,
    target_date: Optional[str] = None,
    client_timezone: str = "Europe/Amsterdam"
) -> ClientCleaningPlanListItem:
    """Formats an extra_services MongoDB document into ClientCleaningPlanListItem."""
    es_id = str(es_doc.get("id") or es_doc.get("_id"))
    title = es_doc.get("title") or "Extra Service"
    pref_date = es_doc.get("preferred_date") or es_doc.get("date")
    loc_id = es_doc.get("location_id")
    loc_name = es_doc.get("location_name")

    room_names = []
    if es_doc.get("room_id"):
        rooms_data = await _resolve_rooms_data([str(es_doc["room_id"])], db)
        if rooms_data:
            room_names = [r.room_name for r in rooms_data]
    if not room_names and es_doc.get("room_name"):
        room_names = [es_doc.get("room_name")]

    workers_meta = es_doc.get("assigned_workers", [])
    raw_wids = [str(w.get("worker_id") or w.get("id")) for w in workers_meta if isinstance(w, dict) and (w.get("worker_id") or w.get("id"))]
    workers_data = await _resolve_workers_data(raw_wids, db, assigned_workers_meta=workers_meta)
    worker_names = [w.name for w in workers_data]

    tasks = es_doc.get("tasks", [])
    tasks_fmt = _format_tasks_list(tasks)
    total_tasks = len(tasks_fmt)
    total_photos = sum(len(t.photo) for t in tasks_fmt) + len(es_doc.get("required_photos", []))

    # Check for shift execution if approved
    exec_doc = None
    if target_date or pref_date:
        d = target_date or pref_date
        exec_doc = await db["shift_executions"].find_one({"$or": [{"_id": f"exec_{es_id}_{d}"}, {"id": f"exec_{es_id}_{d}"}, {"shift_id": es_id}]})

    active_doc = exec_doc if exec_doc else es_doc
    progress = calculate_cleaning_plan_progress(active_doc)

    dur_mins = int((es_doc.get("estimated_hours") or 1) * 60)
    st_time = es_doc.get("start_time", "08:00 AM")

    return ClientCleaningPlanListItem(
        id=es_id,
        title=title,
        service_kind="extra_service",
        priority=es_doc.get("priority", "Medium Priority"),
        date=pref_date,
        start_time=st_time,
        end_date=pref_date,
        duration_minutes=dur_mins,
        status=es_doc.get("status", "under_review"),
        location_id=loc_id,
        location_name=loc_name,
        rooms_count=len(room_names),
        room_names=room_names,
        workers_count=len(worker_names),
        worker_names=worker_names,
        total_tasks_count=total_tasks,
        completed_tasks_count=progress.get("completed_tasks_count", 0) if exec_doc else 0,
        total_photos_count=total_photos,
        overall_progress_percentage=progress.get("overall_progress_percentage", 0.0) if exec_doc else 0.0,
        repeat_shift=None,
        repeat_until=None,
        working_days=[],
        timezone=client_timezone,
        is_active=es_doc.get("status") != "cancelled",
        created_at=es_doc.get("created_at"),
        updated_at=es_doc.get("updated_at")
    )


@router.get(
    "/client/cleaning-plan",
    response_model=ClientCleaningPlanPaginatedResponse,
    summary="Get Client Cleaning Plans & Extra Services (Paginated)",
    description="""
### Get Client Cleaning Plans & Extra Services
Returns a paginated list of regular cleaning plans and extra service requests for the authenticated client.

#### Query Filters Supported:
- **`date`**: Filter schedules active on target date (`YYYY-MM-DD`, e.g. `"2026-08-26"`). Checks recurring weekly/monthly rules and daily execution status.
- **`timezone`**: Timezone name (e.g. `"Europe/Amsterdam"`).
- **`service_kind`**: `"all"` | `"cleaning_plan"` | `"extra_service"`
- **`status_val`**: `"all"`, `"scheduled"`, `"in_progress"`, `"completed"`, `"assigned"`, `"draft"`, `"under_review"`, `"approved"`, `"rejected"`
- **`location_id`**: Filter plans by facility location ID.
- **`search`**: Text search matching plan title, room names, or location.
- **`page`** & **`limit`**: Standard pagination controls.
"""
)
@router.get(
    "/client/cleaning-plans",
    response_model=ClientCleaningPlanPaginatedResponse,
    summary="Get Client Cleaning Plans (Alias)",
    include_in_schema=False
)
async def list_client_cleaning_plans(
    date: Optional[str] = Query(None, description="Target service date (YYYY-MM-DD), e.g. '2026-08-26'", json_schema_extra={"example": "2026-08-26"}),
    timezone: str = Query("Europe/Amsterdam", description="Timezone name, e.g. 'Europe/Amsterdam'", json_schema_extra={"example": "Europe/Amsterdam"}),
    service_kind: str = Query("all", description="Filter by service kind: 'all', 'cleaning_plan', 'extra_service'", json_schema_extra={"example": "all"}),
    status_val: Optional[str] = Query(None, description="Filter by status: 'all', 'scheduled', 'in_progress', 'completed', 'assigned', 'draft', 'under_review', 'approved'", json_schema_extra={"example": "all"}),
    location_id: Optional[str] = Query(None, description="Filter by location ID"),
    search: Optional[str] = Query(None, description="Search keyword matching title, room, or location"),
    page: int = Query(1, ge=1, description="Page number", json_schema_extra={"example": 1}),
    limit: int = Query(10, ge=1, le=100, description="Items per page", json_schema_extra={"example": 10}),
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_aliases = await resolve_client_id_aliases(current_user, db)

    items: List[ClientCleaningPlanListItem] = []

    # 1. Fetch Cleaning Plans
    if service_kind.lower() in ["all", "cleaning_plan"]:
        plan_query: Dict[str, Any] = {
            "$or": [
                {"client_id": {"$in": client_aliases}},
                {"client_ids": {"$in": client_aliases}}
            ],
            "status": {"$ne": "cancelled"}
        }
        if location_id:
            plan_query["location_id"] = location_id

        cursor_p = db["cleaning_plans"].find(plan_query).sort("created_at", -1)
        raw_plans = await cursor_p.to_list(length=100)

        loc_cache = {}
        active_plans = [p for p in raw_plans if not date or is_plan_active_on_date(p, date)]
        if active_plans:
            all_rids = list({str(rid) for p in active_plans for rid in p.get("room_ids", []) if rid})
            all_wids = []
            for p in active_plans:
                for wid in p.get("worker_ids", []):
                    if wid:
                        all_wids.append(str(wid))
                for w in (p.get("assigned_workers") or p.get("workers") or []):
                    if isinstance(w, dict):
                        w_val = w.get("worker_id") or w.get("id")
                        if w_val:
                            all_wids.append(str(w_val))
            all_wids = list(set(all_wids))

            pre_rooms_task = _resolve_rooms_data(all_rids, db) if all_rids else asyncio.sleep(0, result=[])
            pre_workers_task = _resolve_workers_data(all_wids, db) if all_wids else asyncio.sleep(0, result=[])
            pre_rooms, pre_workers = await asyncio.gather(pre_rooms_task, pre_workers_task)

            rooms_map = {str(r.room_id): r for r in (pre_rooms or [])}
            workers_map = {str(w.worker_id): w for w in (pre_workers or [])}

            plan_items = await asyncio.gather(
                *[_format_plan_list_item(
                    p, db,
                    target_date=date,
                    client_timezone=timezone,
                    location_cache=loc_cache,
                    rooms_map=rooms_map,
                    workers_map=workers_map
                ) for p in active_plans]
            )
            items.extend(plan_items)

    # 2. Fetch Extra Services
    if service_kind.lower() in ["all", "extra_service"]:
        es_query: Dict[str, Any] = {
            "client_id": {"$in": client_aliases},
            "status": {"$ne": "cancelled"}
        }
        if location_id:
            es_query["location_id"] = location_id

        cursor_es = db["extra_services"].find(es_query).sort("created_at", -1)
        raw_es = await cursor_es.to_list(length=100)

        active_es = [es for es in raw_es if not date or (es.get("preferred_date") or es.get("date")) == date]
        if active_es:
            es_items = await asyncio.gather(
                *[_format_extra_service_list_item(es, db, target_date=date, client_timezone=timezone) for es in active_es]
            )
            items.extend(es_items)

    # 3. Apply status filtering
    if status_val and status_val.lower() != "all":
        st_filter = status_val.lower()
        items = [it for it in items if it.status.lower() == st_filter or (st_filter == "scheduled" and it.status.lower() in ["scheduled", "assigned", "draft"])]

    # 4. Apply search filter
    if search:
        s_low = search.lower()
        filtered = []
        for it in items:
            if (s_low in it.title.lower() or
                (it.location_name and s_low in it.location_name.lower()) or
                any(s_low in r.lower() for r in it.room_names) or
                any(s_low in w.lower() for w in it.worker_names)):
                filtered.append(it)
        items = filtered

    total_count = len(items)
    skip = (page - 1) * limit
    paged_items = items[skip:skip + limit]
    has_more = (skip + limit) < total_count

    return ClientCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        plans=paged_items
    )


async def _format_client_plan_detail(plan_doc: dict, db) -> ClientCleaningPlanDetailResponse:
    """
    Shared formatter for a `cleaning_plans` document into the client-facing detail
    shape. Used by both the GET detail endpoint and the additional-task-request
    endpoint (so a client sees the same full plan, including their new pending
    request, immediately after submitting it).
    """
    pid = str(plan_doc.get("id") or plan_doc.get("_id"))
    title = plan_doc.get("title") or plan_doc.get("plan_name", "Cleaning Plan")
    shift_notes = plan_doc.get("shift_notes") or plan_doc.get("description", "")
    cid = plan_doc.get("client_id") or (plan_doc.get("client_ids", [""])[0] if plan_doc.get("client_ids") else "")
    cname = plan_doc.get("company_name") or plan_doc.get("client_name") or "Client"

    room_ids = plan_doc.get("room_ids", [])
    worker_ids = plan_doc.get("worker_ids", [])

    rooms_data = await _resolve_rooms_data(room_ids, db)
    workers_meta = plan_doc.get("assigned_workers") or plan_doc.get("workers") or []
    workers_data = await _resolve_workers_data(worker_ids, db, assigned_workers_meta=workers_meta)

    raw_add_tasks = plan_doc.get("additional_tasks", []) or plan_doc.get("tasks", [])
    add_tasks = _format_tasks_list(raw_add_tasks)
    pending_add_tasks = _format_pending_tasks_list(plan_doc.get("pending_additional_tasks", []))

    progress = calculate_cleaning_plan_progress(plan_doc)
    dur_mins = plan_doc.get("duration_minutes") or sum(r.duration for r in rooms_data) or 60
    st_time = plan_doc.get("start_time", "08:00 AM")
    end_date_val = plan_doc.get("repeat_until") or plan_doc.get("date")

    return ClientCleaningPlanDetailResponse(
        id=pid,
        title=title,
        service_kind="cleaning_plan",
        shift_notes=shift_notes,
        priority=None,
        client_id=str(cid),
        company_name=str(cname),
        location_id=plan_doc.get("location_id"),
        location_name=plan_doc.get("location_name"),
        room_ids=room_ids,
        rooms=rooms_data,
        rooms_count=len(rooms_data),
        worker_ids=worker_ids,
        workers=workers_data,
        workers_count=len(workers_data),
        additional_tasks=add_tasks,
        pending_additional_tasks=pending_add_tasks,
        total_tasks_count=progress["total_tasks_count"],
        completed_tasks_count=progress["completed_tasks_count"],
        total_photos_count=progress["total_photos_count"],
        overall_progress_percentage=progress["overall_progress_percentage"],
        date=plan_doc.get("date"),
        start_time=st_time,
        end_date=end_date_val,
        duration_minutes=dur_mins,
        repeat_shift=plan_doc.get("repeat_shift"),
        repeat_until=plan_doc.get("repeat_until"),
        working_days=plan_doc.get("working_days", []),
        timezone=plan_doc.get("timezone", "Europe/Amsterdam"),
        status=plan_doc.get("status", "draft"),
        is_active=plan_doc.get("is_active", True),
        created_at=plan_doc.get("created_at"),
        updated_at=plan_doc.get("updated_at")
    )


@router.get(
    "/client/cleaning-plan/{plan_id}",
    response_model=ClientCleaningPlanDetailResponse,
    summary="Get Full Cleaning Plan / Extra Service Details",
    description="""
### Get Full Cleaning Plan Details for Client
Returns complete cleaning plan / extra service details including rooms breakdown, individual tasks, required photo proofs, assigned cleaners with contact info, recurrence rules, and real-time execution progress.
"""
)
@router.get(
    "/client/cleaning-plans/{plan_id}",
    response_model=ClientCleaningPlanDetailResponse,
    summary="Get Full Cleaning Plan Details (Alias)",
    include_in_schema=False
)
async def get_client_cleaning_plan_detail(
    plan_id: str = Path(..., description="Target cleaning plan ID or extra service ID", json_schema_extra={"example": "plan_1129452756"}),
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_aliases = await resolve_client_id_aliases(current_user, db)

    # 1. Look up in cleaning_plans
    plan_doc = await db["cleaning_plans"].find_one({
        "$or": [{"_id": plan_id}, {"id": plan_id}],
        "$and": [{
            "$or": [
                {"client_id": {"$in": client_aliases}},
                {"client_ids": {"$in": client_aliases}}
            ]
        }]
    })

    if plan_doc:
        return await _format_client_plan_detail(plan_doc, db)

    # 2. Look up in extra_services
    es_doc = await db["extra_services"].find_one({
        "$or": [{"_id": plan_id}, {"id": plan_id}],
        "client_id": {"$in": client_aliases}
    })

    if es_doc:
        es_id = str(es_doc.get("id") or es_doc.get("_id"))
        title = es_doc.get("title", "Extra Service")
        desc = es_doc.get("description", "")
        cid = es_doc.get("client_id", "")
        cname = es_doc.get("client_name", "Client")

        # Format extra service tasks
        raw_tasks = es_doc.get("tasks", [])
        tasks_formatted = _format_tasks_list(raw_tasks)

        rooms_list: List[CleaningPlanRoomDetail] = []
        room_ids = []
        if es_doc.get("room_id"):
            room_ids = [str(es_doc["room_id"])]
            rooms_data = await _resolve_rooms_data(room_ids, db)
            if rooms_data:
                rooms_list = rooms_data

        workers_meta = es_doc.get("assigned_workers", [])
        worker_ids = [str(w.get("worker_id")) for w in workers_meta if isinstance(w, dict) and w.get("worker_id")]
        workers_data = await _resolve_workers_data(worker_ids, db, assigned_workers_meta=workers_meta)

        progress = calculate_cleaning_plan_progress(es_doc)
        dur_mins = int((es_doc.get("estimated_hours") or 1) * 60)
        st_time = es_doc.get("start_time", "08:00 AM")

        return ClientCleaningPlanDetailResponse(
            id=es_id,
            title=title,
            service_kind="extra_service",
            shift_notes=desc,
            priority=es_doc.get("priority", "Medium Priority"),
            client_id=str(cid),
            company_name=str(cname),
            location_id=es_doc.get("location_id"),
            location_name=es_doc.get("location_name"),
            room_ids=room_ids,
            rooms=rooms_list,
            rooms_count=len(rooms_list),
            worker_ids=worker_ids,
            workers=workers_data,
            workers_count=len(workers_data),
            additional_tasks=tasks_formatted if not rooms_list else [],
            total_tasks_count=progress["total_tasks_count"] or len(tasks_formatted),
            completed_tasks_count=progress["completed_tasks_count"],
            total_photos_count=progress["total_photos_count"],
            overall_progress_percentage=progress["overall_progress_percentage"],
            date=es_doc.get("preferred_date") or es_doc.get("date"),
            start_time=st_time,
            end_date=es_doc.get("preferred_date") or es_doc.get("date"),
            duration_minutes=dur_mins,
            repeat_shift=None,
            repeat_until=None,
            working_days=[],
            timezone="Europe/Amsterdam",
            status=es_doc.get("status", "under_review"),
            is_active=es_doc.get("status") != "cancelled",
            created_at=es_doc.get("created_at"),
            updated_at=es_doc.get("updated_at")
        )

    # 3. Look up in shift_executions
    exec_doc = await db["shift_executions"].find_one({
        "$or": [{"_id": plan_id}, {"id": plan_id}],
        "client_id": {"$in": client_aliases}
    })

    if exec_doc:
        ex_id = str(exec_doc.get("id") or exec_doc.get("_id"))
        parent_pid = exec_doc.get("cleaning_plan_id")
        if parent_pid:
            # Delegate to parent plan with today's execution progress
            parent_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": parent_pid}, {"id": parent_pid}]})
            if parent_doc:
                item_res = await get_client_cleaning_plan_detail(plan_id=parent_pid, current_user=current_user)
                return item_res

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Cleaning plan or extra service ID '{plan_id}' not found for this client"
    )


@router.post(
    "/client/cleaning-plan/{plan_id}/additional-tasks",
    response_model=ClientCleaningPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Request Additional Task for Cleaning Plan",
    description="""
### Client Request Additional Task
Submits a request to add an additional task to one of the client's own cleaning plans —
the same task model (name, frequency, `fixed_date`/`weekly_days`/`monthly_dates`,
`duration_minutes`, required photos) the manager uses when creating a plan.

The request is added to the plan's `pending_additional_tasks` with `status: "pending"` and
notifies managers/admins. It does **not** appear in the plan's real `additional_tasks` (and
does not affect the plan's total duration) until a manager approves it via
`POST /manager/cleaning-plans/{plan_id}/additional-tasks/{task_id}/approve`.
"""
)
async def request_additional_task(
    plan_id: str,
    task_in: CleaningTaskCreate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_aliases = await resolve_client_id_aliases(current_user, db)

    plan_doc = await db["cleaning_plans"].find_one({
        "$or": [{"_id": plan_id}, {"id": plan_id}],
        "$and": [{
            "$or": [
                {"client_id": {"$in": client_aliases}},
                {"client_ids": {"$in": client_aliases}}
            ]
        }]
    })
    if not plan_doc:
        raise HTTPException(status_code=404, detail=f"Cleaning plan '{plan_id}' not found for this client")

    now = datetime.now(timezone.utc)
    client_name = getattr(current_user, "company_name", None) or getattr(current_user, "full_name", None) or "Client"
    client_id_str = str(getattr(current_user, "id", None) or getattr(current_user, "_id", ""))

    task_dict = task_in.model_dump()
    task_dict["id"] = task_dict.get("id") or uuid.uuid4().hex[:8]

    processed_photos = []
    for p in (task_dict.get("photo") or []):
        p_dict = p if isinstance(p, dict) else dict(p)
        if not p_dict.get("id"):
            p_dict["id"] = uuid.uuid4().hex[:8]
        processed_photos.append(p_dict)
    task_dict["photo"] = processed_photos
    if processed_photos and not task_dict.get("is_photo_req"):
        task_dict["is_photo_req"] = True

    task_dict.update({
        "status": "pending",
        "requested_by": client_id_str,
        "requested_by_name": client_name,
        "requested_at": now,
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "rejection_reason": None,
    })

    await db["cleaning_plans"].update_one(
        {"_id": plan_doc["_id"]},
        {"$push": {"pending_additional_tasks": task_dict}, "$set": {"updated_at": now}}
    )

    # Notify managers/admins — same pattern as notify_escalation_created
    try:
        from app.services.notification_service import NotificationService
        plan_title = plan_doc.get("title") or plan_doc.get("plan_name") or "Cleaning Plan"
        cursor = db["users"].find(
            {"role": {"$in": ["manager", "admin"]}, "onesignal_player_id": {"$ne": None}, "is_active": True},
            {"onesignal_player_id": 1}
        )
        player_ids = [u["onesignal_player_id"] async for u in cursor if u.get("onesignal_player_id")]
        await NotificationService().create_notification(
            title=f"New Additional Task Request: {task_dict['name']}",
            message=f"{client_name} requested an additional task for '{plan_title}'.",
            notification_type="additional_task_requested",
            route_type="cleaning_plans",
            recipient_type="manager",
            player_ids=player_ids if player_ids else None,
            data={
                "plan_id": str(plan_doc.get("id") or plan_doc.get("_id")),
                "task_id": task_dict["id"],
                "route": f"/manager/cleaning-plans/{plan_id}"
            }
        )
    except Exception as e:
        print(f"Error notifying managers of additional task request: {e}")

    updated_doc = await db["cleaning_plans"].find_one({"_id": plan_doc["_id"]})
    return await _format_client_plan_detail(updated_doc, db)
