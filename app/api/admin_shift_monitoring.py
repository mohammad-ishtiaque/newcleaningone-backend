import uuid
import asyncio
from datetime import datetime, timezone as dt_timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift_monitoring import (
    LiveStatusItem, LiveStatusResponse,
    AttendanceTrackingItem, AttendanceTrackingPaginatedResponse,
    LocationStatItem, LocationStatPaginatedResponse
)
from app.api.worker_shift_utils import (
    is_plan_active_on_date, evaluate_worker_attendance_status, calculate_cleaning_plan_progress,
    get_or_create_shift_execution, evaluate_auto_checkout_and_hours
)
from app.core.timezone_utils import parse_time_to_minutes, get_timezone
from app.api.admin_shift_monitoring_worker_stats import worker_stats_router, require_manager, _get_period_date_range

shift_monitoring_router = APIRouter(prefix="/manager/shift-monitoring", tags=["Manager Shift Monitoring"])

# Mount worker stats and activity drawer sub-router
shift_monitoring_router.include_router(worker_stats_router)


@shift_monitoring_router.get(
    "/live-status",
    response_model=LiveStatusResponse,
    summary="Get Live Shift Monitoring Status",
    description="""
Returns live status and attendance tracking of workers for currently active cleaning shifts (shifts occurring right now where `start_time <= current_time <= end_time`, or where a worker is actively checked in on-site).

### Key Features & Business Rules:
- **Live Filtering**: Only displays shifts actively in-progress right now, filtering out future shifts (e.g., afternoon/evening) and past completed shifts.
- **Attendance Evaluation (with 15-min grace period)**:
  - `ontime`: Worker checked in within 15 minutes of scheduled start time.
  - `late`: Worker checked in after 15-minute grace period, OR shift has started past grace period and worker has not checked in yet.
  - `missing`: Shift has ended and worker never checked in.
  - `scheduled`: Shift is ongoing within the 15-minute grace window, worker has not yet checked in.
- **Supported Query Filters**:
  - `checkin_status`: `all`, `ontime`, `late`, `missing`, `scheduled`
  - `worker_type`: `all`, `employee`, `freelancer`
  - `search`: Searches worker name, client company, location, or shift title.
  - `timezone`: Custom timezone string (e.g. `Asia/Dhaka`, `Europe/Amsterdam`). Defaults to manager profile or local server timezone.
- **Live Counts**: Provides aggregated counts of `ontime_count`, `late_count`, and `missing_count` across all live shifts.
- **Blocker Diagnostics**: Computes real-time uncompleted task counts and pending/rejected photo review blockers for checkout eligibility.
"""
)
async def get_live_shift_monitoring(
    page: int = Query(1, ge=1, description="Page number for pagination", example=1),
    limit: int = Query(10, ge=1, le=100, description="Number of items per page", example=10),
    date_val: Optional[str] = Query(None, description="Optional target date in YYYY-MM-DD format. Defaults to current date.", example="2026-09-04"),
    checkin_status: Optional[str] = Query(None, description="Filter workers by attendance status. Supported values: 'all', 'ontime', 'late', 'missing', 'scheduled'.", example="all"),
    worker_type: Optional[str] = Query(None, description="Filter workers by employment type. Supported values: 'all', 'employee', 'freelancer'.", example="all"),
    search: Optional[str] = Query(None, description="Case-insensitive search filter matching worker name, client company, location, or shift title.", example="Sadim"),
    timezone: Optional[str] = Query(None, description="Manager or local timezone (e.g. 'Asia/Dhaka', 'Europe/Amsterdam', '+06:00').", example="Asia/Dhaka"),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()

    # Determine effective timezone:
    if timezone and str(timezone).strip():
        eff_tz = get_timezone(timezone)
        eff_tz_str = str(timezone).strip()
    elif getattr(current_user, "timezone", None):
        eff_tz = get_timezone(current_user.timezone)
        eff_tz_str = str(current_user.timezone).strip()
    else:
        sys_offset = datetime.now().astimezone().strftime("%z")
        if sys_offset and len(sys_offset) == 5:
            eff_tz_str = sys_offset[:3] + ":" + sys_offset[3:]
        else:
            eff_tz_str = "+00:00"
        eff_tz = get_timezone(eff_tz_str)

    now_local = datetime.now(eff_tz)
    today_str = now_local.strftime("%Y-%m-%d")
    target_date = (date_val or today_str).strip()
    is_today = (target_date == today_str)
    current_time_minutes = now_local.hour * 60 + now_local.minute
    now_utc = datetime.now(dt_timezone.utc)

    # 1. Fetch active cleaning plans, existing shift executions, and legacy shifts in parallel with projection
    plans_task = db["cleaning_plans"].find(
        {"status": {"$ne": "cancelled"}},
        {"rooms": 0, "additional_tasks": 0, "additional_required_photos": 0}
    ).to_list(length=500)
    execs_task = db["shift_executions"].find(
        {"date": target_date, "status": {"$ne": "cancelled"}}
    ).to_list(length=500)
    legacy_task = db["shifts"].find(
        {"date": target_date, "status": {"$ne": "cancelled"}}
    ).to_list(length=500)
    all_plans, existing_execs, legacy_shifts = await asyncio.gather(plans_task, execs_task, legacy_task)

    # 2. Build in-memory lookup map for existing executions (O(1) lookup, eliminates N+1 queries)
    existing_map = {}
    for ex in existing_execs:
        pid = str(ex.get("plan_id") or "")
        if pid:
            existing_map[pid] = ex
        eid = str(ex.get("id") or ex.get("_id") or "")
        if eid:
            existing_map[eid] = ex

    raw_shifts = []
    seen_shift_ids = set()
    create_tasks = []

    for p in all_plans:
        p_id = str(p.get("id") or p.get("_id"))
        if is_plan_active_on_date(p, target_date):
            if p_id in existing_map:
                doc = existing_map[p_id]
                sid = str(doc.get("id") or doc.get("_id"))
                if sid not in seen_shift_ids:
                    seen_shift_ids.add(sid)
                    raw_shifts.append(doc)
            else:
                p_start = p.get("start_time", "08:00 AM")
                p_end = p.get("end_time") or "04:00 PM"
                p_start_m = parse_time_to_minutes(p_start)
                p_end_m = parse_time_to_minutes(p_end)
                if p_end_m < p_start_m:
                    p_end_m += 1440
                if not is_today or (p_start_m <= current_time_minutes <= p_end_m):
                    create_tasks.append(get_or_create_shift_execution(p, target_date, db))

    if create_tasks:
        created_execs = await asyncio.gather(*create_tasks)
        for doc in created_execs:
            sid = str(doc.get("id") or doc.get("_id"))
            if sid not in seen_shift_ids:
                seen_shift_ids.add(sid)
                raw_shifts.append(doc)

    for ex in existing_execs:
        sid = str(ex.get("id") or ex.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ex)

    for ls in legacy_shifts:
        sid = str(ls.get("id") or ls.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ls)

    # 3. Filter LIVE shifts first (start_time <= current_time <= end_time OR actively checked in)
    live_shifts = []
    for s in raw_shifts:
        if s.get("status") in ["completed", "cancelled"]:
            continue

        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time") or "04:00 PM"
        start_mins = parse_time_to_minutes(start_t)
        end_mins = parse_time_to_minutes(end_t)
        if end_mins < start_mins:
            end_mins += 1440

        worker_entries = s.get("assigned_workers") or s.get("workers") or []
        if not worker_entries and s.get("worker_ids"):
            worker_entries = [{"worker_id": wid} for wid in s.get("worker_ids")]

        has_checked_in = any(
            isinstance(w, dict) and w.get("checkin_time") and not w.get("checkout_time")
            for w in worker_entries
        )
        all_checked_out = (
            len(worker_entries) > 0 and
            all(isinstance(w, dict) and w.get("checkout_time") for w in worker_entries)
        )

        if is_today:
            curr_mins = current_time_minutes
            if end_mins > 1440 and curr_mins < start_mins:
                curr_mins += 1440

            is_in_time_window = (start_mins <= curr_mins <= end_mins)
            is_live = (is_in_time_window or has_checked_in) and not all_checked_out
        else:
            is_live = True

        if is_live:
            live_shifts.append((s, start_mins, end_mins, worker_entries))

    # 4. Batch fetch user profiles ONLY for workers in live shifts
    live_worker_ids = set()
    for _, _, _, w_entries in live_shifts:
        for w in w_entries:
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id") or "").strip()
                if wid:
                    live_worker_ids.add(wid)

    worker_user_map = {}
    if live_worker_ids:
        w_list = list(live_worker_ids)
        oid_list = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        u_cursor = db["users"].find(
            {"$or": or_clauses},
            {
                "full_name": 1, "name": 1,
                "profile_photo": 1, "profile_picture": 1,
                "worker_type": 1, "onboarding_draft.worker_type": 1
            }
        )
        async for u in u_cursor:
            uid_str = str(u.get("_id") or u.get("id"))
            worker_user_map[uid_str] = u
            if "id" in u and u["id"]:
                worker_user_map[str(u["id"])] = u
            if "_id" in u:
                worker_user_map[str(u["_id"])] = u

    # 5. Build live status items and aggregate attendance counters
    all_items = []
    ontime_cnt = 0
    late_cnt = 0
    missing_cnt = 0

    for exec_doc, start_mins, end_mins, worker_entries in live_shifts:
        shift_id = str(exec_doc.get("id") or exec_doc.get("_id") or "")
        c_id = str(exec_doc.get("client_id") or "")
        c_name = exec_doc.get("client_name") or exec_doc.get("company_name") or "Client"
        l_id = str(exec_doc.get("location_id") or "")
        l_name = exec_doc.get("location_name") or "Location"
        s_start = exec_doc.get("start_time", "08:00 AM")
        s_end = exec_doc.get("end_time") or "04:00 PM"
        s_title = exec_doc.get("title") or exec_doc.get("shift_name") or exec_doc.get("plan_name") or ""
        s_date = exec_doc.get("date") or target_date

        if not exec_doc.get("timezone"):
            exec_doc["timezone"] = eff_tz_str

        # Calculate item-based progress %
        progress_info = calculate_cleaning_plan_progress(exec_doc)
        progress_pct = progress_info["overall_progress_percentage"]

        # Calculate checkout blockers
        rooms = exec_doc.get("rooms", [])
        pending_approvals = 0
        rejected_photos = 0
        uncompleted_tasks = 0
        pending_photos = 0
        blocker_reasons = []

        for r in rooms:
            for t in r.get("tasks", []):
                if not t.get("is_completed"):
                    uncompleted_tasks += 1
                for p in t.get("photo", []) + t.get("required_photos", []):
                    p_st = p.get("status", "not_uploaded")
                    if p_st == "pending_review":
                        pending_approvals += 1
                        pending_photos += 1
                    elif p_st == "rejected":
                        rejected_photos += 1
                        pending_photos += 1
                    elif p_st == "not_uploaded":
                        pending_photos += 1
            for p in r.get("required_photos", []):
                p_st = p.get("status", "not_uploaded")
                if p_st == "pending_review":
                    pending_approvals += 1
                elif p_st == "rejected":
                    rejected_photos += 1

        if uncompleted_tasks > 0:
            blocker_reasons.append(f"{uncompleted_tasks} task(s) uncompleted")
        if pending_approvals > 0:
            blocker_reasons.append(f"{pending_approvals} photo(s) pending approval")
        if rejected_photos > 0:
            blocker_reasons.append(f"{rejected_photos} photo(s) rejected")

        checkout_blocked_reason = ", ".join(blocker_reasons) if blocker_reasons else None
        can_checkout = (uncompleted_tasks == 0 and pending_approvals == 0 and rejected_photos == 0)

        for w_record in worker_entries:
            w_id = str(w_record.get("worker_id") or w_record.get("id") or "")
            if not w_id:
                continue

            u_doc = worker_user_map.get(w_id, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w_record.get("name", "Worker")
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w_record.get("profile_photo") or w_record.get("profile_picture")
            w_type_raw = u_doc.get("worker_type") or u_doc.get("onboarding_draft", {}).get("worker_type") or w_record.get("worker_type") or "employee"
            w_type = (w_type_raw.value if hasattr(w_type_raw, "value") else str(w_type_raw)).lower()
            w_pos = w_record.get("position", "normal")

            c_time = w_record.get("checkin_time")
            co_time = w_record.get("checkout_time")

            if isinstance(c_time, str):
                try:
                    c_time = datetime.fromisoformat(c_time)
                except Exception:
                    pass
            if isinstance(co_time, str):
                try:
                    co_time = datetime.fromisoformat(co_time)
                except Exception:
                    pass

            if c_time and hasattr(c_time, "tzinfo") and c_time.tzinfo is None:
                c_time = c_time.replace(tzinfo=dt_timezone.utc)
            if co_time and hasattr(co_time, "tzinfo") and co_time.tzinfo is None:
                co_time = co_time.replace(tzinfo=dt_timezone.utc)

            # Evaluate ontime, late, missing, scheduled
            status_label = evaluate_worker_attendance_status(
                plan_doc=exec_doc,
                worker_record={"checkin_time": c_time, "checkout_time": co_time},
                now_utc=now_utc,
                target_date_str=target_date
            )

            if status_label == "ontime":
                ontime_cnt += 1
            elif status_label == "late":
                late_cnt += 1
            elif status_label == "missing":
                missing_cnt += 1

            # Hours worked & auto-checkout evaluation
            resolved_co_time, hours_worked_num, hours_worked_disp, is_auto_co = evaluate_auto_checkout_and_hours(
                plan_doc=exec_doc,
                worker_record={"checkin_time": c_time, "checkout_time": co_time},
                now_utc=now_utc,
                target_date_str=target_date
            )
            final_co_time = co_time or resolved_co_time

            # Filter by checkin_status
            if checkin_status and checkin_status.lower() != "all":
                if status_label != checkin_status.lower():
                    continue

            # Filter by worker_type
            if worker_type and worker_type.lower() != "all":
                if w_type != worker_type.lower():
                    continue

            # Filter by search
            if search:
                s_lower = search.lower()
                if not (s_lower in w_name.lower() or s_lower in c_name.lower() or s_lower in l_name.lower() or s_lower in s_title.lower()):
                    continue

            all_items.append(LiveStatusItem(
                worker_id=w_id,
                worker_name=w_name,
                worker_type=w_type,
                profile_photo=w_pic,
                profile_picture=w_pic,
                position=w_pos,
                shift_id=shift_id,
                shift_name=s_title,
                date=s_date,
                shift_date=s_date,
                location_id=l_id,
                location_name=l_name,
                client_id=c_id,
                client_name=c_name,
                shift_start_time=s_start,
                shift_end_time=s_end,
                checkin_time=c_time,
                checkout_time=final_co_time,
                hours_worked_display=hours_worked_disp,
                hours_worked_numeric=hours_worked_num,
                progress_percentage=progress_pct,
                status=status_label,
                pending_approval_count=pending_approvals,
                pending_photos_count=pending_photos,
                rejected_photos_count=rejected_photos,
                uncompleted_tasks_count=uncompleted_tasks,
                checkout_blocked_reason=checkout_blocked_reason,
                can_checkout=can_checkout
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
        has_more=bool((page * limit) < total_shifts_count),
        items=paginated_items
    )


@shift_monitoring_router.get(
    "/attendance-time-tracking",
    response_model=AttendanceTrackingPaginatedResponse,
    summary="Get Attendance & Time Tracking",
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

    shift_proj = {
        "assigned_workers": 1,
        "workers": 1,
        "date": 1,
        "status": 1
    }
    workers_task = db["users"].find(user_query).sort("full_name", 1).to_list(length=1000)
    execs_task = db["shift_executions"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }, projection=shift_proj).to_list(length=2000)
    legacy_task = db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }, projection=shift_proj).to_list(length=2000)

    raw_workers, shifts_in_period, legacy_shifts = await asyncio.gather(workers_task, execs_task, legacy_task)
    all_shifts_period = shifts_in_period + legacy_shifts

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

        for s in all_shifts_period:
            w_list = s.get("assigned_workers") or s.get("workers") or []
            for assigned_w in w_list:
                assigned_w_id = str(assigned_w.get("worker_id") or assigned_w.get("id"))
                if assigned_w_id == w_id:
                    total_shifts += 1

                    hw = assigned_w.get("hours_worked", 0.0)
                    if hw:
                        total_hours += float(hw)
                    else:
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
    summary="Get Location Statistics",
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

    loc_proj = {
        "location_id": 1,
        "location_name": 1,
        "client_id": 1,
        "client_name": 1,
        "assigned_workers": 1,
        "workers": 1,
        "date": 1
    }
    execs_task = db["shift_executions"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }, projection=loc_proj).to_list(length=2000)

    legacy_task = db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }, projection=loc_proj).to_list(length=2000)

    shifts_in_period, legacy_shifts = await asyncio.gather(execs_task, legacy_task)
    all_shifts_period = shifts_in_period + legacy_shifts


    location_groups: Dict[str, Dict[str, Any]] = {}

    for s in all_shifts_period:
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

        w_list = s.get("assigned_workers") or s.get("workers") or []
        for w in w_list:
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
