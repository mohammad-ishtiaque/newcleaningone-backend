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
    is_plan_active_on_date, get_or_create_shift_execution
)
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
    description="Returns greeting, worker profile, active running shift card, stats counters (Today's Shifts, Completed, Pending), next upcoming shift card, and recent activity feed."
)
async def get_worker_home_dashboard(
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    greeting_str = _get_time_greeting(now)
    worker_name = getattr(current_user, "full_name", "Worker")
    profile_photo = getattr(current_user, "profile_photo", None)

    # 1. Resolve today's active cleaning plans for this worker
    cursor_plans = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor_plans.to_list(length=200)

    active_card = None
    todays_shifts_count = 0
    completed_count = 0
    pending_count = 0

    for p in all_plans:
        w_ids = [str(w) for w in p.get("worker_ids", [])]
        w_assigned = [str(w.get("worker_id")) for w in p.get("assigned_workers", []) if isinstance(w, dict)]
        if worker_id not in w_ids and worker_id not in w_assigned:
            continue

        if is_plan_active_on_date(p, today_str):
            todays_shifts_count += 1
            exec_doc = await get_or_create_shift_execution(p, today_str, db)
            w_list = exec_doc.get("assigned_workers", [])
            w_rec = next((w for w in w_list if str(w.get("worker_id")) == worker_id), None)

            if w_rec and w_rec.get("checkout_time"):
                completed_count += 1
            elif w_rec and w_rec.get("checkin_time"):
                progress = calculate_cleaning_plan_progress(exec_doc)
                if not active_card:
                    active_card = ActiveShiftHomeCard(
                        shift_id=str(exec_doc.get("id") or exec_doc.get("_id")),
                        client_name=exec_doc.get("client_name", "Client"),
                        location_name=exec_doc.get("location_name", "Location"),
                        location_address=None,
                        start_time=exec_doc.get("start_time", "08:00 AM"),
                        end_time=exec_doc.get("end_time", "04:00 PM"),
                        completed_rooms=progress["completed_rooms_count"],
                        total_rooms=progress["total_rooms_count"],
                        overall_progress_percentage=progress["overall_progress_percentage"],
                        status="in_progress"
                    )
            else:
                pending_count += 1

    stats = HomeStatsCounters(
        todays_shifts=todays_shifts_count,
        completed=completed_count,
        pending=pending_count
    )

    # Next Shift Card (if no active card, show next upcoming shift)
    next_card = None
    if not active_card and todays_shifts_count > 0:
        for p in all_plans:
            if is_plan_active_on_date(p, today_str):
                exec_doc = await get_or_create_shift_execution(p, today_str, db)
                next_card = NextShiftHomeCard(
                    shift_id=str(exec_doc.get("id") or exec_doc.get("_id")),
                    client_name=exec_doc.get("client_name", "Client"),
                    location_name=exec_doc.get("location_name", "Location"),
                    location_address=None,
                    start_time=exec_doc.get("start_time", "08:00 AM"),
                    end_time=exec_doc.get("end_time", "04:00 PM"),
                    time_until_start="Starting today",
                    date=today_str
                )
                break

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
        greeting=greeting_str,
        worker_name=worker_name,
        profile_photo=profile_photo,
        active_shift=active_card,
        stats=stats,
        next_shift=next_card,
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
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cursor = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor.to_list(length=200)

    shifts_res = []
    for p in all_plans:
        w_ids = [str(w) for w in p.get("worker_ids", [])]
        w_assigned = [str(w.get("worker_id")) for w in p.get("assigned_workers", []) if isinstance(w, dict)]
        if worker_id not in w_ids and worker_id not in w_assigned:
            continue

        if is_plan_active_on_date(p, today_str):
            exec_doc = await get_or_create_shift_execution(p, today_str, db)
            exec_doc["id"] = str(exec_doc.get("id") or exec_doc.get("_id"))
            progress = calculate_cleaning_plan_progress(exec_doc)
            exec_doc["overall_progress_percentage"] = progress["overall_progress_percentage"]
            exec_doc["completed_rooms_count"] = progress["completed_rooms_count"]
            exec_doc["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
            exec_doc["pending_rooms_count"] = progress["pending_rooms_count"]

            if status_val and exec_doc.get("status") != status_val:
                continue

            shifts_res.append(ShiftResponse(**exec_doc))

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
