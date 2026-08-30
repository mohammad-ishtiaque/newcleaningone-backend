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
from app.api.worker_shift_utils import (
    is_plan_active_on_date, get_or_create_shift_execution, resolve_shift_execution
)
from app.core.timezone_utils import parse_plan_start_datetime, human_time_until

router = APIRouter(prefix="/worker/home", tags=["Worker Home Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _get_time_greeting(dt: datetime) -> str:
    hour = dt.hour
    if hour < 12:
        return "Good Morning"
    elif hour < 17:
        return "Good Afternoon"
    else:
        return "Good Evening"


def _format_time_12h(time_str: str) -> str:
    try:
        dt = datetime.strptime(str(time_str).strip(), "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(time_str)


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
    Computes time-based greeting, active running shift card, 3 stat counters, quick actions, next shift card, and recent activity feed.
    """
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "w_1")

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    greeting_str = _get_time_greeting(now_utc)
    worker_name = getattr(current_user, "full_name", None) or getattr(current_user, "name", "Worker")
    profile_photo = getattr(current_user, "profile_photo", None)

    # 1. Fetch cleaning plans and direct shifts for worker
    cursor_plans = db["cleaning_plans"].find({
        "$or": [
            {"worker_ids": worker_id},
            {"assigned_workers.worker_id": worker_id},
            {"workers.worker_id": worker_id}
        ],
        "status": {"$ne": "cancelled"}
    })
    plans = await cursor_plans.to_list(length=100)

    # Also query active shift executions and direct shifts
    exec_cursor = db["shift_executions"].find({
        "$or": [
            {"assigned_workers.worker_id": worker_id},
            {"workers.worker_id": worker_id},
            {"worker_ids": worker_id}
        ],
        "date": today_str,
        "status": {"$ne": "cancelled"}
    })
    today_execs = await exec_cursor.to_list(length=100)

    direct_shifts = await db["shifts"].find({
        "$or": [
            {"workers.worker_id": worker_id},
            {"assigned_workers.worker_id": worker_id}
        ],
        "date": today_str,
        "status": {"$ne": "cancelled"}
    }).to_list(length=100)

    all_today_shifts = []
    seen_shift_ids = set()

    for p in plans:
        if is_plan_active_on_date(p, today_str):
            exec_doc = await get_or_create_shift_execution(p, today_str, db)
            sid = str(exec_doc.get("id") or exec_doc.get("_id"))
            if sid not in seen_shift_ids:
                seen_shift_ids.add(sid)
                all_today_shifts.append(exec_doc)

    for ex in today_execs:
        sid = str(ex.get("id") or ex.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            all_today_shifts.append(ex)

    for ds in direct_shifts:
        sid = str(ds.get("id") or ds.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            all_today_shifts.append(ds)

    def _get_shift_start_dt(shift_dict):
        try:
            st = shift_dict.get("start_time", "08:00 AM")
            tz = shift_dict.get("timezone") or "Europe/Amsterdam"
            return parse_plan_start_datetime(today_str, st, tz=tz)
        except Exception:
            return datetime.now(timezone.utc)

    all_today_shifts.sort(key=_get_shift_start_dt)

    active_card = None
    next_card = None
    completed_rooms_cnt = 0
    pending_rooms_cnt = 0

    for s in all_today_shifts:
        sid = str(s.get("id") or s.get("_id"))
        rooms = s.get("rooms", [])
        tot_r = len(rooms)
        comp_r = sum(1 for r in rooms if r.get("status") in ["completed", "approved"] or r.get("is_completed"))
        pend_r = sum(1 for r in rooms if r.get("status") not in ["completed", "approved"] and not r.get("is_completed"))
        completed_rooms_cnt += comp_r
        pending_rooms_cnt += pend_r

        pct = round((comp_r / tot_r * 100.0), 1) if tot_r > 0 else 0.0
        c_name = s.get("client_name") or s.get("company_name") or "Client"
        l_name = s.get("location_name") or "Location"
        l_addr = s.get("location_address") or s.get("address") or ""
        st_12 = _format_time_12h(s.get("start_time", "08:00"))
        et_12 = _format_time_12h(s.get("end_time", "16:00"))
        t_slot = f"{st_12} - {et_12}"

        # Check if worker checked in or shift in progress
        w_list = s.get("assigned_workers") or s.get("workers") or []
        w_rec = next((w for w in w_list if str(w.get("worker_id")) == worker_id), None)
        is_active = (s.get("status") in ["running", "in_progress"] or (w_rec and w_rec.get("checkin_time") and not w_rec.get("checkout_time")))

        if is_active and not active_card:
            active_card = WorkerActiveShiftCard(
                shift_id=sid,
                client_name=c_name,
                location_name=l_name,
                location_address=l_addr,
                time_range=t_slot,
                rooms_progress_str=f"{comp_r} / {tot_r} rooms",
                completed_rooms=comp_r,
                total_rooms=tot_r,
                overall_progress_percentage=pct,
                status=s.get("status", "running")
            )
        elif not is_active and not next_card and s.get("status") not in ["completed", "cancelled"]:
            st_str = s.get("start_time", "08:00 AM")
            tz = s.get("timezone") or "Europe/Amsterdam"
            st_dt = parse_plan_start_datetime(today_str, st_str, tz=tz)
            time_until = human_time_until(st_dt, now_utc)
            next_card = WorkerNextShiftCard(
                shift_id=sid,
                location_name=l_name,
                time_until_start=time_until,
                time_range=t_slot,
                address_district=l_addr,
                date=s.get("date", today_str)
            )

    if not active_card and all_today_shifts:
        s = all_today_shifts[0]
        sid = str(s.get("id") or s.get("_id"))
        rooms = s.get("rooms", [])
        tot_r = len(rooms)
        comp_r = sum(1 for r in rooms if r.get("status") in ["completed", "approved"] or r.get("is_completed"))
        pct = round((comp_r / tot_r * 100.0), 1) if tot_r > 0 else 0.0
        active_card = WorkerActiveShiftCard(
            shift_id=sid,
            client_name=s.get("client_name") or "Client",
            location_name=s.get("location_name") or "Location",
            location_address=s.get("location_address") or "",
            time_range=f"{_format_time_12h(s.get('start_time', '08:00'))} - {_format_time_12h(s.get('end_time', '16:00'))}",
            rooms_progress_str=f"{comp_r} / {tot_r} rooms",
            completed_rooms=comp_r,
            total_rooms=tot_r,
            overall_progress_percentage=pct,
            status=s.get("status", "published")
        )
        if len(all_today_shifts) > 1 and not next_card:
            ns = all_today_shifts[1]
            ns_id = str(ns.get("id") or ns.get("_id"))
            nst_str = ns.get("start_time", "08:00 AM")
            ntz = ns.get("timezone") or "Europe/Amsterdam"
            nst_dt = parse_plan_start_datetime(today_str, nst_str, tz=ntz)
            next_card = WorkerNextShiftCard(
                shift_id=ns_id,
                location_name=ns.get("location_name") or "Location",
                time_until_start=human_time_until(nst_dt, now_utc),
                time_range=f"{_format_time_12h(nst_str)} - {_format_time_12h(ns.get('end_time', '16:00'))}",
                address_district=ns.get("location_address") or ns.get("address") or "",
                date=today_str
            )

    counters = WorkerHomeCounters(
        todays_shifts=len(all_today_shifts),
        completed_rooms=completed_rooms_cnt,
        pending_rooms=pending_rooms_cnt
    )

    # 3. Quick Actions
    quick_actions = [
        QuickActionItem(id="act_ai", title="AI Assistant", action_type="ai_assistant", icon_type="sparkles"),
        QuickActionItem(id="act_help", title="Get Help", action_type="get_help", icon_type="help")
    ]

    # 4. Next Shift Card (Look ahead across today and next 14 days)
    if not next_card:
        for day_offset in range(1, 15):
            future_dt = now_utc + timedelta(days=day_offset)
            future_date_str = future_dt.strftime("%Y-%m-%d")

            # 1. Check recurring cleaning plans
            for p in plans:
                if is_plan_active_on_date(p, future_date_str):
                    plan_tz = p.get("timezone") or "Europe/Amsterdam"
                    st_str = p.get("start_time", "08:00 AM")
                    et_str = p.get("end_time", "04:00 PM")
                    start_dt = parse_plan_start_datetime(future_date_str, st_str, tz=plan_tz)
                    time_until = human_time_until(start_dt, now_utc)

                    next_card = WorkerNextShiftCard(
                        shift_id=str(p.get("id") or p.get("_id")),
                        location_name=p.get("location_name") or p.get("client_name") or "Location",
                        time_until_start=time_until,
                        time_range=f"{_format_time_12h(st_str)} - {_format_time_12h(et_str)}",
                        address_district=p.get("location_address") or p.get("address") or "",
                        date=future_date_str
                    )
                    break

            if next_card:
                break

            # 2. Check shift executions or direct shifts on future_date_str
            future_shift = await db["shift_executions"].find_one({
                "$or": [
                    {"assigned_workers.worker_id": worker_id},
                    {"workers.worker_id": worker_id},
                    {"worker_ids": worker_id}
                ],
                "date": future_date_str,
                "status": {"$ne": "cancelled"}
            }, sort=[("start_time", 1)])

            if not future_shift:
                future_shift = await db["shifts"].find_one({
                    "$or": [
                        {"workers.worker_id": worker_id},
                        {"assigned_workers.worker_id": worker_id}
                    ],
                    "date": future_date_str,
                    "status": {"$in": ["published", "upcoming", "scheduled"]}
                }, sort=[("start_time", 1)])

            if future_shift:
                fs_id = str(future_shift.get("_id") or future_shift.get("id"))
                st_str = future_shift.get("start_time", "08:00 AM")
                et_str = future_shift.get("end_time", "04:00 PM")
                start_dt = parse_plan_start_datetime(future_date_str, st_str, tz=future_shift.get("timezone", "Europe/Amsterdam"))
                time_until = human_time_until(start_dt, now_utc)

                next_card = WorkerNextShiftCard(
                    shift_id=fs_id,
                    location_name=future_shift.get("location_name") or "Location",
                    time_until_start=time_until,
                    time_range=f"{_format_time_12h(st_str)} - {_format_time_12h(et_str)}",
                    address_district=future_shift.get("location_address") or future_shift.get("address") or "",
                    date=future_date_str
                )
                break

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
            time_str = _human_time_ago(sub_dt, now_utc)

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
