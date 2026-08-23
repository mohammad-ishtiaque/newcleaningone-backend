import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import (
    ShiftResponse, ShiftWorkerDetail, ShiftRoomDetail,
    WorkerHomeResponse, ActiveShiftHomeCard, HomeStatsCounters,
    NextShiftHomeCard, ActivityFeedItem,
    WorkerRosterResponse, WorkerRosterGroup, RosterShiftDetail
)
from app.api.worker_shift_utils import (
    resolve_shift_execution, calculate_cleaning_plan_progress,
    is_plan_active_on_date, get_or_create_shift_execution,
    parse_plan_start_datetime
)
from app.core.timezone_utils import (
    now_in_tz, get_today_str, parse_plan_start_datetime,
    get_timezone, human_time_until
)
from app.dependencies.timezone import get_request_timezone
from zoneinfo import ZoneInfo
from app.api.worker_shifts_attendance import attendance_router, require_worker
from app.api.worker_shifts_execution import execution_router

worker_shift_router = APIRouter(prefix="/worker/shifts", tags=["Worker Active Shift Management"])

# Mount sub-routers for attendance and room task execution
worker_shift_router.include_router(attendance_router)
worker_shift_router.include_router(execution_router)


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


@worker_shift_router.get(
    "/home",
    response_model=WorkerHomeResponse,
    summary="Get Worker Home Screen Dashboard Data",
    description="Returns worker profile, active running shift card, stats counters (Today's Shifts, Completed, Pending), next upcoming shift card, and recent activity feed with professional timezone handling."
)
async def get_worker_home_dashboard(
    client_tz: ZoneInfo = Depends(get_request_timezone),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    tz_str = str(client_tz)
    now = now_in_tz(client_tz)
    today_str = get_today_str(client_tz)

    worker_name = getattr(current_user, "full_name", "Worker")
    profile_photo = getattr(current_user, "profile_photo", None)

    # 1. Resolve today's active cleaning plans for this worker
    cursor_plans = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor_plans.to_list(length=200)

    active_candidates = []
    upcoming_candidates = []
    todays_shifts_count = 0
    completed_count = 0
    pending_count = 0

    for p in all_plans:
        w_ids = [str(w) for w in p.get("worker_ids", [])]
        w_assigned = [str(w.get("worker_id")) for w in p.get("assigned_workers", []) if isinstance(w, dict)]
        w_workers = [str(w.get("worker_id")) for w in p.get("workers", []) if isinstance(w, dict)]
        if worker_id not in w_ids and worker_id not in w_assigned and worker_id not in w_workers:
            continue

        if is_plan_active_on_date(p, today_str):
            todays_shifts_count += 1
            exec_doc = await get_or_create_shift_execution(p, today_str, db)
            w_list = exec_doc.get("assigned_workers") or exec_doc.get("workers") or []
            w_rec = next((w for w in w_list if str(w.get("worker_id")) == worker_id), None)

            plan_title = exec_doc.get("title") or p.get("title") or p.get("plan_name", "Cleaning Shift")
            location_name = exec_doc.get("location_name") or p.get("location_name") or "Location"
            client_name = exec_doc.get("client_name") or p.get("company_name") or p.get("client_name") or "Client"

            shift_tz_str = exec_doc.get("timezone") or p.get("timezone") or tz_str
            shift_tz = get_timezone(shift_tz_str)

            start_time_str = exec_doc.get("start_time", "08:00 AM")
            end_time_str = exec_doc.get("end_time", "04:00 PM")
            start_dt = parse_plan_start_datetime(today_str, start_time_str, shift_tz)

            if w_rec and w_rec.get("checkout_time"):
                completed_count += 1
            else:
                pending_count += 1
                progress = calculate_cleaning_plan_progress(exec_doc)

                is_checked_in = bool(w_rec and w_rec.get("checkin_time"))
                is_in_time_window = (now >= (start_dt - timedelta(minutes=30)))

                if is_checked_in:
                    # Checked in shift has top priority (rank 0)
                    active_candidates.append((0, start_dt, ActiveShiftHomeCard(
                        shift_id=str(exec_doc.get("id") or exec_doc.get("_id")),
                        title=plan_title,
                        client_name=client_name,
                        location_name=location_name,
                        location_address=None,
                        start_time=start_time_str,
                        end_time=end_time_str,
                        timezone=shift_tz_str,
                        completed_rooms=progress["completed_rooms_count"],
                        total_rooms=progress["total_rooms_count"],
                        overall_progress_percentage=progress["overall_progress_percentage"],
                        status="in_progress"
                    )))
                elif is_in_time_window:
                    card_status = "late" if now > (start_dt + timedelta(minutes=15)) else "scheduled"
                    active_candidates.append((1, start_dt, ActiveShiftHomeCard(
                        shift_id=str(exec_doc.get("id") or exec_doc.get("_id")),
                        title=plan_title,
                        client_name=client_name,
                        location_name=location_name,
                        location_address=None,
                        start_time=start_time_str,
                        end_time=end_time_str,
                        timezone=shift_tz_str,
                        completed_rooms=progress["completed_rooms_count"],
                        total_rooms=progress["total_rooms_count"],
                        overall_progress_percentage=progress["overall_progress_percentage"],
                        status=card_status
                    )))
                else:
                    upcoming_candidates.append((start_dt, exec_doc, p, shift_tz_str))

    stats = HomeStatsCounters(
        todays_shifts=todays_shifts_count,
        completed=completed_count,
        pending=pending_count
    )

    active_card = None
    if active_candidates:
        active_candidates.sort(key=lambda x: (x[0], x[1]))
        active_card = active_candidates[0][2]

    # Next Shift Card (if upcoming shifts exist)
    next_card = None
    if upcoming_candidates:
        upcoming_candidates.sort(key=lambda x: x[0])
        earliest_start_dt, next_exec, next_p, next_tz_str = upcoming_candidates[0]
        time_until_str = human_time_until(earliest_start_dt, now)

        next_card = NextShiftHomeCard(
            shift_id=str(next_exec.get("id") or next_exec.get("_id")),
            title=next_exec.get("title") or next_p.get("title") or next_p.get("plan_name", "Cleaning Shift"),
            client_name=next_exec.get("client_name") or next_p.get("company_name") or "Client",
            location_name=next_exec.get("location_name") or next_p.get("location_name") or "Location",
            location_address=None,
            start_time=next_exec.get("start_time", "08:00 AM"),
            end_time=next_exec.get("end_time", "04:00 PM"),
            timezone=next_tz_str,
            time_until_start=time_until_str,
            date=today_str
        )

    # Activity feed
    activity_items = []
    recent_reviews = await db["photo_reviews"].find(
        {"cleaner.worker_id": worker_id}
    ).sort("date_submitted", -1).to_list(length=5)

    for r in recent_reviews:
        sub_dt = r.get("date_submitted", now)
        room_name = r.get("room", {}).get("name", "Room")
        activity_items.append(ActivityFeedItem(
            id=str(r.get("_id") or r.get("review_id")),
            title=f"Photo uploaded for {room_name}",
            subtitle=f"Status: {r.get('status', 'pending_review')}",
            timestamp=_human_time_ago(sub_dt, now),
            created_at=sub_dt if isinstance(sub_dt, datetime) else now
        ))

    return WorkerHomeResponse(
        worker_name=worker_name,
        profile_photo=profile_photo,
        active_shift=active_card,
        stats=stats,
        next_shift=next_card,
        timezone=tz_str,
        recent_activity=activity_items
    )


@worker_shift_router.get(
    "/my-shifts",
    response_model=List[ShiftResponse],
    summary="Get Worker Assigned Shifts",
    description="Returns all shifts assigned to the current logged-in worker."
)
async def get_my_assigned_shifts(
    status_val: Optional[str] = None,
    client_tz: ZoneInfo = Depends(get_request_timezone),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    today_str = get_today_str(client_tz)

    seen_ids = set()
    shifts_res = []

    # 1. Fetch active cleaning plans for this worker
    cursor = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor.to_list(length=200)

    for p in all_plans:
        w_ids = [str(w) for w in p.get("worker_ids", [])]
        w_assigned = [str(w.get("worker_id")) for w in p.get("assigned_workers", []) if isinstance(w, dict)]
        w_workers = [str(w.get("worker_id")) for w in p.get("workers", []) if isinstance(w, dict)]
        if worker_id not in w_ids and worker_id not in w_assigned and worker_id not in w_workers:
            continue

        target_date = today_str if is_plan_active_on_date(p, today_str) else str(p.get("date") or today_str)
        exec_doc = await get_or_create_shift_execution(p, target_date, db)
        s_id = str(exec_doc.get("id") or exec_doc.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            exec_doc["id"] = s_id
            progress = calculate_cleaning_plan_progress(exec_doc)
            exec_doc["overall_progress_percentage"] = progress["overall_progress_percentage"]
            exec_doc["completed_rooms_count"] = progress["completed_rooms_count"]
            exec_doc["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
            exec_doc["pending_rooms_count"] = progress["pending_rooms_count"]

            if status_val and exec_doc.get("status") != status_val:
                continue

            try:
                shifts_res.append(ShiftResponse(**exec_doc))
            except Exception:
                pass

    # 2. Fetch from shift_executions
    exec_cursor = db["shift_executions"].find({
        "$or": [
            {"assigned_workers.worker_id": worker_id},
            {"workers.worker_id": worker_id},
            {"worker_ids": worker_id}
        ]
    }).sort("date", -1)
    past_execs = await exec_cursor.to_list(length=100)
    for ex in past_execs:
        s_id = str(ex.get("id") or ex.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            ex["id"] = s_id
            progress = calculate_cleaning_plan_progress(ex)
            ex["overall_progress_percentage"] = progress["overall_progress_percentage"]
            ex["completed_rooms_count"] = progress["completed_rooms_count"]
            ex["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
            ex["pending_rooms_count"] = progress["pending_rooms_count"]

            if status_val and ex.get("status") != status_val:
                continue

            try:
                shifts_res.append(ShiftResponse(**ex))
            except Exception:
                pass

    # 3. Fetch from direct shifts collection
    shift_cursor = db["shifts"].find({
        "workers.worker_id": worker_id,
        "status": {"$ne": "cancelled"}
    }).sort("date", -1)
    direct_shifts = await shift_cursor.to_list(length=100)
    for ds in direct_shifts:
        s_id = str(ds.get("id") or ds.get("_id"))
        if s_id not in seen_ids:
            seen_ids.add(s_id)
            ds["id"] = s_id
            progress = calculate_cleaning_plan_progress(ds)
            ds["overall_progress_percentage"] = progress["overall_progress_percentage"]
            ds["completed_rooms_count"] = progress["completed_rooms_count"]
            ds["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
            ds["pending_rooms_count"] = progress["pending_rooms_count"]

            if status_val and ds.get("status") != status_val:
                continue

            try:
                shifts_res.append(ShiftResponse(**ds))
            except Exception:
                pass

    return shifts_res


@worker_shift_router.get(
    "/{shift_id}",
    response_model=ShiftResponse,
    summary="Get Worker Shift Detail",
    description="Returns full detailed shift execution information (including client, location, rooms, tasks, photo requirements) for a specific assigned shift."
)
async def get_worker_shift_detail(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    shift_doc["id"] = str(shift_doc.get("id") or shift_doc.get("_id"))
    progress = calculate_cleaning_plan_progress(shift_doc)
    shift_doc["overall_progress_percentage"] = progress["overall_progress_percentage"]
    shift_doc["completed_rooms_count"] = progress["completed_rooms_count"]
    shift_doc["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
    shift_doc["pending_rooms_count"] = progress["pending_rooms_count"]

    return ShiftResponse(**shift_doc)
