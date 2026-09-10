import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional, List, Dict, Any
from bson import ObjectId
from zoneinfo import ZoneInfo

from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import (
    ShiftResponse, ShiftListItemResponse, WorkerShiftPaginatedResponse,
    ShiftWorkerDetail, ShiftRoomDetail, ShiftTaskItem,
    WorkerHomeResponse, ActiveShiftHomeCard, HomeStatsCounters,
    NextShiftHomeCard, ActivityFeedItem,
    WorkerRosterResponse, WorkerRosterGroup, RosterShiftDetail
)
from app.schemas.client_list import RequiredPhotoResponse
from app.api.worker_shift_utils import (
    resolve_shift_execution, calculate_cleaning_plan_progress,
    is_plan_in_date_range, get_or_create_shift_execution,
    parse_plan_start_datetime
)
from app.core.timezone_utils import (
    now_in_tz, get_today_str, parse_plan_start_datetime,
    get_timezone, human_time_until
)
from app.dependencies.timezone import get_request_timezone
from app.api.worker_shifts_attendance import attendance_router, require_worker
from app.api.worker_shifts_execution import execution_router

worker_shift_router = APIRouter(prefix="/worker/shifts", tags=["Worker Active Shift Management"])


def _human_time_ago(dt_val, now):
    if not dt_val:
        return "Just now"
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except Exception:
            return "Just now"
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    diff = int((now - dt_val).total_seconds())
    if diff < 60:
        return "Just now"
    elif diff < 3600:
        return f"{diff // 60} mins ago"
    elif diff < 86400:
        return f"{diff // 3600} hours ago"
    else:
        return f"{diff // 86400} days ago"


def _get_time_greeting(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    elif 17 <= hour < 22:
        return "Good evening"
    else:
        return "Good night"


def _format_extra_service_as_shift_list_item(es_doc: dict, worker_id: str, worker_user_map: Optional[dict] = None) -> Optional[ShiftListItemResponse]:
    """Converts an assigned Extra Service document into a lightweight ShiftListItemResponse for short view."""
    try:
        es_id = str(es_doc.get("id") or es_doc.get("_id"))
        prio = str(es_doc.get("priority", "Medium Priority"))
        pref_date = str(es_doc.get("preferred_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        raw_tasks = es_doc.get("tasks", [])
        tot_tasks = len(raw_tasks)
        comp_tasks = sum(1 for t in raw_tasks if isinstance(t, dict) and t.get("is_completed"))

        req_photos = es_doc.get("required_photos", [])
        tot_photos = len(req_photos) + sum(len(t.get("photo", [])) for t in raw_tasks if isinstance(t, dict))

        l_id = str(es_doc.get("location_id") or "loc_es")
        l_name = str(es_doc.get("location_name") or "Main Location")

        es_status = str(es_doc.get("status", "approved")).lower()
        is_completed = (es_status in ["completed", "submitted_for_completion"] or (tot_tasks > 0 and comp_tasks == tot_tasks))

        workers_res = []
        for w in es_doc.get("assigned_workers", []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id"))
                u_doc = worker_user_map.get(wid, {}) if worker_user_map else {}
                w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
                w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_photo") or w.get("profile_picture")
                w_type = str(u_doc.get("worker_type") or w.get("worker_type") or "employee")
                workers_res.append(ShiftWorkerDetail(
                    worker_id=wid,
                    name=w_name,
                    position=str(w.get("position", "normal")),
                    worker_type=w_type,
                    profile_photo=w_pic
                ))

        total_items = tot_tasks + tot_photos
        completed_items = comp_tasks
        overall_progress = round(min(100.0, (completed_items / total_items * 100.0)), 1) if total_items > 0 else (100.0 if is_completed else 0.0)
        shift_status = "completed" if is_completed else ("in_progress" if es_status == "in_progress" else "scheduled")

        return ShiftListItemResponse(
            id=es_id,
            title=f"Extra Service: {es_doc.get('title', 'Service')}",
            client_id=str(es_doc.get("client_id") or "client_es"),
            client_name=str(es_doc.get("client_name") or "Client"),
            location_id=l_id,
            location_name=l_name,
            date=pref_date,
            start_time=str(es_doc.get("start_time") or "08:00 AM"),
            end_time=str(es_doc.get("end_time") or "10:00 AM"),
            timezone=str(es_doc.get("timezone") or "Europe/Amsterdam"),
            shift_notes=es_doc.get("description"),
            cleaning_plan_id=None,
            total_rooms_count=1 if (es_doc.get("room_id") or es_doc.get("room_name")) else 0,
            total_tasks_count=tot_tasks,
            total_photo_required=tot_photos,
            overall_progress_percentage=overall_progress,
            completed_rooms_count=1 if is_completed else 0,
            in_progress_rooms_count=1 if (not is_completed and es_status == "in_progress") else 0,
            pending_rooms_count=0 if is_completed else 1,
            service_kind="extra_service",
            status=shift_status,
            workers=workers_res,
            created_at=es_doc.get("created_at") if isinstance(es_doc.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=es_doc.get("updated_at") if isinstance(es_doc.get("updated_at"), datetime) else datetime.now(timezone.utc)
        )
    except Exception as e:
        print(f"Error formatting extra service as shift list item: {e}")
        return None


def _format_shift_list_item(doc: dict, worker_user_map: Optional[dict] = None) -> Optional[ShiftListItemResponse]:
    """Converts a Shift Execution / Direct Shift / Plan document into a lightweight ShiftListItemResponse."""
    try:
        s_id = str(doc.get("id") or doc.get("_id"))
        progress = calculate_cleaning_plan_progress(doc)

        workers_res = []
        for w in (doc.get("assigned_workers") or doc.get("workers") or []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id"))
                u_doc = worker_user_map.get(wid, {}) if worker_user_map else {}
                w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
                w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_photo") or w.get("profile_picture")
                w_type = str(u_doc.get("worker_type") or w.get("worker_type") or "employee")
                workers_res.append(ShiftWorkerDetail(
                    worker_id=wid,
                    name=w_name,
                    position=str(w.get("position", "normal")),
                    worker_type=w_type,
                    profile_photo=w_pic
                ))

        return ShiftListItemResponse(
            id=s_id,
            title=doc.get("title") or doc.get("plan_name", "Cleaning Shift"),
            draft_id=doc.get("draft_id"),
            client_id=str(doc.get("client_id") or ""),
            client_name=str(doc.get("client_name") or doc.get("company_name") or "Client"),
            location_id=str(doc.get("location_id") or ""),
            location_name=str(doc.get("location_name") or "Location"),
            date=str(doc.get("date") or ""),
            start_time=str(doc.get("start_time") or "08:00 AM"),
            end_time=str(doc.get("end_time") or "04:00 PM"),
            timezone=str(doc.get("timezone") or "Europe/Amsterdam"),
            shift_notes=doc.get("shift_notes"),
            cleaning_plan_id=doc.get("plan_id") or doc.get("cleaning_plan_id"),
            total_rooms_count=progress["total_rooms_count"],
            total_tasks_count=progress["total_tasks_count"],
            total_photo_required=progress["total_photos_count"],
            overall_progress_percentage=progress["overall_progress_percentage"],
            completed_rooms_count=progress["completed_rooms_count"],
            in_progress_rooms_count=progress["in_progress_rooms_count"],
            pending_rooms_count=progress["pending_rooms_count"],
            service_kind=doc.get("service_kind", "cleaning_plan"),
            status=str(doc.get("status", "scheduled")),
            workers=workers_res,
            created_at=doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)
        )
    except Exception as e:
        print(f"Error formatting shift list item: {e}")
        return None


def _format_extra_service_as_shift_detail(es_doc: dict, worker_id: str, worker_user_map: Optional[dict] = None) -> Optional[ShiftResponse]:
    """Converts an assigned Extra Service document into a full ShiftResponse model with rooms, tasks, and photo details."""
    try:
        es_id = str(es_doc.get("id") or es_doc.get("_id"))
        prio = str(es_doc.get("priority", "Medium Priority"))
        pref_date = str(es_doc.get("preferred_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        # Convert tasks
        room_tasks = []
        raw_tasks = es_doc.get("tasks", [])
        for t in raw_tasks:
            if isinstance(t, dict):
                room_tasks.append(ShiftTaskItem(
                    id=str(t.get("id") or f"t_{uuid.uuid4().hex[:6]}"),
                    name=str(t.get("name") or "Task"),
                    is_completed=bool(t.get("is_completed", False)),
                    completed_at=t.get("completed_at")
                ))
            elif isinstance(t, str):
                room_tasks.append(ShiftTaskItem(
                    id=f"t_{uuid.uuid4().hex[:6]}",
                    name=t,
                    is_completed=False,
                    completed_at=None
                ))

        # Convert required photos
        room_photos = []
        for p in es_doc.get("required_photos", []):
            if isinstance(p, dict):
                room_photos.append(RequiredPhotoResponse(
                    id=str(p.get("id") or f"p_{uuid.uuid4().hex[:6]}"),
                    name=str(p.get("name") or "Required Photo")
                ))
        for t in raw_tasks:
            if isinstance(t, dict):
                for p in (t.get("photo") or []):
                    if isinstance(p, dict):
                        room_photos.append(RequiredPhotoResponse(
                            id=str(p.get("id") or f"p_{uuid.uuid4().hex[:6]}"),
                            name=str(p.get("name") or "Task Photo")
                        ))

        tot_tasks = len(room_tasks)
        comp_tasks = sum(1 for t in room_tasks if t.is_completed)
        tot_photos = len(room_photos)

        r_id = str(es_doc.get("room_id") or "room_es")
        r_name = str(es_doc.get("room_name") or es_doc.get("title") or "Extra Service Area")
        l_id = str(es_doc.get("location_id") or "loc_es")
        l_name = str(es_doc.get("location_name") or "Main Location")

        es_status = str(es_doc.get("status", "approved")).lower()
        is_completed = (es_status in ["completed", "submitted_for_completion"] or (tot_tasks > 0 and comp_tasks == tot_tasks))
        room_st = "completed" if is_completed else ("in_progress" if es_status == "in_progress" else "pending")

        room_detail = ShiftRoomDetail(
            id=r_id,
            room_id=r_id,
            room_name=r_name,
            custom_room_name=r_name,
            location_id=l_id,
            location_name=l_name,
            floor=1,
            clean_type="deep_clean" if "high" in prio.lower() else "standard",
            duration=int(float(es_doc.get("estimated_hours", 1.0)) * 60),
            status=room_st,
            is_completed=is_completed,
            tasks=room_tasks,
            required_photos=room_photos,
            submitted_photos=[],
            completed_tasks_count=comp_tasks,
            task_number=tot_tasks,
            photo_number=tot_photos
        )

        workers_res = []
        for w in es_doc.get("assigned_workers", []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id"))
                u_doc = worker_user_map.get(wid, {}) if worker_user_map else {}
                w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
                w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_photo") or w.get("profile_picture")
                w_type = str(u_doc.get("worker_type") or w.get("worker_type") or "employee")
                workers_res.append(ShiftWorkerDetail(
                    worker_id=wid,
                    name=w_name,
                    position=str(w.get("position", "normal")),
                    worker_type=w_type,
                    profile_photo=w_pic
                ))

        total_items = tot_tasks + tot_photos
        completed_items = comp_tasks
        overall_progress = round(min(100.0, (completed_items / total_items * 100.0)), 1) if total_items > 0 else (100.0 if is_completed else 0.0)
        shift_status = "completed" if is_completed else ("in_progress" if es_status == "in_progress" else "scheduled")

        return ShiftResponse(
            id=es_id,
            title=f"Extra Service: {es_doc.get('title', 'Service')}",
            client_id=str(es_doc.get("client_id") or "client_es"),
            client_name=str(es_doc.get("client_name") or "Client"),
            location_id=l_id,
            location_name=l_name,
            date=pref_date,
            start_time=str(es_doc.get("start_time") or "08:00 AM"),
            end_time=str(es_doc.get("end_time") or "10:00 AM"),
            timezone=str(es_doc.get("timezone") or "Europe/Amsterdam"),
            shift_notes=es_doc.get("description"),
            cleaning_plan_id=None,
            rooms=[room_detail],
            total_rooms_count=1 if (es_doc.get("room_id") or es_doc.get("room_name")) else 0,
            total_tasks_count=tot_tasks,
            total_photo_required=tot_photos,
            overall_progress_percentage=overall_progress,
            completed_rooms_count=1 if is_completed else 0,
            in_progress_rooms_count=1 if (not is_completed and es_status == "in_progress") else 0,
            pending_rooms_count=0 if is_completed else 1,
            service_kind="extra_service",
            status=shift_status,
            workers=workers_res,
            created_at=es_doc.get("created_at") if isinstance(es_doc.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=es_doc.get("updated_at") if isinstance(es_doc.get("updated_at"), datetime) else datetime.now(timezone.utc)
        )
    except Exception as e:
        print(f"Error formatting extra service as shift detail: {e}")
        return None


async def _get_worker_user_map(docs: list, db) -> dict:
    worker_ids = set()
    for d in docs:
        for w in (d.get("assigned_workers") or d.get("workers") or []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id") or "")
                if wid:
                    worker_ids.add(wid)
        for wid in d.get("worker_ids", []):
            if wid:
                worker_ids.add(str(wid))

    w_map = {}
    if worker_ids:
        w_list = list(worker_ids)
        oid_list = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        async for u in db["users"].find({"$or": or_clauses}):
            uid_str = str(u.get("_id") or u.get("id"))
            w_map[uid_str] = u
            if "id" in u and u["id"]:
                w_map[str(u["id"])] = u
            if "_id" in u:
                w_map[str(u["_id"])] = u
    return w_map


# =====================================================================
# 1ST API: GET /worker/shifts (Short View - Paginated)
# =====================================================================
@worker_shift_router.get(
    "",
    response_model=WorkerShiftPaginatedResponse,
    summary="Get Worker Assigned Shifts",
    description="""
### Get Worker Assigned Shifts (Short View - Paginated)
Returns a paginated, lightweight list of all active shifts, recurring cleaning plans, and assigned extra services for the logged-in worker.
Excludes deeply nested room task checklists and photo requirements to provide fast list rendering.

#### Query Parameters:
- **`date`** (`str`, *Optional*, e.g. `"2026-08-26"`): Target date in `YYYY-MM-DD` format. When provided, evaluates and filters cleaning plans, shifts, and extra services active on this specific date.
- **`status_val`** (`str`, *Optional*, e.g. `"scheduled"`): Filter by shift status (`"scheduled"`, `"in_progress"`, `"completed"`, `"published"`, `"assigned"`).
- **`kind`** (`str`, *Optional*, default: `"all"`): Filter by service kind (`"all"`, `"cleaning_plan"`, `"extra_service"`).
- **`page`** (`int`, *Optional*, default: `1`): Page number (1-indexed).
- **`limit`** (`int`, *Optional*, default: `10`): Number of shifts per page (max: 100).

#### Paginated Response:
- **`total_count`**: Total matching shifts.
- **`page`**: Current page number.
- **`limit`**: Items per page.
- **`has_more`**: Boolean indicating if further pages exist.
- **`shifts`**: Array of lightweight shift items.
"""
)
@worker_shift_router.get(
    "/my-shifts",
    response_model=WorkerShiftPaginatedResponse,
    summary="Get Worker Assigned Shifts (Legacy Alias)",
    include_in_schema=False
)
async def get_my_assigned_shifts(
    date: Optional[str] = Query(None, description="Filter shifts by date (YYYY-MM-DD), e.g. 2026-08-26"),
    status_val: Optional[str] = Query(None, description="Filter shifts by status"),
    kind: Optional[str] = Query("all", description="Filter service kind: 'all', 'cleaning_plan', or 'extra_service'"),
    page: int = Query(1, ge=1, description="Page number (1-indexed, default: 1)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page (default: 10, max: 100)"),
    client_tz: ZoneInfo = Depends(get_request_timezone),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    today_str = get_today_str(client_tz)
    filter_date = date.strip() if date else None

    seen_ids = set()
    shifts_res: List[ShiftListItemResponse] = []

    all_plans = []
    past_execs = []
    direct_shifts = []
    raw_es = []

    # 1. Fetch cleaning plans for this worker
    if kind in ["all", "cleaning_plan", None]:
        cursor = db["cleaning_plans"].find({
            "status": {"$ne": "cancelled"},
            "$or": [
                {"worker_ids": worker_id},
                {"assigned_workers.worker_id": worker_id},
                {"workers.worker_id": worker_id}
            ]
        })
        all_plans = await cursor.to_list(length=200)

        # 2. Fetch from shift_executions collection
        exec_query = {
            "$or": [
                {"assigned_workers.worker_id": worker_id},
                {"workers.worker_id": worker_id},
                {"worker_ids": worker_id}
            ],
            "status": {"$ne": "cancelled"}
        }
        if filter_date:
            exec_query["date"] = filter_date

        exec_cursor = db["shift_executions"].find(exec_query).sort("date", -1)
        past_execs = await exec_cursor.to_list(length=100)

        # 3. Fetch from direct shifts collection
        shift_query = {
            "workers.worker_id": worker_id,
            "status": {"$ne": "cancelled"}
        }
        if filter_date:
            shift_query["date"] = filter_date

        shift_cursor = db["shifts"].find(shift_query).sort("date", -1)
        direct_shifts = await shift_cursor.to_list(length=100)

    # 4. Fetch assigned extra services
    if kind in ["all", "extra_service", None]:
        es_query = {
            "assigned_workers.worker_id": worker_id,
            "status": {"$ne": "cancelled"}
        }
        if filter_date:
            es_query["preferred_date"] = filter_date

        cursor_es = db["extra_services"].find(es_query).sort("preferred_date", -1)
        raw_es = await cursor_es.to_list(length=100)

    # Pre-resolve worker names from MongoDB users collection
    w_map = await _get_worker_user_map(all_plans + past_execs + direct_shifts + raw_es, db)

    for p in all_plans:
        if filter_date:
            if not is_plan_in_date_range(p, filter_date):
                continue
            target_date = filter_date
        else:
            if is_plan_in_date_range(p, today_str):
                target_date = today_str
            else:
                p_date = str(p.get("date") or "")
                if p_date and p_date >= today_str:
                    target_date = p_date
                else:
                    continue

        exec_doc = await get_or_create_shift_execution(p, target_date, db)
        s_id = str(exec_doc.get("id") or exec_doc.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            exec_doc["id"] = s_id
            exec_doc["service_kind"] = "cleaning_plan"

            if status_val and exec_doc.get("status") != status_val:
                continue

            item = _format_shift_list_item(exec_doc, w_map)
            if item:
                shifts_res.append(item)

    for ex in past_execs:
        s_id = str(ex.get("id") or ex.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            ex["id"] = s_id
            ex["service_kind"] = "cleaning_plan"

            if status_val and ex.get("status") != status_val:
                continue

            item = _format_shift_list_item(ex, w_map)
            if item:
                shifts_res.append(item)

    for ds in direct_shifts:
        s_id = str(ds.get("id") or ds.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            ds["id"] = s_id
            ds["service_kind"] = "cleaning_plan"

            if status_val and ds.get("status") != status_val:
                continue

            item = _format_shift_list_item(ds, w_map)
            if item:
                shifts_res.append(item)

    for es in raw_es:
        es_shift = _format_extra_service_as_shift_list_item(es, worker_id, w_map)
        if es_shift and es_shift.id not in seen_ids:
            if status_val and es_shift.status != status_val:
                continue
            seen_ids.add(es_shift.id)
            shifts_res.append(es_shift)

    # Sort shifts chronologically by date and start_time
    shifts_res.sort(key=lambda s: (s.date, s.start_time), reverse=False if filter_date else True)

    total_count = len(shifts_res)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paged_shifts = shifts_res[start_idx:end_idx]
    has_more = bool((page * limit) < total_count)

    return WorkerShiftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        shifts=paged_shifts
    )


from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

# =====================================================================
# 2ND API: GET /worker/shifts/{shift_id} (Full Detailed View)
# =====================================================================
@worker_shift_router.get(
    "/{shift_id}",
    response_model=ShiftResponse,
    summary="Get Worker Shift Detail",
    description="""
### Get Worker Shift Detail (Full Detailed View)
Returns full detailed shift execution information including client, location, complete room checklist, hierarchical cleaning tasks, required photo specifications, and submitted photos for a specific assigned shift or extra service.

#### Path Parameter:
- **`shift_id`** (`str`, *Required*, e.g. `"exec_plan_e3b88980fc_2026-08-26"` or `"es_35d851cfe3"`): Unique shift execution ID, cleaning plan ID, or assigned extra service ID.
"""
)
async def get_worker_shift_detail(
    shift_id: str,
    client_tz: ZoneInfo = Depends(get_request_timezone),
    current_user: UserInDB = Depends(require_worker)
):
    # Safe fallback if /home was requested
    if shift_id == "home":
        from app.api.worker_home import get_worker_home_dashboard_screen
        home_data = await get_worker_home_dashboard_screen(current_user=current_user)
        return JSONResponse(content=jsonable_encoder(home_data))

    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    if coll_name == "extra_services":
        formatted = _format_extra_service_as_shift_detail(shift_doc, worker_id)
        if not formatted:
            raise HTTPException(status_code=404, detail="Extra service shift detail could not be formatted")
        return formatted

    # Resolve workers data with real names
    w_entries = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    if not w_entries and shift_doc.get("worker_ids"):
        w_entries = [{"worker_id": wid} for wid in shift_doc["worker_ids"]]

    w_ids = [str(w.get("worker_id") or w.get("id") or "") for w in w_entries if isinstance(w, dict)]
    w_map = {}
    if w_ids:
        oid_list = [ObjectId(x) for x in w_ids if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_ids}}, {"id": {"$in": w_ids}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        async for u in db["users"].find({"$or": or_clauses}):
            uid_str = str(u.get("_id") or u.get("id"))
            w_map[uid_str] = u
            if "id" in u and u["id"]:
                w_map[str(u["id"])] = u
            if "_id" in u:
                w_map[str(u["_id"])] = u

    resolved_workers = []
    for w in w_entries:
        if isinstance(w, dict):
            wid = str(w.get("worker_id") or w.get("id") or "")
            u_doc = w_map.get(wid, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_photo") or w.get("profile_picture")
            w_type = str(u_doc.get("worker_type") or w.get("worker_type") or "employee")
            resolved_workers.append(ShiftWorkerDetail(
                worker_id=wid,
                name=w_name,
                position=str(w.get("position", "normal")),
                worker_type=w_type,
                profile_photo=w_pic,
                hours_worked=w.get("hours_worked"),
                hourly_rate=w.get("hourly_rate"),
                shift_earnings=w.get("shift_earnings")
            ))

    shift_doc["workers"] = resolved_workers
    shift_doc["service_kind"] = "cleaning_plan"

    return ShiftResponse(**shift_doc)


# Mount sub-routers for attendance and room task execution
worker_shift_router.include_router(attendance_router)
worker_shift_router.include_router(execution_router)

