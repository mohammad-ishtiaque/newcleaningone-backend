import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift_monitoring import (
    LiveStatusItem, LiveStatusResponse,
    AttendanceTrackingItem, AttendanceTrackingPaginatedResponse,
    LocationStatItem, LocationStatPaginatedResponse,
    WorkerShiftDetailItem, WorkerShiftStatsResponse,
    WorkerDailyActivityRow, WorkerDailyActivityResponse
)

shift_monitoring_router = APIRouter(prefix="/admin/shift-monitoring", tags=["Admin Shift Monitoring"])

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.admin, RoleEnum.super_admin, "admin", "super_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


def _get_period_date_range(period: str) -> tuple:
    today = datetime.now(timezone.utc).date()
    if period == "today":
        start_date = today
        end_date = today
    elif period == "weekly":
        start_date = today - timedelta(days=6)
        end_date = today
    else:  # monthly / default
        start_date = today - timedelta(days=29)
        end_date = today
    return start_date.isoformat(), end_date.isoformat()


@shift_monitoring_router.get(
    "/live-status",
    response_model=LiveStatusResponse,
    summary="Get Live Shift Monitoring Status",
    description="Returns live status of worker attendance for today's shifts (or specified date) with counts of ontime, late, and missing workers."
)
async def get_live_shift_monitoring(
    page: int = 1,
    limit: int = 10,
    date_val: Optional[str] = None,
    checkin_status: Optional[str] = None,  # all, ontime, late, missing
    worker_type: Optional[str] = None,      # all, employee, freelancer
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    target_date = date_val or datetime.now(timezone.utc).date().isoformat()
    now_utc = datetime.now(timezone.utc)

    # Query all active shifts on target_date
    query = {"date": target_date, "status": {"$ne": "cancelled"}}
    shifts = await db["shifts"].find(query).to_list(length=1000)

    all_items = []
    ontime_cnt = 0
    late_cnt = 0
    missing_cnt = 0

    for s in shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        c_id = s.get("client_id", "")
        c_name = s.get("client_name", "")
        l_id = s.get("location_id", "")
        l_name = s.get("location_name", "")
        s_start = s.get("start_time", "00:00")
        s_end = s.get("end_time", "23:59")

        # Parse shift start datetime
        try:
            sh, sm = map(int, s_start.split(":"))
            sy, smon, sd = map(int, target_date.split("-"))
            shift_start_dt = datetime(sy, smon, sd, sh, sm, tzinfo=timezone.utc)
        except Exception:
            shift_start_dt = now_utc

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "")
            w_pic = w.get("profile_picture")
            w_t = w.get("worker_type", "freelancer")

            c_time = w.get("checkin_time")
            co_time = w.get("checkout_time")
            assigned_status = w.get("status")

            # Determine live status
            if c_time:
                status_label = assigned_status if assigned_status in ["ontime", "late"] else "ontime"
            else:
                if now_utc > shift_start_dt:
                    status_label = "missing"
                else:
                    status_label = "scheduled"

            if status_label == "ontime":
                ontime_cnt += 1
            elif status_label == "late":
                late_cnt += 1
            elif status_label == "missing":
                missing_cnt += 1

            # Apply filters
            if checkin_status and checkin_status.lower() != "all":
                if status_label != checkin_status.lower():
                    continue

            if worker_type and worker_type.lower() != "all":
                if w_t.lower() != worker_type.lower():
                    continue

            if search:
                s_lower = search.lower()
                if not (s_lower in w_name.lower() or s_lower in c_name.lower() or s_lower in l_name.lower()):
                    continue

            all_items.append(LiveStatusItem(
                worker_id=w_id,
                worker_name=w_name,
                worker_type=w_t,
                profile_picture=w_pic,
                shift_id=shift_id,
                shift_name=s.get("shift_notes"),
                location_id=l_id,
                location_name=l_name,
                client_id=c_id,
                client_name=c_name,
                shift_start_time=s_start,
                shift_end_time=s_end,
                checkin_time=c_time,
                checkout_time=co_time,
                status=status_label
            ))

    total_shifts_count = len(all_items)
    skip = (page - 1) * limit
    paginated_items = all_items[skip : skip + limit]

    return LiveStatusResponse(
        total_shifts_count=total_shifts_count,
        ontime_count=ontime_cnt,
        late_count=late_cnt,
        missing_count=missing_cnt,
        page=page,
        limit=limit,
        items=paginated_items
    )


@shift_monitoring_router.get(
    "/attendance-time-tracking",
    response_model=AttendanceTrackingPaginatedResponse,
    summary="Get Attendance & Time Tracking (Screenshot 1 Tab)",
    description="Returns worker attendance and time tracking metrics (hours worked, total shifts, late days) for the selected period ('today', 'weekly', 'monthly')."
)
async def get_attendance_time_tracking(
    period: str = "monthly",
    worker_type: Optional[str] = None,  # all, employee, freelancer
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    start_date, end_date = _get_period_date_range(period)

    # Query approved workers
    user_query = {
        "$or": [{"role": RoleEnum.worker}, {"role": "worker"}],
        "is_approved": True
    }
    if worker_type and worker_type.lower() != "all":
        user_query["$and"] = [
            {"$or": [
                {"worker_type": {"$regex": f"^{worker_type}$", "$options": "i"}},
                {"onboarding_draft.worker_type": {"$regex": f"^{worker_type}$", "$options": "i"}}
            ]}
        ]

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        search_condition = {"$or": [{"full_name": search_regex}, {"email": search_regex}, {"phone": search_regex}]}
        if "$and" in user_query:
            user_query["$and"].append(search_condition)
        else:
            user_query["$and"] = [search_condition]

    workers_cursor = db["users"].find(user_query).sort("full_name", 1)
    raw_workers = await workers_cursor.to_list(length=1000)

    # Fetch shifts within period date range
    shifts_in_period = await db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=2000)

    worker_items = []
    for w in raw_workers:
        w_id = str(w.get("_id") or w.get("id"))
        w_name = w.get("full_name", "")
        w_pic = w.get("profile_photo")
        w_t = w.get("worker_type") or w.get("onboarding_draft", {}).get("worker_type", "freelancer")
        if hasattr(w_t, "value"):
            w_t = w_t.value

        total_hours = 0.0
        total_shifts = 0
        late_days = 0

        for s in shifts_in_period:
            for assigned_w in s.get("workers", []):
                assigned_w_id = str(assigned_w.get("worker_id") or assigned_w.get("id"))
                if assigned_w_id == w_id:
                    total_shifts += 1

                    # Add worked hours
                    hw = assigned_w.get("hours_worked", 0.0)
                    if hw:
                        total_hours += float(hw)
                    else:
                        # Standard fallback shift duration if not explicitly logged
                        total_hours += 8.0

                    if assigned_w.get("status") == "late":
                        late_days += 1

        formatted_hours = f"{int(total_hours)}h" if total_hours.is_integer() else f"{total_hours:.1f}h"

        worker_items.append(AttendanceTrackingItem(
            worker_id=w_id,
            worker_name=w_name,
            profile_picture=w_pic,
            worker_type=str(w_t),
            hours_worked=formatted_hours,
            hours_worked_numeric=round(total_hours, 1),
            total_shifts=total_shifts,
            late_days=late_days
        ))

    total_count = len(worker_items)
    skip = (page - 1) * limit
    paginated = worker_items[skip : skip + limit]

    return AttendanceTrackingPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        workers=paginated
    )


@shift_monitoring_router.get(
    "/location-statistics",
    response_model=LocationStatPaginatedResponse,
    summary="Get Location Statistics (Screenshot 2 Tab)",
    description="Returns aggregated location metrics (distinct workers count, total hours worked, total shifts count) for the selected period ('today', 'weekly', 'monthly')."
)
async def get_location_statistics(
    period: str = "monthly",
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    start_date, end_date = _get_period_date_range(period)

    # Pipeline to aggregate locations across client_list
    shifts_in_period = await db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=2000)

    # Group shifts by location_id
    location_groups: Dict[str, Dict[str, Any]] = {}

    for s in shifts_in_period:
        loc_id = s.get("location_id")
        loc_name = s.get("location_name", "Location")
        c_id = s.get("client_id", "")
        c_name = s.get("client_name", "")

        if not loc_id:
            continue

        if loc_id not in location_groups:
            location_groups[loc_id] = {
                "location_id": loc_id,
                "location_name": loc_name,
                "client_id": c_id,
                "client_name": c_name,
                "worker_ids": set(),
                "total_hours": 0.0,
                "shifts_count": 0
            }

        group = location_groups[loc_id]
        group["shifts_count"] += 1

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            group["worker_ids"].add(w_id)
            hw = w.get("hours_worked", 8.0)
            group["total_hours"] += float(hw if hw else 8.0)

    loc_items = []
    for loc_id, g in location_groups.items():
        if search:
            s_lower = search.lower()
            if not (s_lower in g["location_name"].lower() or s_lower in g["client_name"].lower()):
                continue

        hours_val = g["total_hours"]
        formatted_hours = f"{int(hours_val)}h" if hours_val.is_integer() else f"{hours_val:.1f}h"

        loc_items.append(LocationStatItem(
            location_id=loc_id,
            location_name=g["location_name"],
            client_id=g["client_id"],
            client_name=g["client_name"],
            workers_count=len(g["worker_ids"]),
            hours_worked=formatted_hours,
            hours_worked_numeric=round(hours_val, 1),
            shifts_count=g["shifts_count"]
        ))

    total_count = len(loc_items)
    skip = (page - 1) * limit
    paginated = loc_items[skip : skip + limit]

    return LocationStatPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=paginated
    )


@shift_monitoring_router.get(
    "/worker-stats/{worker_id}",
    response_model=WorkerShiftStatsResponse,
    summary="Get Specific Worker Detailed Statistics (Red Box Detail)",
    description="Returns detailed attendance and shift statistics for a specific worker over a given period ('today', 'weekly', 'monthly')."
)
async def get_worker_detailed_stats(
    worker_id: str,
    period: str = "monthly",
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    start_date, end_date = _get_period_date_range(period)

    obj_id = ObjectId(worker_id) if ObjectId.is_valid(worker_id) else worker_id
    w_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": worker_id}]})
    if not w_doc:
        raise HTTPException(status_code=404, detail="Worker user not found")

    w_name = w_doc.get("full_name", "")
    w_pic = w_doc.get("profile_photo")
    w_t = w_doc.get("worker_type") or w_doc.get("onboarding_draft", {}).get("worker_type", "freelancer")
    if hasattr(w_t, "value"):
        w_t = w_t.value

    # Query worker shifts in date range
    shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).sort("date", -1).to_list(length=1000)

    shift_details = []
    total_hours = 0.0
    late_days = 0

    for s in shifts:
        s_id = str(s.get("id") or s.get("_id"))
        c_name = s.get("client_name", "")
        l_name = s.get("location_name", "")
        d_str = s.get("date", "")
        st = s.get("start_time", "")
        et = s.get("end_time", "")

        w_record = {}
        for w in s.get("workers", []):
            if str(w.get("worker_id") or w.get("id")) == worker_id:
                w_record = w
                break

        dur = float(w_record.get("hours_worked", 8.0) or 8.0)
        total_hours += dur
        w_status = w_record.get("status", "ontime")
        if w_status == "late":
            late_days += 1

        shift_details.append(WorkerShiftDetailItem(
            shift_id=s_id,
            client_name=c_name,
            location_name=l_name,
            date=d_str,
            start_time=st,
            end_time=et,
            checkin_time=w_record.get("checkin_time"),
            checkout_time=w_record.get("checkout_time"),
            duration_hours=round(dur, 2),
            status=w_status
        ))

    shifts_count = len(shift_details)
    avg_duration = (total_hours / shifts_count) if shifts_count > 0 else 0.0

    formatted_total_hours = f"{int(total_hours)}h" if total_hours.is_integer() else f"{total_hours:.1f}h"
    formatted_avg = f"{round(avg_duration, 1)}h"

    return WorkerShiftStatsResponse(
        worker_id=worker_id,
        worker_name=w_name,
        profile_picture=w_pic,
        worker_type=str(w_t),
        period=period,
        total_hours_worked=formatted_total_hours,
        total_hours_worked_numeric=round(total_hours, 1),
        total_shifts_count=shifts_count,
        late_days_count=late_days,
        avg_shift_duration=formatted_avg,
        shifts=shift_details
    )


@shift_monitoring_router.get(
    "/worker-daily-activity",
    response_model=WorkerDailyActivityResponse,
    summary="Get Worker Daily Activity (Lisa Visser Screenshot Detail)",
    description="Returns a worker's daily activity breakdown for a specific month (default current month in YYYY-MM format e.g., '2026-06'). Includes total hours worked, attendance %, late days, absent days, and daily check-in/check-out logs."
)
async def get_worker_daily_activity(
    worker_id: str,
    month: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()

    now_utc = datetime.now(timezone.utc)
    if not month:
        month_iso = now_utc.strftime("%Y-%m")
    else:
        month_iso = month.strip()

    try:
        dt_month = datetime.strptime(month_iso, "%Y-%m")
        month_label = dt_month.strftime("%B %Y")
    except Exception:
        dt_month = now_utc
        month_iso = now_utc.strftime("%Y-%m")
        month_label = now_utc.strftime("%B %Y")

    obj_id = ObjectId(worker_id) if ObjectId.is_valid(worker_id) else worker_id
    w_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": worker_id}]})
    if not w_doc:
        raise HTTPException(status_code=404, detail="Worker user not found")

    w_name = w_doc.get("full_name", "")
    w_pic = w_doc.get("profile_photo")
    w_t = w_doc.get("worker_type") or w_doc.get("onboarding_draft", {}).get("worker_type", "freelancer")
    if hasattr(w_t, "value"):
        w_t = w_t.value

    shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": {"$regex": f"^{month_iso}"},
        "status": {"$ne": "cancelled"}
    }).sort("date", -1).to_list(length=1000)

    daily_rows = []
    total_hours_worked = 0.0
    late_days_count = 0
    absent_days_count = 0
    attended_shifts_count = 0

    for s in shifts:
        s_id = str(s.get("id") or s.get("_id"))
        d_str = s.get("date", "")

        formatted_date = d_str
        try:
            parsed_d = datetime.strptime(d_str, "%Y-%m-%d")
            formatted_date = f"{parsed_d.day} {parsed_d.strftime('%b %Y')}"
        except Exception:
            pass

        w_record = {}
        for w in s.get("workers", []):
            if str(w.get("worker_id") or w.get("id")) == worker_id:
                w_record = w
                break

        c_time_raw = w_record.get("checkin_time")
        co_time_raw = w_record.get("checkout_time")
        status_val = w_record.get("status")

        c_time_str = "--:--"
        if c_time_raw:
            if isinstance(c_time_raw, str):
                c_time_raw = datetime.fromisoformat(c_time_raw)
            c_time_str = c_time_raw.strftime("%H:%M")

        co_time_str = "--:--"
        if co_time_raw:
            if isinstance(co_time_raw, str):
                co_time_raw = datetime.fromisoformat(co_time_raw)
            co_time_str = co_time_raw.strftime("%H:%M")

        dur = float(w_record.get("hours_worked", 0.0) or 0.0)
        if not dur and c_time_raw:
            dur = 8.0

        total_hours_worked += dur

        if status_val == "late":
            status_badge = "Late"
            late_days_count += 1
            attended_shifts_count += 1
        elif c_time_raw or status_val == "ontime":
            status_badge = "On Time"
            attended_shifts_count += 1
        else:
            status_badge = "Absent"
            absent_days_count += 1

        formatted_dur = f"{int(dur)}h" if dur.is_integer() else f"{dur:.1f}h"

        daily_rows.append(WorkerDailyActivityRow(
            shift_id=s_id,
            date=formatted_date,
            date_iso=d_str,
            check_in_time=c_time_str,
            check_out_time=co_time_str,
            total_hours=formatted_dur,
            total_hours_numeric=round(dur, 2),
            status=status_badge
        ))

    total_shifts_count = len(shifts)
    attendance_pct = (attended_shifts_count / total_shifts_count * 100.0) if total_shifts_count > 0 else 100.0

    formatted_total_hours = f"{int(total_hours_worked)}h" if total_hours_worked.is_integer() else f"{total_hours_worked:.1f}h"
    formatted_pct = f"{int(attendance_pct)}%" if attendance_pct.is_integer() else f"{attendance_pct:.1f}%"

    return WorkerDailyActivityResponse(
        worker_id=worker_id,
        worker_name=w_name,
        profile_picture=w_pic,
        worker_type=str(w_t),
        month=month_label,
        month_iso=month_iso,
        total_hours_worked=formatted_total_hours,
        total_hours_worked_numeric=round(total_hours_worked, 1),
        attendance_percentage=formatted_pct,
        attendance_percentage_numeric=round(attendance_pct, 1),
        late_days=late_days_count,
        absent_days=absent_days_count,
        total_shifts=total_shifts_count,
        daily_activity=daily_rows
    )
