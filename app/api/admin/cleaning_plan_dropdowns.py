from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.client_list import (
    CleaningPlanRoomDropdownItem, CleaningPlanRoomDropdownPaginatedResponse,
    CleaningPlanWorkerDropdownItem, CleaningPlanWorkerDropdownPaginatedResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.cleaning_plan_worker_utils import (
    parse_time_to_minutes, normalize_worker_position, resolve_plan_working_days,
    batch_compute_worker_metrics, sort_workers_by_suitability
)

cleaning_plan_dropdowns_router = APIRouter(prefix="/manager", tags=["Manager Cleaning Plan Management"])


# ============================================================================
# 1. Room Dropdown for Cleaning Plan Creation (Paginated + Filter)
# ============================================================================

@cleaning_plan_dropdowns_router.get(
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


# ============================================================================
# 2. Worker Dropdown for Cleaning Plan Assignment (Availability & Smart Ranking)
# ============================================================================

@cleaning_plan_dropdowns_router.get(
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
    query_plan = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query_plan)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    plan_date_str = plan_doc.get("date", "")
    start_time_str = plan_doc.get("start_time", "08:00 AM")
    duration_minutes = plan_doc.get("duration_minutes", 60)
    repeat_shift = plan_doc.get("repeat_shift", "Does not repeat")
    repeat_days = resolve_plan_working_days(
        repeat_shift=repeat_shift,
        working_days=plan_doc.get("working_days"),
        frequency=plan_doc.get("frequency"),
        date_str=plan_date_str
    )

    plan_start_mins = parse_time_to_minutes(start_time_str)
    plan_end_mins = (plan_start_mins + duration_minutes) % 1440

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
            {"phone": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}}
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
        plan_id=plan_id,
        plan_date=plan_date_str,
        plan_start_mins=plan_start_mins,
        plan_end_mins=plan_end_mins,
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

    end_time_str = plan_doc.get("end_time") or f"{(plan_end_mins // 60) % 24:02d}:{plan_end_mins % 60:02d}"
    time_window_str = f"{start_time_str} - {end_time_str}"

    return CleaningPlanWorkerDropdownPaginatedResponse(
        plan_id=plan_id,
        plan_date=plan_date_str,
        plan_time_window=time_window_str,
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        workers=paginated_items
    )
