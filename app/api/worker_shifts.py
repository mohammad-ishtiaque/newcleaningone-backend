import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.schemas.shift import (
    ShiftResponse, WorkerRosterResponse, WorkerRosterGroup, RosterShiftDetail,
    ShiftExecutionStateResponse, PhotoUploadResponse, ShiftRoomDetail,
    WorkerHomeResponse, ActiveShiftHomeCard, NextShiftHomeCard, HomeStatsCounters, ActivityFeedItem
)
from app.schemas.shift_monitoring import (
    WorkerCheckInResponse, WorkerCheckOutResponse,
    WorkerAttendanceToggleResponse, WorkerShiftStateResponse
)

worker_shift_router = APIRouter(prefix="/worker/shifts", tags=["Workers Shift Management"])

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _eval_auto_checkout(shift_doc: dict, w_record: dict, now: datetime) -> dict:
    """Helper to check if a worker checkin needs auto checkout at 23:59:59 of shift date."""
    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if c_time and not co_time:
        if isinstance(c_time, str):
            c_time = datetime.fromisoformat(c_time)
        if c_time and c_time.tzinfo is None:
            c_time = c_time.replace(tzinfo=timezone.utc)

        shift_date_str = shift_doc.get("date")
        try:
            sy, smon, sd = map(int, shift_date_str.split("-"))
            auto_co_dt = datetime(sy, smon, sd, 23, 59, 59, tzinfo=timezone.utc)

            if now > auto_co_dt and c_time:
                duration_seconds = (auto_co_dt - c_time).total_seconds()
                h_worked = round(max(0.0, duration_seconds / 3600.0), 2)
                w_record["checkout_time"] = auto_co_dt
                w_record["hours_worked"] = h_worked
                w_record["is_auto_checked_out"] = True
        except Exception:
            pass

    return w_record


def _calculate_shift_progress(shift_doc: dict) -> dict:
    rooms = shift_doc.get("rooms", [])
    total_rooms = len(rooms)
    completed_rooms = 0
    in_progress_rooms = 0
    pending_rooms = 0

    total_tasks = 0
    completed_tasks = 0

    for r in rooms:
        r_status = r.get("status", "pending")
        if r_status == "completed":
            completed_rooms += 1
        elif r_status in ["in_progress", "photo_submitted"]:
            in_progress_rooms += 1
        else:
            pending_rooms += 1

        r_tasks = r.get("tasks", [])
        total_tasks += len(r_tasks)
        completed_tasks += sum(1 for t in r_tasks if t.get("is_completed"))

    if total_rooms > 0:
        overall_progress = round((completed_rooms / total_rooms) * 100.0, 1)
    elif total_tasks > 0:
        overall_progress = round((completed_tasks / total_tasks) * 100.0, 1)
    else:
        overall_progress = 0.0

    return {
        "overall_progress_percentage": overall_progress,
        "total_rooms_count": total_rooms,
        "completed_rooms_count": completed_rooms,
        "in_progress_rooms_count": in_progress_rooms,
        "pending_rooms_count": pending_rooms,
        "total_tasks_count": total_tasks,
        "completed_tasks_count": completed_tasks
    }


def _get_time_greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "Good Morning"
    elif hour < 17:
        return "Good Afternoon"
    else:
        return "Good Evening"


def _human_time_ago(dt: datetime, now: datetime) -> str:
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return "Recently"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff_seconds = max(0, int((now - dt).total_seconds()))
    if diff_seconds < 60:
        return "Just now"
    minutes = diff_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


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
    worker_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    greeting_str = _get_time_greeting(now)
    worker_name = getattr(current_user, "full_name", "Worker")
    profile_photo = getattr(current_user, "profile_photo", None)

    # 1. Active Running Shift
    active_shift_doc = await db["shifts"].find_one({
        "workers.worker_id": worker_id,
        "status": {"$in": ["running", "in_progress"]}
    })
    if not active_shift_doc:
        active_shift_doc = await db["shifts"].find_one({
            "workers": {
                "$elemMatch": {
                    "worker_id": worker_id,
                    "checkin_time": {"$ne": None},
                    "checkout_time": None
                }
            }
        })

    active_card = None
    active_shift_id = None
    if active_shift_doc:
        active_shift_id = str(active_shift_doc.get("_id") or active_shift_doc.get("id"))
        progress = _calculate_shift_progress(active_shift_doc)

        loc_addr = None
        c_id = active_shift_doc.get("client_id")
        if c_id:
            c_query = {"_id": ObjectId(c_id)} if ObjectId.is_valid(c_id) else {"_id": c_id}
            c_doc = await db["client_list"].find_one(c_query)
            if c_doc and "locations" in c_doc:
                l_id = active_shift_doc.get("location_id")
                target_loc = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(l_id)), None)
                if target_loc:
                    loc_addr = target_loc.get("address")

        active_card = ActiveShiftHomeCard(
            shift_id=active_shift_id,
            client_name=active_shift_doc.get("client_name", "Client"),
            location_name=active_shift_doc.get("location_name", "Location"),
            location_address=loc_addr,
            start_time=active_shift_doc.get("start_time", "08:00"),
            end_time=active_shift_doc.get("end_time", "16:00"),
            completed_rooms=progress["completed_rooms_count"],
            total_rooms=progress["total_rooms_count"],
            overall_progress_percentage=progress["overall_progress_percentage"],
            status=active_shift_doc.get("status", "running")
        )

    # 2. Stats Counters
    todays_count = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "date": today_str
    })
    completed_count = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "status": "completed"
    })
    pending_count = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "status": {"$in": ["published", "upcoming", "draft"]}
    })

    stats = HomeStatsCounters(
        todays_shifts=todays_count,
        completed=completed_count,
        pending=pending_count
    )

    # 3. Next Shift Card
    next_shift_query = {
        "workers.worker_id": worker_id,
        "date": {"$gte": today_str},
        "status": {"$in": ["published", "upcoming"]}
    }
    if active_shift_id:
        next_shift_query["_id"] = {"$ne": active_shift_id}

    next_cursor = db["shifts"].find(next_shift_query).sort([("date", 1), ("start_time", 1)])
    next_shift_doc = await next_cursor.to_list(length=1)
    if not next_shift_doc and active_shift_id:
        del next_shift_query["_id"]
        next_shift_doc = await db["shifts"].find({"workers.worker_id": worker_id, "status": {"$in": ["published", "upcoming"]}}).sort([("date", 1), ("start_time", 1)]).to_list(length=1)

    next_card = None
    if next_shift_doc and len(next_shift_doc) > 0:
        ns = next_shift_doc[0]
        ns_date_str = ns.get("date", today_str)
        ns_start_str = ns.get("start_time", "09:00")

        time_until_str = "Upcoming"
        try:
            shour, smin = map(int, ns_start_str.split(":"))
            syear, smonth, sday = map(int, ns_date_str.split("-"))
            shift_dt = datetime(syear, smonth, sday, shour, smin, tzinfo=timezone.utc)
            diff_hours = int((shift_dt - now).total_seconds() / 3600)
            if diff_hours <= 0:
                time_until_str = "Starting soon"
            elif diff_hours < 24:
                time_until_str = f"In {diff_hours} hours"
            else:
                diff_days = round(diff_hours / 24)
                time_until_str = f"In {diff_days} days"
        except Exception:
            time_until_str = "Upcoming"

        loc_addr_next = None
        c_id = ns.get("client_id")
        if c_id:
            c_query = {"_id": ObjectId(c_id)} if ObjectId.is_valid(c_id) else {"_id": c_id}
            c_doc = await db["client_list"].find_one(c_query)
            if c_doc and "locations" in c_doc:
                l_id = ns.get("location_id")
                target_loc = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(l_id)), None)
                if target_loc:
                    loc_addr_next = target_loc.get("address")

        next_card = NextShiftHomeCard(
            shift_id=str(ns.get("_id") or ns.get("id")),
            client_name=ns.get("client_name", "Client"),
            location_name=ns.get("location_name", "Location"),
            location_address=loc_addr_next,
            start_time=ns_start_str,
            end_time=ns.get("end_time", "17:00"),
            time_until_start=time_until_str,
            date=ns_date_str
        )

    # 4. Recent Activity Feed
    activity_items = []

    raw_reviews = await db["photo_reviews"].find({"cleaner.worker_id": worker_id}).sort("date_submitted", -1).to_list(length=10)
    for r in raw_reviews:
        sub_dt = r.get("date_submitted", now)
        room_name = r.get("room", {}).get("name", "Room")
        activity_items.append(ActivityFeedItem(
            id=str(r.get("_id") or r.get("review_id")),
            title=f"Photo uploaded for {room_name}",
            subtitle="Saved to inspection report.",
            timestamp=_human_time_ago(sub_dt, now),
            created_at=sub_dt if isinstance(sub_dt, datetime) else now
        ))

    worker_shifts = await db["shifts"].find({"workers.worker_id": worker_id}).sort("updated_at", -1).to_list(length=10)
    for s in worker_shifts:
        workers_list = s.get("workers", [])
        w_rec = next((w for w in workers_list if str(w.get("worker_id")) == worker_id), None)
        if w_rec and w_rec.get("checkin_time"):
            c_dt = w_rec.get("checkin_time")
            loc_name = s.get("location_name", s.get("client_name", "Shift"))
            s_time = s.get("start_time", "08:00")
            e_time = s.get("end_time", "17:00")
            activity_items.append(ActivityFeedItem(
                id=f"start_{s.get('_id')}",
                title="Shift started",
                subtitle=f"{loc_name} - {s_time} - {e_time}",
                timestamp=_human_time_ago(c_dt, now),
                created_at=c_dt if isinstance(c_dt, datetime) else now
            ))

    activity_items.sort(key=lambda x: x.created_at, reverse=True)
    recent_activity = activity_items[:5]

    return WorkerHomeResponse(
        greeting=greeting_str,
        worker_name=worker_name,
        profile_photo=profile_photo,
        active_shift=active_card,
        stats=stats,
        next_shift=next_card,
        recent_activity=recent_activity
    )


@worker_shift_router.get(
    "/roster",
    response_model=WorkerRosterResponse,
    summary="Get Worker Roster",
    description="Returns worker's shift roster grouped by date with shift status (upcoming, running, completed), client details, location details, room count, and assigned admin info."
)
async def get_worker_roster(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    query = {"workers.worker_id": worker_id}
    if start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
    elif start_date:
        query["date"] = {"$gte": start_date}

    cursor = db["shifts"].find(query).sort("date", 1)
    raw_shifts = await cursor.to_list(length=500)

    grouped = {}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    admin_doc = await db["users"].find_one({"role": "admin"})
    admin_name = admin_doc.get("full_name", "Admin") if admin_doc else "Admin"
    admin_pic = admin_doc.get("profile_photo") if admin_doc else None

    for s in raw_shifts:
        s_date = s.get("date", today_str)
        s_status = s.get("status", "published")

        if s_status == "completed":
            roster_status = "completed"
        elif s_status == "running" or any(w.get("checkin_time") for w in s.get("workers", [])):
            roster_status = "running"
        else:
            roster_status = "upcoming"

        client_phone = None
        c_id = s.get("client_id")
        if c_id:
            c_query = {"_id": ObjectId(c_id)} if ObjectId.is_valid(c_id) else {"_id": c_id}
            c_doc = await db["client_list"].find_one(c_query)
            if c_doc:
                client_phone = c_doc.get("phone")

        rooms_list = s.get("rooms", [])
        room_ids = [str(r.get("room_id")) for r in rooms_list if r.get("room_id")]

        roster_item = RosterShiftDetail(
            id=str(s.get("_id") or s.get("id")),
            client_id=str(s.get("client_id")),
            name=s.get("client_name", "Client"),
            profile_picture=None,
            phone_number=client_phone,
            time_start=s.get("start_time", "08:00"),
            time_end=s.get("end_time", "17:00"),
            status=roster_status,
            location_id=str(s.get("location_id")),
            location_name=s.get("location_name", "Location"),
            location_picture=None,
            total_rooms_count=len(rooms_list),
            room_ids=room_ids,
            assigned_admin_name=admin_name,
            assigned_admin_profile=admin_pic
        )

        if s_date not in grouped:
            grouped[s_date] = []
        grouped[s_date].append(roster_item)

    roster_groups = [WorkerRosterGroup(date=d, shifts=shifts) for d, shifts in grouped.items()]
    total_shifts_count = sum(len(g.shifts) for g in roster_groups)

    return WorkerRosterResponse(
        total_shifts=total_shifts_count,
        roster=roster_groups
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
    worker_id = str(current_user.id or current_user.mongo_id)

    query = {"workers.worker_id": worker_id}
    if status_val:
        query["status"] = status_val

    cursor = db["shifts"].find(query).sort("created_at", -1)
    raw_shifts = await cursor.to_list(length=200)

    shifts_res = []
    for s in raw_shifts:
        s["id"] = str(s.get("_id") or s.get("id"))
        progress = _calculate_shift_progress(s)
        s["overall_progress_percentage"] = progress["overall_progress_percentage"]
        s["completed_rooms_count"] = progress["completed_rooms_count"]
        s["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
        s["pending_rooms_count"] = progress["pending_rooms_count"]
        shifts_res.append(ShiftResponse(**s))

    return shifts_res


@worker_shift_router.get(
    "/{shift_id}",
    response_model=ShiftResponse,
    summary="Get Worker Shift Detail",
    description="Returns full detailed shift information (including client, location, rooms, tasks, photo requirements) for a specific assigned shift."
)
async def get_worker_shift_detail(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    is_assigned = any(str(w.get("worker_id")) == worker_id for w in workers_list)
    if not is_assigned:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    shift_doc["id"] = str(shift_doc.get("_id") or shift_doc.get("id"))
    progress = _calculate_shift_progress(shift_doc)
    shift_doc["overall_progress_percentage"] = progress["overall_progress_percentage"]
    shift_doc["completed_rooms_count"] = progress["completed_rooms_count"]
    shift_doc["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
    shift_doc["pending_rooms_count"] = progress["pending_rooms_count"]

    return ShiftResponse(**shift_doc)


@worker_shift_router.post(
    "/{shift_id}/start",
    response_model=ShiftExecutionStateResponse,
    summary="Start Cleaning Shift & Check-In",
    description="Starts the cleaning shift, records check-in, initializes room statuses, and returns live room task execution state."
)
async def start_worker_shift(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    target_idx = next((idx for idx, w in enumerate(workers_list) if str(w.get("worker_id")) == worker_id), None)
    if target_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    workers_list[target_idx]["checkin_time"] = now
    workers_list[target_idx]["status"] = "ontime"

    rooms = shift_doc.get("rooms", [])
    for r in rooms:
        if "status" not in r:
            r["status"] = "pending"

    progress = _calculate_shift_progress(shift_doc)

    await db["shifts"].update_one(
        {"$or": [{"_id": shift_id}, {"id": shift_id}]},
        {"$set": {
            "status": "running",
            "workers": workers_list,
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "updated_at": now
        }}
    )

    updated_shift = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    return ShiftExecutionStateResponse(
        shift_id=str(updated_shift.get("_id") or updated_shift.get("id")),
        client_id=updated_shift.get("client_id", ""),
        client_name=updated_shift.get("client_name", ""),
        location_id=updated_shift.get("location_id", ""),
        location_name=updated_shift.get("location_name", ""),
        status="running",
        overall_progress_percentage=progress["overall_progress_percentage"],
        total_rooms_count=progress["total_rooms_count"],
        completed_rooms_count=progress["completed_rooms_count"],
        in_progress_rooms_count=progress["in_progress_rooms_count"],
        pending_rooms_count=progress["pending_rooms_count"],
        rooms=[ShiftRoomDetail(**r) for r in updated_shift.get("rooms", [])]
    )


@worker_shift_router.post(
    "/{shift_id}/rooms/{room_id}/tasks/{task_id}/toggle",
    response_model=ShiftExecutionStateResponse,
    summary="Toggle Room Task Completion State",
    description="Toggles task completion state for a room. Updates room status to 'in_progress' and recalculates live progress."
)
async def toggle_room_task_completion(
    shift_id: str,
    room_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    is_assigned = any(str(w.get("worker_id")) == worker_id for w in shift_doc.get("workers", []))
    if not is_assigned:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    tasks = target_room.get("tasks", [])
    target_task = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if not target_task:
        raise HTTPException(status_code=404, detail=f"Task ID '{task_id}' not found in room '{room_id}'")

    now = datetime.now(timezone.utc)
    new_state = not target_task.get("is_completed", False)
    target_task["is_completed"] = new_state
    target_task["completed_at"] = now if new_state else None

    completed_tasks = sum(1 for t in tasks if t.get("is_completed"))
    target_room["completed_tasks_count"] = completed_tasks
    if target_room.get("status") in ["pending", None] and completed_tasks > 0:
        target_room["status"] = "in_progress"

    progress = _calculate_shift_progress(shift_doc)

    await db["shifts"].update_one(
        {"$or": [{"_id": shift_id}, {"id": shift_id}]},
        {"$set": {
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "updated_at": now
        }}
    )

    updated_shift = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    return ShiftExecutionStateResponse(
        shift_id=str(updated_shift.get("_id") or updated_shift.get("id")),
        client_id=updated_shift.get("client_id", ""),
        client_name=updated_shift.get("client_name", ""),
        location_id=updated_shift.get("location_id", ""),
        location_name=updated_shift.get("location_name", ""),
        status=updated_shift.get("status", "running"),
        overall_progress_percentage=progress["overall_progress_percentage"],
        total_rooms_count=progress["total_rooms_count"],
        completed_rooms_count=progress["completed_rooms_count"],
        in_progress_rooms_count=progress["in_progress_rooms_count"],
        pending_rooms_count=progress["pending_rooms_count"],
        rooms=[ShiftRoomDetail(**r) for r in updated_shift.get("rooms", [])]
    )


@worker_shift_router.post(
    "/{shift_id}/rooms/{room_id}/photos",
    response_model=PhotoUploadResponse,
    summary="Upload Room Photo for Admin Review",
    description="Worker uploads required photo for a room. Inserts photo review into Admin photo review queue and updates room status."
)
async def upload_room_photo(
    shift_id: str,
    room_id: str,
    file: UploadFile = File(...),
    photo_name: Optional[str] = Form(None),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    is_assigned = any(str(w.get("worker_id")) == worker_id for w in shift_doc.get("workers", []))
    if not is_assigned:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    s3_service = S3Service()
    file_bytes = await file.read()
    photo_url = await s3_service.upload_file(file_bytes, file.filename, file.content_type)

    now = datetime.now(timezone.utc)
    review_id = f"RV-{uuid.uuid4().hex[:6].upper()}"
    photo_title = photo_name or file.filename or f"Room Photo ({target_room.get('room_name')})"

    review_doc = {
        "_id": review_id,
        "review_id": review_id,
        "shift_id": shift_id,
        "cleaner": {
            "worker_id": worker_id,
            "name": current_user.full_name,
            "profile_picture": getattr(current_user, "profile_photo", None)
        },
        "client": {
            "client_id": shift_doc.get("client_id", ""),
            "name": shift_doc.get("client_name", "")
        },
        "location": {
            "location_id": shift_doc.get("location_id", ""),
            "name": shift_doc.get("location_name", "")
        },
        "room": {
            "room_id": room_id,
            "name": target_room.get("room_name", "")
        },
        "photo_url": photo_url,
        "photo_name": photo_title,
        "ai_score": 90.0,
        "ai_confidence": "high",
        "status": "pending_review",
        "rejection_reason": None,
        "date_submitted": now
    }

    await db["photo_reviews"].insert_one(review_doc)

    photo_entry = {
        "photo_id": str(uuid.uuid4()),
        "photo_name": photo_title,
        "photo_url": photo_url,
        "submitted_at": now,
        "review_id": review_id,
        "status": "pending_review"
    }

    if "submitted_photos" not in target_room or not isinstance(target_room["submitted_photos"], list):
        target_room["submitted_photos"] = []
    target_room["submitted_photos"].append(photo_entry)
    target_room["status"] = "photo_submitted"

    progress = _calculate_shift_progress(shift_doc)

    await db["shifts"].update_one(
        {"$or": [{"_id": shift_id}, {"id": shift_id}]},
        {"$set": {
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "updated_at": now
        }}
    )

    return PhotoUploadResponse(
        review_id=review_id,
        shift_id=shift_id,
        room_id=room_id,
        photo_url=photo_url,
        status="pending_review",
        message="Photo submitted successfully for Admin review"
    )


@worker_shift_router.post(
    "/{shift_id}/rooms/{room_id}/request-clean",
    summary="Request Room Cleaning Completion",
    description="Validates that all tasks for the room are finished before requesting completion. Returns error if any task is incomplete."
)
async def request_room_clean_completion(
    shift_id: str,
    room_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    tasks = target_room.get("tasks", [])
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.get("is_completed"))

    if completed_tasks < total_tasks:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete room clean. All tasks ({completed_tasks}/{total_tasks} completed) must be finished before requesting room completion."
        )

    return {
        "shift_id": shift_id,
        "room_id": room_id,
        "room_name": target_room.get("room_name"),
        "status": target_room.get("status", "in_progress"),
        "completed_tasks": completed_tasks,
        "total_tasks": total_tasks,
        "message": "All tasks completed. Room photos submitted for Admin approval."
    }


@worker_shift_router.post(
    "/{shift_id}/complete",
    response_model=ShiftResponse,
    summary="Mark Entire Shift Completed",
    description="Worker marks shift complete after all assigned rooms are cleaned and approved by Admin."
)
async def mark_shift_completed(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    target_idx = next((idx for idx, w in enumerate(workers_list) if str(w.get("worker_id")) == worker_id), None)
    if target_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    rooms = shift_doc.get("rooms", [])
    incomplete_rooms = [r.get("room_name", r.get("room_id")) for r in rooms if r.get("status") != "completed"]
    if incomplete_rooms:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete shift. The following rooms are not yet approved/completed: {', '.join(incomplete_rooms)}"
        )

    now = datetime.now(timezone.utc)
    workers_list[target_idx]["checkout_time"] = now

    await db["shifts"].update_one(
        {"$or": [{"_id": shift_id}, {"id": shift_id}]},
        {"$set": {
            "status": "completed",
            "workers": workers_list,
            "overall_progress_percentage": 100.0,
            "updated_at": now
        }}
    )

    updated_shift = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    updated_shift["id"] = str(updated_shift.get("_id") or updated_shift.get("id"))
    progress = _calculate_shift_progress(updated_shift)
    updated_shift["overall_progress_percentage"] = progress["overall_progress_percentage"]
    updated_shift["completed_rooms_count"] = progress["completed_rooms_count"]
    updated_shift["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
    updated_shift["pending_rooms_count"] = progress["pending_rooms_count"]

    return ShiftResponse(**updated_shift)


@worker_shift_router.get(
    "/{shift_id}/state",
    response_model=WorkerShiftStateResponse,
    summary="Get Worker Shift Attendance State",
    description="Returns the current attendance state ('not_checked_in', 'checked_in', 'completed') of the worker for a specific shift."
)
async def get_worker_shift_state(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    w_record = None
    w_idx = None
    for idx, w in enumerate(workers_list):
        if str(w.get("worker_id")) == worker_id:
            w_record = w
            w_idx = idx
            break

    if w_record is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = _eval_auto_checkout(shift_doc, w_record, now)

    if w_record.get("is_auto_checked_out"):
        workers_list[w_idx] = w_record
        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if not c_time:
        state_label = "not_checked_in"
    elif c_time and not co_time:
        state_label = "checked_in"
    else:
        state_label = "completed"

    return WorkerShiftStateResponse(
        shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
        worker_id=worker_id,
        state=state_label,
        checkin_time=c_time,
        checkout_time=co_time,
        status=w_record.get("status"),
        hours_worked=w_record.get("hours_worked"),
        is_auto_checked_out=w_record.get("is_auto_checked_out", False)
    )


@worker_shift_router.post(
    "/{shift_id}/attendance",
    response_model=WorkerAttendanceToggleResponse,
    summary="Toggle Worker Shift Attendance (Combined Check-in & Check-out)",
    description="Single combined endpoint for worker attendance. Automatically executes Check-in if not checked in, or Check-out if currently checked in."
)
async def toggle_worker_shift_attendance(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    target_worker_idx = None
    for idx, w in enumerate(workers_list):
        if str(w.get("worker_id")) == worker_id:
            target_worker_idx = idx
            break

    if target_worker_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = workers_list[target_worker_idx]

    w_record = _eval_auto_checkout(shift_doc, w_record, now)
    workers_list[target_worker_idx] = w_record

    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if not c_time:
        shift_date_str = shift_doc.get("date")
        start_time_str = shift_doc.get("start_time")

        attendance_status = "ontime"
        try:
            start_hour, start_min = map(int, start_time_str.split(":"))
            shift_year, shift_month, shift_day = map(int, shift_date_str.split("-"))
            shift_start_dt = datetime(shift_year, shift_month, shift_day, start_hour, start_min, tzinfo=timezone.utc)

            if now > shift_start_dt:
                attendance_status = "late"
            else:
                attendance_status = "ontime"
        except Exception:
            attendance_status = "ontime"

        workers_list[target_worker_idx]["checkin_time"] = now
        workers_list[target_worker_idx]["status"] = attendance_status

        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="check_in",
            state="checked_in",
            checkin_time=now,
            checkout_time=None,
            status=attendance_status,
            hours_worked=None,
            is_auto_checked_out=False,
            message="Check-in successful"
        )

    elif c_time and not co_time:
        checkin_dt = c_time
        if isinstance(checkin_dt, str):
            checkin_dt = datetime.fromisoformat(checkin_dt)
        if checkin_dt and checkin_dt.tzinfo is None:
            checkin_dt = checkin_dt.replace(tzinfo=timezone.utc)

        duration_seconds = (now - checkin_dt).total_seconds() if checkin_dt else 0.0
        hours_worked = round(max(0.0, duration_seconds / 3600.0), 2)

        workers_list[target_worker_idx]["checkout_time"] = now
        workers_list[target_worker_idx]["hours_worked"] = hours_worked

        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="check_out",
            state="completed",
            checkin_time=checkin_dt,
            checkout_time=now,
            status=w_record.get("status"),
            hours_worked=hours_worked,
            is_auto_checked_out=False,
            message="Check-out successful"
        )

    else:
        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="already_completed",
            state="completed",
            checkin_time=c_time,
            checkout_time=co_time,
            status=w_record.get("status"),
            hours_worked=w_record.get("hours_worked"),
            is_auto_checked_out=w_record.get("is_auto_checked_out", False),
            message="Worker has already completed attendance for this shift"
        )


@worker_shift_router.post(
    "/{shift_id}/check-in",
    response_model=WorkerCheckInResponse,
    summary="Worker Shift Check-in (Backward Compatible)",
    description="Allows a worker to check in to an assigned shift."
)
async def worker_shift_check_in(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    res = await toggle_worker_shift_attendance(shift_id, current_user)
    return WorkerCheckInResponse(
        shift_id=res.shift_id,
        worker_id=res.worker_id,
        checkin_time=res.checkin_time or datetime.now(timezone.utc),
        status=res.status or "ontime"
    )


@worker_shift_router.post(
    "/{shift_id}/check-out",
    response_model=WorkerCheckOutResponse,
    summary="Worker Shift Check-out (Backward Compatible)",
    description="Allows a worker to check out of an assigned shift."
)
async def worker_shift_check_out(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    res = await toggle_worker_shift_attendance(shift_id, current_user)
    return WorkerCheckOutResponse(
        shift_id=res.shift_id,
        worker_id=res.worker_id,
        checkout_time=res.checkout_time or datetime.now(timezone.utc),
        hours_worked=res.hours_worked or 0.0
    )

