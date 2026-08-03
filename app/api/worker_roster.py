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

router = APIRouter(prefix="/worker/roster", tags=["Worker Roaster Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


async def _get_admin_contact(db) -> tuple[str, str]:
    """Helper to fetch primary Admin name and phone from MongoDB users collection."""
    admin_user = await db["users"].find_one({"role": "admin"})
    if admin_user:
        name = admin_user.get("full_name") or admin_user.get("name") or "John Smith"
        phone = admin_user.get("phone") or admin_user.get("phone_number") or "+31 20 555 7200"
        return name, phone
    return "John Smith", "+31 20 555 7200"


def _format_time_12h(time_str: str) -> str:
    """Formats '14:00' to '2:00 PM'."""
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return time_str


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

    # Query worker shifts in MongoDB
    cursor = db["shifts"].find({
        "workers.worker_id": worker_id,
        "status": {"$ne": "cancelled"}
    }).sort("date", 1)

    raw_shifts = await cursor.to_list(length=100)

    # Filter for selected date if shifts exist
    selected_shifts = [s for s in raw_shifts if s.get("date") == selected_date_str]
    if not selected_shifts and raw_shifts:
        selected_shifts = raw_shifts[:5]

    cards = []
    if selected_shifts:
        for s in selected_shifts:
            s_id = str(s.get("_id") or s.get("id"))
            loc_name = s.get("location_name") or s.get("client_name") or "Hilton Hotel"
            st_time = _format_time_12h(s.get("start_time", "14:00"))
            end_time = _format_time_12h(s.get("end_time", "18:00"))
            t_range = f"{st_time} - {end_time}"
            addr = s.get("location_address") or s.get("address") or "Downtown Business District"

            r_list = s.get("rooms", [])
            r_count = len(r_list) if r_list else 20
            r_count_str = f"{r_count} rooms"

            s_status = s.get("status", "published").lower()
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
    else:
        # Default mock shift cards matching Image Mockup layout
        cards = [
            RosterCardItem(
                shift_id="sh_rost_1",
                location_name="Hilton Hotel",
                status="completed",
                status_label="Completed",
                time_range="2:00 PM - 6:00 PM",
                address_district="Downtown Business District",
                rooms_count=20,
                rooms_count_str="20 rooms",
                admin_contact_name=admin_name,
                admin_contact_phone=admin_phone,
                can_start_shift=False
            ),
            RosterCardItem(
                shift_id="sh_rost_2",
                location_name="Office Building A",
                status="upcoming",
                status_label="Upcoming",
                time_range="2:00 PM - 6:00 PM",
                address_district="Downtown Business District",
                rooms_count=20,
                rooms_count_str="20 rooms",
                admin_contact_name=admin_name,
                admin_contact_phone=admin_phone,
                can_start_shift=True
            ),
            RosterCardItem(
                shift_id="sh_rost_3",
                location_name="Apartment Complex",
                status="upcoming",
                status_label="Upcoming",
                time_range="2:00 PM - 6:00 PM",
                address_district="Downtown Business District",
                rooms_count=20,
                rooms_count_str="20 rooms",
                admin_contact_name=admin_name,
                admin_contact_phone=admin_phone,
                can_start_shift=True
            )
        ]

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

    s_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not s_doc:
        return WorkerRosterDetailResponse(
            shift_id=shift_id,
            location_name="Hilton Hotel",
            address_district="Downtown Business District",
            date_str=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            time_range="2:00 PM - 6:00 PM",
            status="completed",
            status_label="Completed",
            total_rooms=20,
            admin_contact_name=admin_name,
            admin_contact_phone=admin_phone,
            assigned_workers_count=3
        )

    st_time = _format_time_12h(s_doc.get("start_time", "14:00"))
    end_time = _format_time_12h(s_doc.get("end_time", "18:00"))

    return WorkerRosterDetailResponse(
        shift_id=str(s_doc.get("_id") or s_doc.get("id")),
        location_name=s_doc.get("location_name", "Main Location"),
        address_district=s_doc.get("location_address", "Downtown Business District"),
        date_str=s_doc.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        time_range=f"{st_time} - {end_time}",
        status=s_doc.get("status", "published"),
        status_label=s_doc.get("status", "published").capitalize(),
        total_rooms=len(s_doc.get("rooms", [])),
        admin_contact_name=admin_name,
        admin_contact_phone=admin_phone,
        assigned_workers_count=len(s_doc.get("workers", []))
    )
