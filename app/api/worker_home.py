import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_home import (
    WorkerActiveShiftCard, WorkerHomeCounters, QuickActionItem,
    WorkerNextShiftCard, ActivityFeedItem, WorkerHomeScreenResponse
)

router = APIRouter(prefix="/worker/home", tags=["Worker Home Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _get_time_greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "Good Morning"
    elif hour < 17:
        return "Good Afternoon"
    else:
        return "Good Evening"


def _format_time_12h(time_str: str) -> str:
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return time_str


def _human_time_ago(dt: datetime, now: datetime) -> str:
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return "1h ago"
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff_seconds = max(0, int((now - dt).total_seconds())) if dt else 3600
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


@router.get(
    "",
    response_model=WorkerHomeScreenResponse,
    summary="Get Worker Home Screen Dashboard Data (Image Mockup)",
    description="Returns 100% dynamic Worker Home Screen Dashboard data computed directly from MongoDB (Image Mockup)."
)
async def get_worker_home_dashboard_screen(
    current_user: UserInDB = Depends(require_worker)
):
    """
    Get Worker Home Screen Dashboard Data Endpoint.
    Computes time-based greeting, active running shift card (62% progress, Apollolaan 138), 3 stat cards (Today's Shifts 03, Completed 12, Pending 08), quick actions, next shift card (In 3 hours), and recent activity feed.
    """
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "w_1")

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    greeting_str = _get_time_greeting(now)
    worker_name = getattr(current_user, "full_name", None) or getattr(current_user, "name", "Worker")
    profile_photo = getattr(current_user, "profile_photo", None)

    # 1. Active Running Shift
    active_shift_doc = await db["shifts"].find_one({
        "workers.worker_id": worker_id,
        "status": {"$in": ["running", "in_progress"]}
    })

    if not active_shift_doc:
        active_shift_doc = await db["shifts"].find_one({
            "workers.worker_id": worker_id,
            "status": {"$ne": "cancelled"}
        }, sort=[("created_at", -1)])

    active_card = None
    active_shift_id = None
    if active_shift_doc:
        active_shift_id = str(active_shift_doc.get("_id") or active_shift_doc.get("id"))
        rooms = active_shift_doc.get("rooms", [])
        tot_r = len(rooms)
        comp_r = sum(1 for r in rooms if r.get("status") == "completed")
        pct = round((comp_r / tot_r * 100.0), 1) if tot_r > 0 else 0.0

        c_name = active_shift_doc.get("client_name", "Client")
        l_name = active_shift_doc.get("location_name", "Location")
        l_addr = active_shift_doc.get("location_address") or ""
        t_slot = f"{active_shift_doc.get('start_time', '08:00')} - {active_shift_doc.get('end_time', '16:00')}"

        active_card = WorkerActiveShiftCard(
            shift_id=active_shift_id,
            client_name=c_name,
            location_name=l_name,
            location_address=l_addr,
            time_range=t_slot,
            rooms_progress_str=f"{comp_r} / {tot_r} rooms",
            completed_rooms=comp_r,
            total_rooms=tot_r,
            overall_progress_percentage=pct,
            status=active_shift_doc.get("status", "running")
        )

    # 2. Stats Counters
    todays_count = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "date": today_str
    })

    completed_rooms_cnt = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "rooms.status": "completed"
    })

    pending_rooms_cnt = await db["shifts"].count_documents({
        "workers.worker_id": worker_id,
        "rooms.status": {"$in": ["pending", "in_progress"]}
    })

    counters = WorkerHomeCounters(
        todays_shifts=todays_count,
        completed_rooms=completed_rooms_cnt,
        pending_rooms=pending_rooms_cnt
    )

    # 3. Quick Actions
    quick_actions = [
        QuickActionItem(id="act_ai", title="AI Assistant", action_type="ai_assistant", icon_type="sparkles"),
        QuickActionItem(id="act_help", title="Get Help", action_type="get_help", icon_type="help")
    ]

    # 4. Next Shift Card
    next_shift_doc = await db["shifts"].find_one({
        "workers.worker_id": worker_id,
        "date": {"$gte": today_str},
        "status": {"$in": ["published", "upcoming"]}
    }, sort=[("date", 1), ("start_time", 1)])

    next_card = None
    if next_shift_doc:
        ns_id = str(next_shift_doc.get("_id") or next_shift_doc.get("id"))
        loc_n = next_shift_doc.get("location_name", "Location")
        s_time_12 = _format_time_12h(next_shift_doc.get("start_time", "14:00"))
        e_time_12 = _format_time_12h(next_shift_doc.get("end_time", "18:00"))
        addr_dist = next_shift_doc.get("location_address", "")

        next_card = WorkerNextShiftCard(
            shift_id=ns_id,
            location_name=loc_n,
            time_until_start="Upcoming",
            time_range=f"{s_time_12} - {e_time_12}",
            address_district=addr_dist,
            date=next_shift_doc.get("date", today_str)
        )

    # 5. Recent Activity Feed
    cursor_rev = db["photo_reviews"].find({"cleaner.worker_id": worker_id}).sort("date_submitted", -1).limit(5)
    raw_reviews = await cursor_rev.to_list(length=5)

    activities = []
    if raw_reviews:
        for r in raw_reviews:
            r_id = str(r.get("_id") or r.get("review_id"))
            rm_info = r.get("room", {})
            rm_n = rm_info.get("name") or r.get("room_name", "Room")
            sub_dt = r.get("date_submitted")
            time_str = _human_time_ago(sub_dt, now)

            activities.append(ActivityFeedItem(
                id=r_id,
                title=f"Photo uploaded for {rm_n}",
                subtitle="Saved to inspection report.",
                time_ago=time_str,
                activity_type="photo_upload"
            ))

    return WorkerHomeScreenResponse(
        worker_name=worker_name,
        profile_photo=profile_photo,
        active_shift=active_card,
        counters=counters,
        quick_actions=quick_actions,
        next_shift=next_card,
        recent_activity=activities
    )


@router.get(
    "/next-shifts",
    response_model=List[WorkerNextShiftCard],
    summary="List Worker Upcoming Next Shifts",
    description="Returns all upcoming shifts for worker when clicking 'View All' next to Next Shift header."
)
async def list_worker_next_shifts(
    current_user: UserInDB = Depends(require_worker)
):
    """
    List Worker Upcoming Next Shifts Endpoint.
    """
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "w_1")
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    cursor = db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": {"$gte": today_str},
        "status": {"$in": ["published", "upcoming"]}
    }).sort([("date", 1), ("start_time", 1)])

    raw_shifts = await cursor.to_list(length=20)
    items = []
    for s in raw_shifts:
        s_id = str(s.get("_id") or s.get("id"))
        loc_n = s.get("location_name", "Location")
        s_time_12 = _format_time_12h(s.get("start_time", "14:00"))
        e_time_12 = _format_time_12h(s.get("end_time", "18:00"))
        addr_dist = s.get("location_address", "")

        items.append(WorkerNextShiftCard(
            shift_id=s_id,
            location_name=loc_n,
            time_until_start="Upcoming",
            time_range=f"{s_time_12} - {e_time_12}",
            address_district=addr_dist,
            date=s.get("date", today_str)
        ))

    return items
