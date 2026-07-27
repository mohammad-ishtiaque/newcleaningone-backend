import calendar
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import (
    ClientScheduleResponse, ScheduleSummaryCounters, ClientCleaningVisitItem
)

router = APIRouter(prefix="/client/schedule", tags=["Clients Schedule"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


@router.get("", response_model=ClientScheduleResponse, summary="Get Client Cleaning Visits Schedule")
async def get_client_schedule(
    status_val: Optional[str] = None,  # "all", "scheduled", "in_progress", "completed"
    time_frame: Optional[str] = None,  # "today", "this_week", "this_month", "all"
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)
    current_year = now.year
    current_month = now.month

    first_day_month_str = f"{current_year:04d}-{current_month:02d}-01"
    last_day_num = calendar.monthrange(current_year, current_month)[1]
    last_day_month_str = f"{current_year:04d}-{current_month:02d}-{last_day_num:02d}"

    # 1. Summary Statistics for current month
    month_query = {
        "client_id": client_id,
        "date": {"$gte": first_day_month_str, "$lte": last_day_month_str}
    }
    this_month_total = await db["shifts"].count_documents(month_query)

    completed_query = {
        "client_id": client_id,
        "date": {"$gte": first_day_month_str, "$lte": last_day_month_str},
        "status": "completed"
    }
    completed_total = await db["shifts"].count_documents(completed_query)

    upcoming_query = {
        "client_id": client_id,
        "date": {"$gte": first_day_month_str, "$lte": last_day_month_str},
        "status": {"$in": ["published", "upcoming", "running", "in_progress", "draft"]}
    }
    upcoming_total = await db["shifts"].count_documents(upcoming_query)

    summary = ScheduleSummaryCounters(
        this_month_visits=this_month_total,
        completed_visits=completed_total,
        upcoming_visits=upcoming_total
    )

    # 2. Main Query Construction
    query = {"client_id": client_id}

    # Status Filter
    if status_val and status_val.lower() != "all":
        st_lower = status_val.lower()
        if st_lower == "scheduled":
            query["status"] = {"$in": ["published", "upcoming", "draft"]}
        elif st_lower == "in_progress":
            query["status"] = {"$in": ["running", "in_progress"]}
        elif st_lower == "completed":
            query["status"] = "completed"

    # Time Frame Filter
    if time_frame and time_frame.lower() != "all":
        tf_lower = time_frame.lower()
        today_str = now.strftime("%Y-%m-%d")

        if tf_lower == "today":
            query["date"] = today_str
        elif tf_lower == "this_week":
            start_week = now - timedelta(days=now.weekday())
            end_week = start_week + timedelta(days=6)
            query["date"] = {
                "$gte": start_week.strftime("%Y-%m-%d"),
                "$lte": end_week.strftime("%Y-%m-%d")
            }
        elif tf_lower == "this_month":
            query["date"] = {"$gte": first_day_month_str, "$lte": last_day_month_str}

    total_count = await db["shifts"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["shifts"].find(query).sort([("date", 1), ("start_time", 1)]).skip(skip).limit(limit)
    raw_shifts = await cursor.to_list(length=limit)

    visits_res = []
    for s in raw_shifts:
        shift_id = str(s.get("_id") or s.get("id"))
        d_str = s.get("date", now.strftime("%Y-%m-%d"))

        # Parse date formatting
        try:
            y, m, d = map(int, d_str.split("-"))
            dt_obj = datetime(y, m, d)
            badge_month = dt_obj.strftime("%B").upper()
            badge_day = str(dt_obj.day)
            formatted_date = dt_obj.strftime("%A, %B %d, %Y")
        except Exception:
            badge_month = "JULY"
            badge_day = "1"
            formatted_date = d_str

        # Time interval
        s_time = s.get("start_time", "09:00 AM")
        e_time = s.get("end_time", "12:00 PM")
        time_interval = f"{s_time} - {e_time}"

        # Team name
        team_name = "Team Alpha"
        workers = s.get("workers", [])
        if workers:
            w0 = workers[0]
            w_team = w0.get("team_name") or w0.get("worker_type")
            if w_team:
                team_name = str(w_team)

        # Service type
        service_type = s.get("clean_type") or s.get("service_name") or "Regular Cleaning"
        if isinstance(service_type, str):
            service_type = service_type.replace("_", " ").title()

        # Status mapping
        raw_st = s.get("status", "published")
        if raw_st in ["running", "in_progress"]:
            status_display = "In Progress"
        elif raw_st == "completed":
            status_display = "Completed"
        else:
            status_display = "Scheduled"

        visits_res.append(ClientCleaningVisitItem(
            id=shift_id,
            date_badge_month=badge_month,
            date_badge_day=badge_day,
            formatted_date=formatted_date,
            time_interval=time_interval,
            assigned_team=team_name,
            service_type=service_type,
            status=status_display,
            location_name=s.get("location_name", "Main Location"),
            location_id=str(s.get("location_id", "")),
            date_raw=d_str
        ))

    return ClientScheduleResponse(
        summary=summary,
        total_count=total_count,
        page=page,
        limit=limit,
        visits=visits_res
    )
