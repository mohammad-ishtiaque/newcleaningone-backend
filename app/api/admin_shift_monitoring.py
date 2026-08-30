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
    LocationStatItem, LocationStatPaginatedResponse
)
from app.api.worker_shift_utils import (
    is_plan_active_on_date, evaluate_worker_attendance_status, calculate_cleaning_plan_progress,
    get_or_create_shift_execution, evaluate_auto_checkout_and_hours
)
from app.api.admin_shift_monitoring_worker_stats import worker_stats_router, require_manager, _get_period_date_range

shift_monitoring_router = APIRouter(prefix="/manager/shift-monitoring", tags=["Manager Shift Monitoring"])

# Mount worker stats and activity drawer sub-router
shift_monitoring_router.include_router(worker_stats_router)


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
    target_date = (date_val or datetime.now(timezone.utc).date().isoformat()).strip()
    now_utc = datetime.now(timezone.utc)

    # 1. Fetch all active cleaning plans from cleaning_plans collection
    cursor_plans = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor_plans.to_list(length=1000)

    # Also load existing shift executions and legacy shifts for target_date
    exec_cursor = db["shift_executions"].find({"date": target_date, "status": {"$ne": "cancelled"}})
    existing_execs = await exec_cursor.to_list(length=1000)
    legacy_shifts = await db["shifts"].find({"date": target_date, "status": {"$ne": "cancelled"}}).to_list(length=1000)
    seen_plan_ids = set()

    # Filter plans active on target_date
    active_plans = []
    for p in all_plans:
        p_id = str(p.get("id") or p.get("_id"))
        if is_plan_active_on_date(p, target_date):
            seen_plan_ids.add(p_id)
            active_plans.append(p)

    for ex in existing_execs:
        ex_id = str(ex.get("id") or ex.get("_id"))
        p_id = str(ex.get("plan_id") or "")
        if ex_id not in seen_plan_ids and (not p_id or p_id not in seen_plan_ids):
            seen_plan_ids.add(ex_id)
            active_plans.append(ex)

    for ls in legacy_shifts:
        ls_id = str(ls.get("id") or ls.get("_id"))
        if ls_id not in seen_plan_ids:
            seen_plan_ids.add(ls_id)
            active_plans.append(ls)

    # Collect all worker IDs to batch fetch user details
    all_worker_ids = set()
    for p in active_plans:
        for wid in p.get("worker_ids", []):
            if str(wid).strip():
                all_worker_ids.add(str(wid).strip())
        for w_entry in (p.get("assigned_workers") or []):
            if isinstance(w_entry, dict) and w_entry.get("worker_id"):
                all_worker_ids.add(str(w_entry["worker_id"]).strip())
        for w_entry in (p.get("workers") or []):
            if isinstance(w_entry, dict):
                wid = str(w_entry.get("worker_id") or w_entry.get("id") or "")
                if wid:
                    all_worker_ids.add(wid)

    worker_user_map = {}
    if all_worker_ids:
        w_list = list(all_worker_ids)
        oid_list = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        u_cursor = db["users"].find({"$or": or_clauses})
        async for u in u_cursor:
            uid_str = str(u.get("_id") or u.get("id"))
            worker_user_map[uid_str] = u
            if "id" in u and u["id"]:
                worker_user_map[str(u["id"])] = u
            if "_id" in u:
                worker_user_map[str(u["_id"])] = u

    all_items = []
    ontime_cnt = 0
    late_cnt = 0
    missing_cnt = 0

    for s in active_plans:
        # Get or create dedicated daily shift execution document
        exec_doc = await get_or_create_shift_execution(s, target_date, db)
        shift_id = str(exec_doc.get("id") or exec_doc.get("_id") or s.get("id") or s.get("_id"))
        c_id = str(exec_doc.get("client_id") or "")
        c_name = exec_doc.get("client_name") or "Client"
        l_id = str(exec_doc.get("location_id") or "")
        l_name = exec_doc.get("location_name") or "Location"
        s_start = exec_doc.get("start_time", "08:00 AM")
        s_end = exec_doc.get("end_time", "04:00 PM")
        s_title = exec_doc.get("title") or s.get("title") or s.get("plan_name") or ""
        s_date = exec_doc.get("date") or target_date

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

        worker_entries = (
            exec_doc.get("assigned_workers") or
            exec_doc.get("workers") or
            s.get("assigned_workers") or
            s.get("workers") or []
        )
        if not worker_entries and (exec_doc.get("worker_ids") or s.get("worker_ids")):
            worker_entries = [{"worker_id": wid} for wid in (exec_doc.get("worker_ids") or s.get("worker_ids"))]

        for w_record in worker_entries:
            w_id = str(w_record.get("worker_id") or w_record.get("id") or "")
            if not w_id:
                continue

            u_doc = worker_user_map.get(w_id, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w_record.get("name", "Worker")
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w_record.get("profile_photo") or w_record.get("profile_picture")
            w_type = str(u_doc.get("worker_type") or w_record.get("worker_type") or "employee").lower()
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
                c_time = c_time.replace(tzinfo=timezone.utc)
            if co_time and hasattr(co_time, "tzinfo") and co_time.tzinfo is None:
                co_time = co_time.replace(tzinfo=timezone.utc)

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

            # Apply filters
            if checkin_status and checkin_status.lower() != "all":
                if status_label != checkin_status.lower():
                    continue

            if worker_type and worker_type.lower() != "all":
                if w_type != worker_type.lower():
                    continue

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

    workers_cursor = db["users"].find(user_query).sort("full_name", 1)
    raw_workers = await workers_cursor.to_list(length=1000)

    # Fetch shifts within period date range from shift_executions and legacy shifts
    cursor_execs = db["shift_executions"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    })
    shifts_in_period = await cursor_execs.to_list(length=2000)

    legacy_shifts = await db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=2000)

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

    shifts_in_period = await db["shift_executions"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=2000)

    legacy_shifts = await db["shifts"].find({
        "date": {"$gte": start_date, "$lte": end_date},
        "status": {"$ne": "cancelled"}
    }).to_list(length=2000)

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
