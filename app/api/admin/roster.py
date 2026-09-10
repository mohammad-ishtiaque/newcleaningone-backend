import uuid
from datetime import datetime, timezone, date, timedelta
from calendar import monthrange
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional, Dict, Any
from bson import ObjectId
from app.core.database import get_database
from app.schemas.shift import (
    DailyRosterShiftItem, DailyRosterWorkerRow, DailyRosterBanner, AdminDailyRosterResponse,
    WeeklyRosterDayShiftItem, WeeklyRosterDayCell, WeeklyRosterWorkerRow, WeeklyRosterBanner, AdminWeeklyRosterResponse,
    MonthlyRosterDaySummary, MonthlyRosterWorkerRow, MonthlyRosterBanner, AdminMonthlyRosterResponse,
    RosterShiftDetailModalResponse, RosterShiftCreateRequest, ShiftResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.shifts import _format_shift_response
from app.api.worker_shift_utils import is_plan_active_on_date, resolve_shift_execution
from app.api.admin.roster_drafts_dropdowns import roster_drafts_dropdowns_router
from app.api.admin.cleaning_plan_formatters import _resolve_rooms_data
from app.schemas.client_list import CleaningTaskResponse

roster_mgmt_router = APIRouter(prefix="/manager/roster", tags=["Manager Roster Management"])
roster_mgmt_router.include_router(roster_drafts_dropdowns_router)


def _task_applies_on_date(task: CleaningTaskResponse, target_date_str: str) -> bool:
    """
    Matches a room task's frequency_type against one specific roster date:
    - every_visit (or unset): always applies
    - weekly: task.weekly_days must contain this date's weekday (abbr or full name)
    - monthly: task.monthly_dates must contain this date's day-of-month
    - fixed_date: task.fixed_date must equal this exact date
    """
    freq = (task.frequency_type or "every_visit").strip().lower()
    try:
        t_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:
        return freq == "every_visit"

    if freq == "every_visit":
        return True
    elif freq == "weekly":
        day_name = t_dt.strftime("%A").lower()
        day_abbr = t_dt.strftime("%a").lower()
        days = [str(d).strip().lower() for d in (task.weekly_days or [])]
        return any(d in [day_name, day_abbr] or d.startswith(day_abbr) or day_name.startswith(d) for d in days)
    elif freq == "monthly":
        dates = [int(d) for d in (task.monthly_dates or []) if str(d).strip().lstrip("-").isdigit()]
        return t_dt.day in dates
    elif freq == "fixed_date":
        return str(task.fixed_date or "").strip() == target_date_str
    return False


async def _get_room_tasks_for_plan_date(plan_doc: dict, target_date_str: str, db) -> List[CleaningTaskResponse]:
    """Plan -> rooms -> tasks, filtered to only those applicable on target_date_str."""
    room_ids = plan_doc.get("room_ids", []) or []
    if not room_ids:
        return []
    rooms_data = await _resolve_rooms_data(room_ids, db)
    matched: List[CleaningTaskResponse] = []
    for room in rooms_data:
        for task in room.tasks:
            if _task_applies_on_date(task, target_date_str):
                matched.append(task)
    return matched


def _calculate_hours(start_time: str, end_time: str) -> float:
    try:
        sh, sm = map(int, str(start_time).split(":"))
        eh, em = map(int, str(end_time).split(":"))
        s_min = sh * 60 + sm
        e_min = eh * 60 + em
        if e_min < s_min:
            e_min += 24 * 60
        return round((e_min - s_min) / 60.0, 1)
    except Exception:
        return 1.0


# ================================
# 1. Daily Roster View (Filtered: Only workers with shifts)
# ================================

@roster_mgmt_router.get("/daily", response_model=AdminDailyRosterResponse, summary="Get Admin Daily Roster")
async def get_daily_roster(
    target_date: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        dt_obj = datetime.strptime(target_date, "%Y-%m-%d")
    except Exception:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dt_obj = datetime.strptime(target_date, "%Y-%m-%d")

    date_str_formatted = dt_obj.strftime("%A, %d %B %Y")

    # Fetch all approved or admin-created workers
    workers_raw = await db["users"].find({
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }).sort("full_name", 1).to_list(length=200)

    # 1. Fetch direct shifts for target_date
    shifts_raw = await db["shifts"].find({"date": target_date, "status": {"$ne": "cancelled"}}).sort("start_time", 1).to_list(length=300)

    # 2. Fetch active cleaning plans on target_date
    cursor_plans = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    all_plans = await cursor_plans.to_list(length=200)

    worker_shifts_map: Dict[str, List[DailyRosterShiftItem]] = {}
    total_scheduled_shifts = 0
    total_scheduled_hours = 0.0
    seen_shift_ids = set()

    for s in shifts_raw:
        s_id = str(s.get("_id") or s.get("id"))
        seen_shift_ids.add(s_id)
        c_id = str(s.get("client_id", ""))
        c_name = s.get("client_name", "Client")
        l_id = str(s.get("location_id", ""))
        l_name = s.get("location_name", "Location")
        st = s.get("start_time", "08:00")
        et = s.get("end_time", "16:00")
        dur = _calculate_hours(st, et)
        status_str = s.get("status", "scheduled")
        notes = s.get("shift_notes")
        rooms_cnt = len(s.get("rooms", []))

        shift_item = DailyRosterShiftItem(
            shift_id=s_id,
            client_id=c_id,
            client_name=c_name,
            location_id=l_id,
            location_name=l_name,
            start_time=st,
            end_time=et,
            time_label=f"{st} – {et}",
            duration_hours=dur,
            status=status_str,
            shift_notes=notes,
            rooms_count=rooms_cnt
        )

        total_scheduled_shifts += 1
        total_scheduled_hours += dur

        for w in s.get("workers", []):
            wid = str(w.get("worker_id"))
            if wid not in worker_shifts_map:
                worker_shifts_map[wid] = []
            worker_shifts_map[wid].append(shift_item)

    # Add active cleaning plan shifts
    for p in all_plans:
        if is_plan_active_on_date(p, target_date):
            p_id = str(p.get("_id") or p.get("id"))
            if p_id in seen_shift_ids:
                continue

            c_id = str(p.get("client_id", ""))
            c_name = p.get("client_name") or p.get("client_company_name") or "Client"
            l_id = str(p.get("location_id", ""))
            l_name = p.get("location_name", "Location")
            st = p.get("start_time", "08:00")
            dur_mins = p.get("duration_minutes", 60)
            dur_hrs = round(dur_mins / 60.0, 1)
            status_str = p.get("status", "scheduled")
            rooms_cnt = len(p.get("rooms", []))

            plan_shift_item = DailyRosterShiftItem(
                shift_id=p_id,
                client_id=c_id,
                client_name=c_name,
                location_id=l_id,
                location_name=l_name,
                start_time=st,
                end_time=p.get("end_time", "04:00 PM"),
                time_label=f"{st} ({dur_mins}m)",
                duration_hours=dur_hrs,
                status=status_str,
                shift_notes=p.get("notes"),
                rooms_count=rooms_cnt
            )

            w_ids = set([str(w) for w in p.get("worker_ids", [])])
            for w_rec in p.get("assigned_workers", []):
                if isinstance(w_rec, dict) and w_rec.get("worker_id"):
                    w_ids.add(str(w_rec["worker_id"]))

            if w_ids:
                total_scheduled_shifts += 1
                total_scheduled_hours += dur_hrs

            for wid in w_ids:
                if wid not in worker_shifts_map:
                    worker_shifts_map[wid] = []
                worker_shifts_map[wid].append(plan_shift_item)

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        w_shifts = worker_shifts_map.get(wid, [])
        cnt = len(w_shifts)
        if cnt == 0:
            continue

        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")
        wtype = str(w.get("worker_type", "employee"))
        cnt_label = f"{cnt} shift today" if cnt == 1 else f"{cnt} shifts today"

        team_members.append(DailyRosterWorkerRow(
            worker_id=wid,
            worker_name=wname,
            worker_type=wtype,
            profile_photo=photo,
            shifts_today_count=cnt,
            shifts_today_label=cnt_label,
            shifts=w_shifts
        ))

    banner = DailyRosterBanner(
        header_title="DAILY ROSTER",
        date_str=date_str_formatted,
        full_date=target_date,
        total_scheduled_shifts=total_scheduled_shifts,
        total_scheduled_hours=round(total_scheduled_hours, 1)
    )

    return AdminDailyRosterResponse(
        banner=banner,
        total_team_members=len(team_members),
        team_members=team_members
    )


# ================================
# 2. Weekly Roster View
# ================================

@roster_mgmt_router.get("/weekly", response_model=AdminWeeklyRosterResponse, summary="Get Admin Weekly Roster")
async def get_weekly_roster(
    start_date: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    if not start_date:
        today = datetime.now(timezone.utc).date()
        start_dt = today - timedelta(days=today.weekday())
    else:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        except Exception:
            today = datetime.now(timezone.utc).date()
            start_dt = today - timedelta(days=today.weekday())

    end_dt = start_dt + timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")

    week_days = []
    day_date_strs = []
    for i in range(7):
        curr_d = start_dt + timedelta(days=i)
        d_str = curr_d.strftime("%Y-%m-%d")
        day_date_strs.append(d_str)
        week_days.append({
            "day_name": curr_d.strftime("%a"),
            "date_str": curr_d.strftime("%d %b"),
            "full_date": d_str
        })

    range_banner_str = f"{start_dt.strftime('%d %B')} – {end_dt.strftime('%d %B, %Y')}"

    # Fetch workers, shifts, and cleaning plans
    workers_raw = await db["users"].find({
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }).sort("full_name", 1).to_list(length=200)
    shifts_raw = await db["shifts"].find({
        "date": {"$gte": start_date_str, "$lte": end_date_str},
        "status": {"$ne": "cancelled"}
    }).to_list(length=500)
    plans_raw = await db["cleaning_plans"].find({"status": {"$ne": "cancelled"}}).to_list(length=200)

    worker_day_shifts: Dict[str, Dict[str, List[WeeklyRosterDayShiftItem]]] = {}
    total_shifts_count = 0
    total_hours_count = 0.0

    for s in shifts_raw:
        s_date = s.get("date")
        st = s.get("start_time", "08:00")
        et = s.get("end_time", "16:00")
        dur = _calculate_hours(st, et)
        s_id = str(s.get("_id") or s.get("id"))
        loc = s.get("location_name", "Location")
        cid = str(s.get("client_id", ""))
        cname = s.get("client_name", "Client")
        lid = str(s.get("location_id", ""))

        shift_item = WeeklyRosterDayShiftItem(
            shift_id=s_id,
            client_id=cid,
            client_name=cname,
            location_id=lid,
            location_name=loc,
            start_time=st,
            end_time=et,
            duration_hours=dur,
            status=s.get("status", "scheduled")
        )

        total_shifts_count += 1
        total_hours_count += dur

        for w in s.get("workers", []):
            wid = str(w.get("worker_id"))
            if wid not in worker_day_shifts:
                worker_day_shifts[wid] = {d: [] for d in day_date_strs}
            if s_date in worker_day_shifts[wid]:
                worker_day_shifts[wid][s_date].append(shift_item)

    # Check active cleaning plans across the week
    for p in plans_raw:
        for d_str in day_date_strs:
            if is_plan_active_on_date(p, d_str):
                p_id = str(p.get("_id") or p.get("id"))
                st = p.get("start_time", "08:00")
                dur_mins = p.get("duration_minutes", 60)
                dur_hrs = round(dur_mins / 60.0, 1)

                # Plan -> rooms -> tasks, filtered to this specific date by each
                # task's frequency_type (every_visit/weekly/monthly/fixed_date).
                day_tasks = await _get_room_tasks_for_plan_date(p, d_str, db)

                plan_item = WeeklyRosterDayShiftItem(
                    shift_id=p_id,
                    client_id=str(p.get("client_id", "")),
                    client_name=p.get("client_name") or p.get("client_company_name") or "Client",
                    location_id=str(p.get("location_id", "")),
                    location_name=p.get("location_name", "Location"),
                    start_time=st,
                    end_time=p.get("end_time", "04:00 PM"),
                    duration_hours=dur_hrs,
                    status=p.get("status", "scheduled"),
                    tasks=day_tasks
                )

                w_ids = set([str(w) for w in p.get("worker_ids", [])])
                for w_rec in p.get("assigned_workers", []):
                    if isinstance(w_rec, dict) and w_rec.get("worker_id"):
                        w_ids.add(str(w_rec["worker_id"]))

                if w_ids:
                    total_shifts_count += 1
                    total_hours_count += dur_hrs

                for wid in w_ids:
                    if wid not in worker_day_shifts:
                        worker_day_shifts[wid] = {d: [] for d in day_date_strs}
                    if d_str in worker_day_shifts[wid]:
                        worker_day_shifts[wid][d_str].append(plan_item)

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        w_days_map = worker_day_shifts.get(wid, {d: [] for d in day_date_strs})
        worker_week_shifts_count = sum(len(shifts) for shifts in w_days_map.values())
        if worker_week_shifts_count == 0:
            continue

        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")
        daily_cells = []
        for d_str in day_date_strs:
            d_shifts = w_days_map.get(d_str, [])
            d_dt = datetime.strptime(d_str, "%Y-%m-%d")
            daily_cells.append(WeeklyRosterDayCell(
                day_name=d_dt.strftime("%A"),
                date_str=d_dt.strftime("%d %b"),
                full_date=d_str,
                status="On Shift" if d_shifts else "Available",
                shift_count=len(d_shifts),
                shifts=d_shifts
            ))

        lbl = f"{worker_week_shifts_count} shift this week" if worker_week_shifts_count == 1 else f"{worker_week_shifts_count} shifts this week"
        team_members.append(WeeklyRosterWorkerRow(
            worker_id=wid,
            worker_name=wname,
            profile_photo=photo,
            shifts_this_week_count=worker_week_shifts_count,
            shifts_this_week_label=lbl,
            daily_schedule=daily_cells
        ))

    banner = WeeklyRosterBanner(
        header_title="WEEKLY ROSTER",
        range_str=range_banner_str,
        start_date=start_date_str,
        end_date=end_date_str,
        total_scheduled_shifts=total_shifts_count,
        total_scheduled_hours=round(total_hours_count, 1)
    )

    return AdminWeeklyRosterResponse(
        banner=banner,
        days=week_days,
        total_team_members=len(team_members),
        team_members=team_members
    )


# ================================
# 3. Monthly Roster View
# ================================

@roster_mgmt_router.get("/monthly", response_model=AdminMonthlyRosterResponse, summary="Get Admin Monthly Roster")
async def get_monthly_roster(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    target_m = month or now.month
    target_y = year or now.year

    num_days = monthrange(target_y, target_m)[1]
    start_date_str = f"{target_y:04d}-{target_m:02d}-01"
    end_date_str = f"{target_y:04d}-{target_m:02d}-{num_days:02d}"

    dt_first = datetime(target_y, target_m, 1)
    month_name_banner = f"{dt_first.strftime('%B %Y')}"

    workers_raw = await db["users"].find({
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }).sort("full_name", 1).to_list(length=200)
    shifts_raw = await db["shifts"].find({
        "date": {"$gte": start_date_str, "$lte": end_date_str},
        "status": {"$ne": "cancelled"}
    }).to_list(length=1000)
    plans_raw = await db["cleaning_plans"].find({"status": {"$ne": "cancelled"}}).to_list(length=200)

    worker_day_map: Dict[str, Dict[int, List[WeeklyRosterDayShiftItem]]] = {}
    total_shifts_count = 0

    for s in shifts_raw:
        try:
            day_num = int(s.get("date", "2026-01-01").split("-")[2])
            st = s.get("start_time", "08:00")
            et = s.get("end_time", "16:00")
            dur = _calculate_hours(st, et)
            loc = s.get("location_name", "Location")
            cid = str(s.get("client_id", ""))
            cname = s.get("client_name", "Client")
            lid = str(s.get("location_id", ""))
            s_id = str(s.get("_id") or s.get("id"))

            shift_item = WeeklyRosterDayShiftItem(
                shift_id=s_id,
                client_id=cid,
                client_name=cname,
                location_id=lid,
                location_name=loc,
                start_time=st,
                end_time=et,
                duration_hours=dur,
                status=s.get("status", "scheduled")
            )
            total_shifts_count += 1

            for w in s.get("workers", []):
                wid = str(w.get("worker_id"))
                if wid not in worker_day_map:
                    worker_day_map[wid] = {}
                if day_num not in worker_day_map[wid]:
                    worker_day_map[wid][day_num] = []
                worker_day_map[wid][day_num].append(shift_item)
        except Exception:
            continue

    # Add cleaning plans active in this month
    for p in plans_raw:
        for day_num in range(1, num_days + 1):
            d_str = f"{target_y:04d}-{target_m:02d}-{day_num:02d}"
            if is_plan_active_on_date(p, d_str):
                dur_hrs = round(p.get("duration_minutes", 60) / 60.0, 1)
                loc = p.get("location_name", "Location")
                p_id = str(p.get("_id") or p.get("id"))
                st = p.get("start_time", "08:00")

                plan_item = WeeklyRosterDayShiftItem(
                    shift_id=p_id,
                    client_id=str(p.get("client_id", "")),
                    client_name=p.get("client_name") or p.get("client_company_name") or "Client",
                    location_id=str(p.get("location_id", "")),
                    location_name=loc,
                    start_time=st,
                    end_time=p.get("end_time", "04:00 PM"),
                    duration_hours=dur_hrs,
                    status=p.get("status", "scheduled")
                )

                w_ids = set([str(w) for w in p.get("worker_ids", [])])
                for w_rec in p.get("assigned_workers", []):
                    if isinstance(w_rec, dict) and w_rec.get("worker_id"):
                        w_ids.add(str(w_rec["worker_id"]))

                if w_ids:
                    total_shifts_count += 1

                for wid in w_ids:
                    if wid not in worker_day_map:
                        worker_day_map[wid] = {}
                    if day_num not in worker_day_map[wid]:
                        worker_day_map[wid][day_num] = []
                    worker_day_map[wid][day_num].append(plan_item)

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        w_days = worker_day_map.get(wid, {})
        worker_month_shifts = sum(len(shifts) for shifts in w_days.values())
        if worker_month_shifts == 0:
            continue

        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")
        day_summaries = []
        for d in range(1, num_days + 1):
            d_shifts = w_days.get(d, [])
            d_hours = round(sum(sh.duration_hours for sh in d_shifts), 1)
            full_d_str = f"{target_y:04d}-{target_m:02d}-{d:02d}"
            day_summaries.append(MonthlyRosterDaySummary(
                day_number=d,
                full_date=full_d_str,
                shift_count=len(d_shifts),
                total_hours=d_hours,
                shifts=d_shifts
            ))

        lbl = f"{worker_month_shifts} shift" if worker_month_shifts == 1 else f"{worker_month_shifts} shifts"
        team_members.append(MonthlyRosterWorkerRow(
            worker_id=wid,
            worker_name=wname,
            profile_photo=photo,
            total_month_shifts=worker_month_shifts,
            total_month_shifts_label=lbl,
            daily_summaries=day_summaries
        ))

    banner = MonthlyRosterBanner(
        header_title="MONTHLY ROSTER",
        month_str=month_name_banner,
        month=target_m,
        year=target_y,
        total_scheduled_shifts=total_shifts_count,
        total_team_members=len(team_members)
    )

    return AdminMonthlyRosterResponse(
        banner=banner,
        days_in_month=num_days,
        team_members=team_members
    )


# ================================
# 4. Shift Details Modal Card & Management
# ================================

@roster_mgmt_router.get("/shifts/{shift_id}", response_model=RosterShiftDetailModalResponse, summary="Get Shift Details Modal Card")
async def get_roster_shift_details(
    shift_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    shift_doc, _ = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers = shift_doc.get("assigned_workers", []) or shift_doc.get("workers", [])
    primary_worker_name = "Worker"
    primary_worker_id = ""
    primary_worker_photo = None

    if workers and isinstance(workers, list):
        w0 = workers[0]
        if isinstance(w0, dict):
            primary_worker_id = str(w0.get("worker_id") or w0.get("id") or "")
            primary_worker_name = w0.get("name") or w0.get("full_name") or primary_worker_name
            primary_worker_photo = w0.get("profile_picture") or w0.get("profile_photo")

    if primary_worker_id and not primary_worker_photo:
        w_query = {"_id": ObjectId(primary_worker_id)} if ObjectId.is_valid(primary_worker_id) else {"_id": primary_worker_id}
        w_doc = await db["users"].find_one(w_query)
        if w_doc:
            primary_worker_name = w_doc.get("full_name", primary_worker_name)
            primary_worker_photo = w_doc.get("profile_photo")

    st = shift_doc.get("start_time", "08:00")
    et = shift_doc.get("end_time", "14:00")
    time_range_str = f"{st} – {et}"

    loc_name = shift_doc.get("location_name", "Location")
    loc_id = str(shift_doc.get("location_id", ""))
    loc_doc = await db["locations"].find_one({"$or": [{"_id": loc_id}, {"id": loc_id}]})
    loc_address = loc_doc.get("address") if loc_doc else None

    return RosterShiftDetailModalResponse(
        shift_id=str(shift_doc.get("_id") or shift_doc.get("id")),
        worker_id=primary_worker_id,
        worker_name=primary_worker_name,
        worker_profile_photo=primary_worker_photo,
        assignment_label="Scheduled assignment",
        location_id=loc_id,
        location_name=loc_name,
        location_address=loc_address,
        start_time=st,
        end_time=et,
        time_range=time_range_str,
        date=shift_doc.get("date", ""),
        client_id=str(shift_doc.get("client_id", "")),
        client_name=shift_doc.get("client_name", "Client"),
        status=shift_doc.get("status", "scheduled")
    )


@roster_mgmt_router.post("/shifts", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED, summary="Direct Create Roster Shift")
async def create_roster_shift(
    shift_in: RosterShiftCreateRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    cdoc = await db["client_list"].find_one({"$or": [{"_id": shift_in.client_id}, {"id": shift_in.client_id}]})
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    ldoc = await db["locations"].find_one({"$or": [{"_id": shift_in.location_id}, {"id": shift_in.location_id}]})
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    assigned_workers = []
    for wid in shift_in.worker_ids:
        u_query = {"_id": ObjectId(wid)} if ObjectId.is_valid(wid) else {"_id": wid}
        w_user = await db["users"].find_one(u_query)
        if w_user:
            assigned_workers.append({
                "worker_id": str(w_user.get("_id")),
                "name": w_user.get("full_name", "Worker"),
                "profile_picture": w_user.get("profile_photo"),
                "worker_type": str(w_user.get("worker_type", "employee")),
                "shift_role": "cleaning_specialist"
            })

    now = datetime.now(timezone.utc)
    shift_id = f"shift_{uuid.uuid4().hex[:10]}"

    shift_doc = {
        "_id": shift_id,
        "id": shift_id,
        "client_id": shift_in.client_id,
        "client_name": cdoc.get("company_name", "Client"),
        "location_id": shift_in.location_id,
        "location_name": ldoc.get("name", "Location"),
        "date": shift_in.date,
        "start_time": shift_in.start_time,
        "end_time": shift_in.end_time,
        "shift_notes": shift_in.shift_notes,
        "cleaning_plan_id": shift_in.cleaning_plan_id,
        "rooms": [],
        "status": "published",
        "workers": assigned_workers,
        "created_at": now,
        "updated_at": now
    }

    await db["shifts"].insert_one(shift_doc)
    return _format_shift_response(shift_doc)


@roster_mgmt_router.delete("/shifts/{shift_id}", status_code=status.HTTP_200_OK, summary="Delete Roster Shift")
async def delete_roster_shift(
    shift_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": shift_id}, {"id": shift_id}]}
    res = await db["shifts"].delete_one(query)
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Shift not found")
    return {"message": "Roster shift deleted successfully"}
