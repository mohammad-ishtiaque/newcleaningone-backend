import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_roster import (
    DateStripItem, RosterCardItem, WorkerRosterScreenResponse, WorkerRosterDetailResponse
)
from app.api.worker_shift_utils import resolve_shift_execution, is_plan_active_on_date, get_or_create_shift_execution

router = APIRouter(prefix="/worker/roster", tags=["Worker Roaster Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


async def _get_admin_contact(db) -> tuple[str, str]:
    """Helper to fetch primary Admin name and phone from MongoDB users collection."""
    admin_user = await db["users"].find_one({"role": {"$in": ["admin", "manager"]}})
    if admin_user:
        name = admin_user.get("full_name") or admin_user.get("name") or "Support Admin"
        phone = admin_user.get("phone") or admin_user.get("phone_number") or "+1555000000"
        return name, phone
    company_doc = await db["company_profile"].find_one({"type": "main"})
    if company_doc:
        return company_doc.get("company_name", "CleanOnes Support"), company_doc.get("phone", "+1555000000")
    return "CleanOnes Support", "+1555000000"


def _format_time_12h(time_str: str) -> str:
    """Formats '14:00' to '2:00 PM'."""
    try:
        dt = datetime.strptime(str(time_str).strip(), "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(time_str)


@router.get(
    "",
    response_model=WorkerRosterScreenResponse,
    summary="Get Worker My Roster Screen Data (Image Mockup)",
    description="Returns worker's roster dashboard screen data including 7-day date strip, week/month header, shift cards with room counts, status pills, and dynamic Admin contact info."
)
async def get_worker_roster_screen(
    week: Optional[int] = None,
    year: Optional[int] = None,
    date_selected: Optional[str] = None,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Get Worker My Roster Screen Data Endpoint.
    Serves the worker mobile app 'My Roster' screen matching the attached mockup.
    """
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "w_1")

    now = datetime.now(timezone.utc)
    target_date = now

    if date_selected:
        try:
            target_date = datetime.strptime(date_selected, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    curr_year, curr_week, curr_weekday = target_date.isocalendar()
    if week:
        curr_week = week
    if year:
        curr_year = year

    # Calculate Sunday of that ISO week
    start_of_week = target_date - timedelta(days=target_date.weekday() + 1)
    if target_date.weekday() == 6:  # Sunday
        start_of_week = target_date

    admin_name, admin_phone = await _get_admin_contact(db)

    # Build 7-day date strip
    date_strip = []
    selected_date_str = target_date.strftime("%Y-%m-%d")
    for i in range(7):
        d = start_of_week + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        date_strip.append(DateStripItem(
            day_name=d.strftime("%a").upper(),
            day_number=d.day,
            date_str=d_str,
            is_selected=(d_str == selected_date_str)
        ))

    month_year_lbl = target_date.strftime("%B %Y")
    week_lbl = f"Week {curr_week}"

    # 1. Fetch direct shifts for worker
    raw_shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": selected_date_str,
        "status": {"$ne": "cancelled"}
    }).sort("start_time", 1).to_list(length=100)

    # 2. Fetch active cleaning plans on selected date
    cursor_p = db["cleaning_plans"].find({
        "$or": [
            {"worker_ids": worker_id},
            {"assigned_workers.worker_id": worker_id}
        ],
        "status": {"$ne": "cancelled"}
    })
    plans = await cursor_p.to_list(length=100)
    for p in plans:
        if is_plan_active_on_date(p, selected_date_str):
            exec_doc = await get_or_create_shift_execution(p, selected_date_str, db)
            raw_shifts.append(exec_doc)

    cards = []
    seen_ids = set()
    for s in raw_shifts:
        s_id = str(s.get("_id") or s.get("id"))
        if s_id in seen_ids:
            continue
        seen_ids.add(s_id)

        loc_name = s.get("location_name") or s.get("client_name") or "Location"
        st_time = _format_time_12h(s.get("start_time", "08:00"))
        end_time = _format_time_12h(s.get("end_time", "16:00"))
        t_range = f"{st_time} - {end_time}"
        addr = s.get("location_address") or s.get("address") or ""

        r_list = s.get("rooms", [])
        r_count = len(r_list)
        r_count_str = f"{r_count} rooms" if r_count != 1 else "1 room"

        s_status = str(s.get("status", "published")).lower()
        if s_status == "completed":
            st_badge = "completed"
            st_label = "Completed"
            can_start = False
        elif s_status in ["running", "in_progress"]:
            st_badge = "running"
            st_label = "In Progress"
            can_start = False
        else:
            st_badge = "upcoming"
            st_label = "Upcoming"
            can_start = True

        cards.append(RosterCardItem(
            shift_id=s_id,
            location_name=loc_name,
            status=st_badge,
            status_label=st_label,
            time_range=t_range,
            address_district=addr,
            rooms_count=r_count,
            rooms_count_str=r_count_str,
            admin_contact_name=admin_name,
            admin_contact_phone=admin_phone,
            can_start_shift=can_start
        ))

    return WorkerRosterScreenResponse(
        screen_title="My Roster",
        week_number=curr_week,
        week_label=week_lbl,
        month_year_label=month_year_lbl,
        date_strip=date_strip,
        shifts=cards
    )


@router.get(
    "/shifts/{shift_id}",
    response_model=WorkerRosterDetailResponse,
    summary="Get Worker Roster Shift Detail",
    description="Returns single shift details for worker roster view including Admin contact information."
)
async def get_worker_roster_shift_detail(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Get Worker Roster Shift Detail Endpoint.
    """
    db = get_database()
    admin_name, admin_phone = await _get_admin_contact(db)

    s_doc, _ = await resolve_shift_execution(shift_id, db)
    if not s_doc:
        raise HTTPException(status_code=404, detail="Roster shift not found")

    st_time = _format_time_12h(s_doc.get("start_time", "08:00"))
    end_time = _format_time_12h(s_doc.get("end_time", "16:00"))

    workers = s_doc.get("assigned_workers", []) or s_doc.get("workers", [])

    return WorkerRosterDetailResponse(
        shift_id=str(s_doc.get("_id") or s_doc.get("id")),
        location_name=s_doc.get("location_name") or "Main Location",
        address_district=s_doc.get("location_address") or "",
        date_str=str(s_doc.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        time_range=f"{st_time} - {end_time}",
        status=str(s_doc.get("status", "published")),
        status_label=str(s_doc.get("status", "published")).capitalize(),
        total_rooms=len(s_doc.get("rooms", [])),
        admin_contact_name=admin_name,
        admin_contact_phone=admin_phone,
        assigned_workers_count=len(workers) if isinstance(workers, list) else 1
    )
