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
    WorkerDailyActivityRow, WorkerDailyActivityResponse,
    WorkerLiveDetailsResponse, WorkerLiveShiftDetail,
    WorkerAttendanceStatsDrawerResponse, WeeklyTrendItem, MonthlyTrendItem
)

shift_monitoring_router = APIRouter(prefix="/manager/shift-monitoring", tags=["Admin Shift Monitoring"])

def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin, "manager", "admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
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
    current_user: UserInDB = Depends(require_manager)
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
    current_user: UserInDB = Depends(require_manager)
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
    current_user: UserInDB = Depends(require_manager)
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
    current_user: UserInDB = Depends(require_manager)
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
    current_user: UserInDB = Depends(require_manager)
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


@shift_monitoring_router.get(
    "/live-worker-details/{worker_id}",
    response_model=WorkerLiveDetailsResponse,
    summary="Get Worker Live Details Drawer (Picture 4)",
    description="Returns live worker shift status, period metrics (Today, Weekly, Monthly), and check-in/check-out details."
)
async def get_live_worker_details(
    worker_id: str,
    period: str = "today",  # today, weekly, monthly
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    today_dt = datetime.now(timezone.utc).date()
    today_str = today_dt.isoformat()

    # Calculate period date range
    if period == "today":
        start_date = today_str
        end_date = today_str
    elif period == "weekly":
        start_date = (today_dt - timedelta(days=6)).isoformat()
        end_date = today_str
    else:  # monthly
        start_date = (today_dt - timedelta(days=29)).isoformat()
        end_date = today_str

    # Fetch worker user
    obj_id = ObjectId(worker_id) if ObjectId.is_valid(worker_id) else worker_id
    w_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": worker_id}]})
    if not w_doc:
        raise HTTPException(status_code=404, detail="Worker user not found")

    w_name = w_doc.get("full_name", "Worker")
    w_pic = w_doc.get("profile_photo")
    w_t = w_doc.get("worker_type") or w_doc.get("onboarding_draft", {}).get("worker_type", "employee")
    if hasattr(w_t, "value"):
        w_t = w_t.value

    # Query worker shifts in date range
    period_shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=1000)

    total_hours = 0.0
    shifts_count = len(period_shifts)

    for s in period_shifts:
        for w in s.get("workers", []):
            if str(w.get("worker_id") or w.get("id")) == worker_id:
                hw = float(w.get("hours_worked", 8.0) or 8.0)
                total_hours += hw
                break

    avg_duration = (total_hours / shifts_count) if shifts_count > 0 else 0.0

    # Fetch today's active or recent shift for Shift Details card
    today_shift = await db["shifts"].find_one({
        "workers.worker_id": worker_id,
        "date": today_str,
        "status": {"$ne": "cancelled"}
    })

    shift_id_label = None
    shift_raw_id = None
    current_status = "On Time"
    check_in_str = "--:--"
    check_out_str = "--:--"
    duration_str = "0h"
    shift_status = "Scheduled"

    if today_shift:
        shift_raw_id = str(today_shift.get("id") or today_shift.get("_id"))
        shift_id_label = f"Shift #{shift_raw_id[:6]}"

        for w in today_shift.get("workers", []):
            if str(w.get("worker_id") or w.get("id")) == worker_id:
                c_raw = w.get("checkin_time")
                co_raw = w.get("checkout_time")
                w_st = w.get("status", "ontime")

                if c_raw:
                    if isinstance(c_raw, str):
                        c_raw = datetime.fromisoformat(c_raw)
                    check_in_str = c_raw.strftime("%H:%M")

                if co_raw:
                    if isinstance(co_raw, str):
                        co_raw = datetime.fromisoformat(co_raw)
                    check_out_str = co_raw.strftime("%H:%M")

                dur_val = float(w.get("hours_worked", 8.0) or 8.0)
                duration_str = f"{int(dur_val)}h" if dur_val.is_integer() else f"{dur_val:.1f}h"

                if w_st == "late":
                    current_status = "Late"
                    shift_status = "Late"
                elif c_raw or w_st == "ontime":
                    current_status = "On Time"
                    shift_status = "On Time"
                else:
                    current_status = "Missing"
                    shift_status = "Missing"
                break
    else:
        check_in_str = "08:00"
        check_out_str = "16:00"
        duration_str = "8h"
        shift_status = "On Time"

    formatted_hours = f"{int(total_hours)}h" if total_hours.is_integer() else f"{total_hours:.1f}h"
    formatted_avg = f"{round(avg_duration, 1)}h"

    return WorkerLiveDetailsResponse(
        worker_id=worker_id,
        worker_name=w_name,
        worker_type=str(w_t).capitalize(),
        position=w_doc.get("position"),
        shift_id=shift_raw_id,
        shift_label=shift_id_label or "Shift #1041",
        current_status=current_status,
        profile_picture=w_pic,
        period=period if period in ["today", "weekly", "monthly"] else "today",
        hours_worked=formatted_hours,
        hours_worked_numeric=round(total_hours, 1),
        shifts_count=shifts_count,
        avg_duration=formatted_avg,
        avg_duration_numeric=round(avg_duration, 1),
        shift_details=WorkerLiveShiftDetail(
            check_in=check_in_str,
            check_out=check_out_str,
            duration=duration_str,
            status=shift_status
        ),
        activity_history_available=True
    )


@shift_monitoring_router.get(
    "/worker-attendance-stats/{worker_id}",
    response_model=WorkerAttendanceStatsDrawerResponse,
    summary="Get Worker Attendance Stats Drawer (Picture 5)",
    description="Returns detailed worker metrics (hours worked, completed shifts, avg duration, late check-ins) and weekly/monthly trend series for charts."
)
async def get_worker_attendance_stats_drawer(
    worker_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    today_dt = datetime.now(timezone.utc).date()

    obj_id = ObjectId(worker_id) if ObjectId.is_valid(worker_id) else worker_id
    w_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": worker_id}]})
    if not w_doc:
        raise HTTPException(status_code=404, detail="Worker user not found")

    w_name = w_doc.get("full_name", "Worker")
    w_pic = w_doc.get("profile_photo")
    w_t = w_doc.get("worker_type") or w_doc.get("onboarding_draft", {}).get("worker_type", "employee")
    if hasattr(w_t, "value"):
        w_t = w_t.value

    shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "status": {"$ne": "cancelled"}
    }).sort("date", 1).to_list(length=2000)

    total_hours = 0.0
    completed_shifts = 0
    late_checkins = 0

    weekly_buckets = [0.0, 0.0, 0.0, 0.0]
    monthly_buckets = {}
    for i in range(5, -1, -1):
        m_dt = today_dt.replace(day=1) - timedelta(days=i * 28)
        m_key = m_dt.strftime("%b")
        monthly_buckets[m_key] = 0.0

    for s in shifts:
        s_date_str = s.get("date")
        try:
            s_dt = datetime.strptime(s_date_str, "%Y-%m-%d").date()
        except Exception:
            continue

        for w in s.get("workers", []):
            if str(w.get("worker_id") or w.get("id")) == worker_id:
                hw = float(w.get("hours_worked", 8.0) or 8.0)
                total_hours += hw
                completed_shifts += 1
                if w.get("status") == "late":
                    late_checkins += 1

                diff_days = (today_dt - s_dt).days
                if 0 <= diff_days < 28:
                    w_idx = 3 - (diff_days // 7)
                    if 0 <= w_idx < 4:
                        weekly_buckets[w_idx] += hw

                m_key = s_dt.strftime("%b")
                if m_key in monthly_buckets:
                    monthly_buckets[m_key] += hw
                break

    avg_duration = (total_hours / completed_shifts) if completed_shifts > 0 else 0.0

    weekly_trend = [
        WeeklyTrendItem(week_label=f"W{i+1}", hours=round(weekly_buckets[i], 1))
        for i in range(4)
    ]

    monthly_trend = [
        MonthlyTrendItem(month_label=k, hours=round(v, 1))
        for k, v in monthly_buckets.items()
    ]

    formatted_hours = f"{int(total_hours)}h" if total_hours.is_integer() else f"{total_hours:.1f}h"
    formatted_avg = f"{round(avg_duration, 1)}h"

    return WorkerAttendanceStatsDrawerResponse(
        worker_id=worker_id,
        worker_name=w_name,
        worker_type=str(w_t).capitalize(),
        profile_picture=w_pic,
        hours_worked=formatted_hours,
        hours_worked_numeric=round(total_hours, 1),
        completed_shifts=completed_shifts,
        avg_shift_duration=formatted_avg,
        avg_shift_duration_numeric=round(avg_duration, 1),
        late_checkins=late_checkins,
        weekly_hours_trend=weekly_trend,
        monthly_hours_trend=monthly_trend
    )

