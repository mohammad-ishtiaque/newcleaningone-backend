from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Dict, Any, Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.admin_dashboard import (
    MinimalItem, ShiftSummaryGroup, WorkerSummaryGroup,
    LocationSummaryGroup, AdminDashboardHomeResponse,
    InProgressShiftItem, InProgressShiftPaginatedResponse,
    WorkerAttendanceDetailItem, WorkerAttendanceGroup, WorkerAttendanceSummaryResponse
)

admin_dashboard_router = APIRouter(prefix="/admin/dashboard", tags=["Admin Dashboard HomePage"])

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.admin, RoleEnum.super_admin, "admin", "super_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


async def _find_location_image_url(db, location_id: str) -> Optional[str]:
    loc_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
    if loc_doc and loc_doc.get("image_url"):
        return loc_doc.get("image_url")

    client_doc = await db["client_list"].find_one({"locations.id": location_id})
    if client_doc and "locations" in client_doc:
        for loc in client_doc["locations"]:
            if str(loc.get("id")) == str(location_id) or str(loc.get("_id")) == str(location_id):
                return loc.get("image_url")
    return None


@admin_dashboard_router.get(
    "/overview",
    response_model=AdminDashboardHomeResponse,
    summary="Get Admin Dashboard HomePage Overview",
    description="Returns metrics for active shifts now, today's upcoming shifts, today's completed shifts, active workers list, and active locations list. Only returns name and ID for all entities."
)
async def get_admin_dashboard_overview(
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_date_str = now_utc.date().isoformat()
    current_time_str = now_utc.strftime("%H:%M")

    # 1. Fetch Today's Shifts
    today_shifts = await db["shifts"].find({
        "date": today_date_str,
        "status": {"$ne": "cancelled"}
    }).to_list(length=1000)

    active_now_items = []
    upcoming_items = []
    completed_items = []

    for s in today_shifts:
        s_id = str(s.get("id") or s.get("_id"))
        s_name = s.get("shift_notes") or f"{s.get('client_name', 'Client')} - {s.get('location_name', 'Location')}"
        start_t = s.get("start_time", "00:00")
        end_t = s.get("end_time", "23:59")
        status_val = s.get("status", "")

        item = MinimalItem(id=s_id, name=s_name)

        if status_val == "completed" or current_time_str > end_t:
            completed_items.append(item)
        elif current_time_str < start_t:
            upcoming_items.append(item)
        else:
            active_now_items.append(item)

    # 2. Fetch Active Workers
    worker_query = {
        "$or": [{"role": RoleEnum.worker}, {"role": "worker"}],
        "is_approved": True
    }
    raw_workers = await db["users"].find(worker_query).sort("full_name", 1).to_list(length=1000)
    worker_items = []
    for w in raw_workers:
        w_id = str(w.get("_id") or w.get("id"))
        w_name = w.get("full_name") or w.get("name", "Worker")
        worker_items.append(MinimalItem(id=w_id, name=w_name))

    # 3. Fetch Active Locations from client_list & locations
    location_map = {}

    clients = await db["client_list"].find().to_list(length=1000)
    for c in clients:
        for loc in c.get("locations", []):
            loc_id = str(loc.get("id") or loc.get("_id"))
            loc_name = loc.get("name", "Location")
            if loc_id and loc_id not in location_map:
                location_map[loc_id] = loc_name

    global_locs = await db["locations"].find().to_list(length=1000)
    for g_loc in global_locs:
        loc_id = str(g_loc.get("id") or g_loc.get("_id"))
        loc_name = g_loc.get("name", "Location")
        if loc_id and loc_id not in location_map:
            location_map[loc_id] = loc_name

    location_items = [MinimalItem(id=l_id, name=l_name) for l_id, l_name in location_map.items()]

    return AdminDashboardHomeResponse(
        active_shifts_now=ShiftSummaryGroup(
            count=len(active_now_items),
            shifts=active_now_items
        ),
        todays_upcoming_shifts=ShiftSummaryGroup(
            count=len(upcoming_items),
            shifts=upcoming_items
        ),
        todays_completed_shifts=ShiftSummaryGroup(
            count=len(completed_items),
            shifts=completed_items
        ),
        active_workers=WorkerSummaryGroup(
            count=len(worker_items),
            workers=worker_items
        ),
        active_locations=LocationSummaryGroup(
            count=len(location_items),
            locations=location_items
        )
    )


@admin_dashboard_router.get(
    "/in-progress-shifts",
    response_model=InProgressShiftPaginatedResponse,
    summary="Get In-Progress / Live Cleaning Shifts (Paginated)",
    description="Returns a paginated list of live in-progress shifts currently running today, including worker details, location details with image URL, check-in status ('on_time', 'late', 'missing'), and dynamic time-based progress percentage."
)
async def get_in_progress_shifts(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_date_str = now_utc.date().isoformat()
    current_time_str = now_utc.strftime("%H:%M")

    now_hours, now_mins = now_utc.hour, now_utc.minute
    current_time_minutes = now_hours * 60 + now_mins

    query = {
        "date": today_date_str,
        "status": {"$ne": "cancelled"}
    }
    raw_shifts = await db["shifts"].find(query).sort("start_time", 1).to_list(length=1000)

    in_progress_items = []

    for s in raw_shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        l_id = s.get("location_id", "")
        l_name = s.get("location_name", "Location")
        start_t = s.get("start_time", "08:00")
        end_t = s.get("end_time", "18:00")

        try:
            sh, sm = map(int, start_t.split(":"))
            eh, em = map(int, end_t.split(":"))
            start_mins = sh * 60 + sm
            end_mins = eh * 60 + em
        except Exception:
            start_mins = 8 * 60
            end_mins = 18 * 60

        total_duration_mins = max(1, end_mins - start_mins)

        loc_img_url = await _find_location_image_url(db, l_id)

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "")
            w_pic = w.get("profile_picture")
            c_time_raw = w.get("checkin_time")
            assigned_status = w.get("status")

            if c_time_raw:
                if isinstance(c_time_raw, str):
                    c_time_raw = datetime.fromisoformat(c_time_raw)
                checkin_status = assigned_status if assigned_status in ["ontime", "late"] else "on_time"
                if checkin_status == "ontime":
                    checkin_status = "on_time"
            else:
                if current_time_minutes > start_mins:
                    checkin_status = "missing"
                else:
                    checkin_status = "on_time"

            if checkin_status == "missing":
                progress_val = 0.0
            else:
                elapsed_mins = current_time_minutes - start_mins
                progress_val = round(min(100.0, max(0.0, (elapsed_mins / total_duration_mins) * 100.0)), 1)

            progress_str = f"{int(progress_val)}%" if progress_val.is_integer() else f"{progress_val:.1f}%"

            if search:
                s_lower = search.lower()
                if not (s_lower in w_name.lower() or s_lower in l_name.lower()):
                    continue

            in_progress_items.append(InProgressShiftItem(
                shift_id=shift_id,
                worker_id=w_id,
                worker_name=w_name,
                worker_profile_picture=w_pic,
                location_id=l_id,
                location_name=l_name,
                location_picture_url=loc_img_url,
                worker_checkin_time=c_time_raw,
                shift_start_time=start_t,
                shift_end_time=end_t,
                progress=progress_val,
                progress_percentage=progress_str,
                checkin_status=checkin_status
            ))

    total_count = len(in_progress_items)
    skip = (page - 1) * limit
    paginated_items = in_progress_items[skip : skip + limit]

    return InProgressShiftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        shifts=paginated_items
    )


@admin_dashboard_router.get(
    "/worker-attendance-summary",
    response_model=WorkerAttendanceSummaryResponse,
    summary="Get Worker Attendance Summary Breakdown (Check-in, Late, Missing)",
    description="Returns breakdown metrics for today's shifts containing worker_id, name, profile picture, and worker_type for total checkin count, late worker count, and missing worker count."
)
async def get_worker_attendance_summary(
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_date_str = now_utc.date().isoformat()

    now_hours, now_mins = now_utc.hour, now_utc.minute
    current_time_minutes = now_hours * 60 + now_mins

    shifts = await db["shifts"].find({
        "date": today_date_str,
        "status": {"$ne": "cancelled"}
    }).to_list(length=1000)

    checked_in_workers = []
    late_workers = []
    missing_workers = []

    seen_checked_in = set()
    seen_late = set()
    seen_missing = set()

    for s in shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        start_t = s.get("start_time", "08:00")

        try:
            sh, sm = map(int, start_t.split(":"))
            start_mins = sh * 60 + sm
        except Exception:
            start_mins = 8 * 60

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "")
            w_pic = w.get("profile_picture")
            w_t = w.get("worker_type", "freelancer")
            c_time_raw = w.get("checkin_time")
            w_status = w.get("status")

            c_dt = datetime.fromisoformat(c_time_raw) if isinstance(c_time_raw, str) else c_time_raw

            item = WorkerAttendanceDetailItem(
                worker_id=w_id,
                name=w_name,
                profile_picture=w_pic,
                worker_type=str(w_t),
                shift_id=shift_id,
                checkin_time=c_dt
            )

            if c_time_raw and w_id not in seen_checked_in:
                checked_in_workers.append(item)
                seen_checked_in.add(w_id)

            if c_time_raw and w_status == "late" and w_id not in seen_late:
                late_workers.append(item)
                seen_late.add(w_id)

            if not c_time_raw and current_time_minutes > start_mins and w_id not in seen_missing:
                missing_workers.append(item)
                seen_missing.add(w_id)

    return WorkerAttendanceSummaryResponse(
        total_checkin=WorkerAttendanceGroup(
            count=len(checked_in_workers),
            workers=checked_in_workers
        ),
        late_workers=WorkerAttendanceGroup(
            count=len(late_workers),
            workers=late_workers
        ),
        missing_workers=WorkerAttendanceGroup(
            count=len(missing_workers),
            workers=missing_workers
        )
    )
