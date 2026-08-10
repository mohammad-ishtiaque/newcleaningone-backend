import uuid
from datetime import datetime, timezone, date, timedelta
from calendar import monthrange
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional, Dict
from bson import ObjectId
from app.core.database import get_database
from app.schemas.shift import (
    DailyRosterShiftItem, DailyRosterWorkerRow, DailyRosterBanner, AdminDailyRosterResponse,
    WeeklyRosterDayShiftItem, WeeklyRosterDayCell, WeeklyRosterWorkerRow, WeeklyRosterBanner, AdminWeeklyRosterResponse,
    MonthlyRosterDaySummary, MonthlyRosterWorkerRow, MonthlyRosterBanner, AdminMonthlyRosterResponse,
    RosterShiftDetailModalResponse, RosterShiftCreateRequest, ShiftResponse,
    ShiftDraftCreate, ShiftDraftResponse, ShiftDraftUpdate, ShiftDraftPaginatedResponse,
    WorkerDropdownItem, WorkerDropdownPaginatedResponse, ShiftAssignRequest, ShiftWorkerDetail
)
from app.schemas.client_list import LocationDropdownItemResponse, LocationDropdownPaginatedResponse, RoomDropdownItemResponse, RoomDropdownPaginatedResponse
from app.models.user import UserInDB, RoleEnum
from app.api.admin.profile_company import require_manager
from app.api.admin.shifts import _format_shift_response

roster_mgmt_router = APIRouter(prefix="/manager/roster", tags=["Admin Roaster Management"])

def _calculate_hours(start_time: str, end_time: str) -> float:
    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
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

    # Fetch all workers
    workers_raw = await db["users"].find({"role": "worker"}).sort("full_name", 1).to_list(length=200)

    # Fetch all shifts for target_date
    shifts_raw = await db["shifts"].find({"date": target_date}).sort("start_time", 1).to_list(length=300)

    worker_shifts_map: Dict[str, List[DailyRosterShiftItem]] = {}
    total_scheduled_shifts = 0
    total_scheduled_hours = 0.0

    for s in shifts_raw:
        s_id = str(s.get("_id") or s.get("id"))
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

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        w_shifts = worker_shifts_map.get(wid, [])
        cnt = len(w_shifts)
        
        # User requirement: Only include workers who HAVE a shift on that date!
        if cnt == 0:
            continue

        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")
        wtype = str(w.get("worker_type", "employee"))
        cnt_label = f"{cnt} shift today" if cnt == 1 else f"{cnt} shifts today"

        team_members.append(DailyRosterWorkerRow(
            worker_id=wid,
            worker_name=wname,
            profile_photo=photo,
            worker_type=wtype,
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
# 2. Weekly Roster View (Filtered: Only workers with shifts)
# ================================

@roster_mgmt_router.get("/weekly", response_model=AdminWeeklyRosterResponse, summary="Get Admin Weekly Roster")
async def get_weekly_roster(
    start_date: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    if not start_date:
        today = datetime.now(timezone.utc).date()
        idx = (today.weekday() + 1) % 7
        start_dt = today - timedelta(days=idx)
    else:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        except Exception:
            today = datetime.now(timezone.utc).date()
            idx = (today.weekday() + 1) % 7
            start_dt = today - timedelta(days=idx)

    end_dt = start_dt + timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")

    week_days = []
    current_d = start_dt
    for _ in range(7):
        week_days.append({
            "day_name": current_d.strftime("%a").upper(),
            "date_str": current_d.strftime("%d %b").lstrip("0"),
            "full_date": current_d.strftime("%Y-%m-%d")
        })
        current_d += timedelta(days=1)

    range_banner_str = f"{start_dt.strftime('%d %b').lstrip('0')} – {end_dt.strftime('%d %b').lstrip('0')} {end_dt.year}"

    workers_raw = await db["users"].find({"role": "worker"}).sort("full_name", 1).to_list(length=200)
    shifts_raw = await db["shifts"].find({
        "date": {"$gte": start_date_str, "$lte": end_date_str}
    }).to_list(length=500)

    worker_date_shifts: Dict[str, Dict[str, List[WeeklyRosterDayShiftItem]]] = {}
    total_shifts_count = 0
    total_hours_count = 0.0

    for s in shifts_raw:
        s_id = str(s.get("_id") or s.get("id"))
        s_date = s.get("date")
        c_id = str(s.get("client_id", ""))
        c_name = s.get("client_name", "Client")
        l_id = str(s.get("location_id", ""))
        l_name = s.get("location_name", "Location")
        st = s.get("start_time", "08:00")
        et = s.get("end_time", "16:00")
        dur = _calculate_hours(st, et)

        shift_item = WeeklyRosterDayShiftItem(
            shift_id=s_id,
            client_id=c_id,
            client_name=c_name,
            location_id=l_id,
            location_name=l_name,
            start_time=st,
            end_time=et,
            duration_hours=dur,
            status=s.get("status", "scheduled")
        )

        total_shifts_count += 1
        total_hours_count += dur

        for w in s.get("workers", []):
            wid = str(w.get("worker_id"))
            if wid not in worker_date_shifts:
                worker_date_shifts[wid] = {}
            if s_date not in worker_date_shifts[wid]:
                worker_date_shifts[wid][s_date] = []
            worker_date_shifts[wid][s_date].append(shift_item)

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")

        daily_cells = []
        worker_week_shifts_count = 0

        for d_meta in week_days:
            f_date = d_meta["full_date"]
            day_shifts = worker_date_shifts.get(wid, {}).get(f_date, [])
            scnt = len(day_shifts)
            worker_week_shifts_count += scnt

            daily_cells.append(WeeklyRosterDayCell(
                day_name=d_meta["day_name"],
                date_str=d_meta["date_str"],
                full_date=f_date,
                status="Scheduled" if scnt > 0 else "Available",
                shift_count=scnt,
                shifts=day_shifts
            ))

        # Only include workers who have shifts this week!
        if worker_week_shifts_count == 0:
            continue

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
# 3. Monthly Roster View (Filtered: Only workers with shifts)
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

    workers_raw = await db["users"].find({"role": "worker"}).sort("full_name", 1).to_list(length=200)
    shifts_raw = await db["shifts"].find({
        "date": {"$gte": start_date_str, "$lte": end_date_str}
    }).to_list(length=1000)

    worker_month_shifts: Dict[str, Dict[int, List[WeeklyRosterDayShiftItem]]] = {}
    total_shifts_count = 0

    for s in shifts_raw:
        s_id = str(s.get("_id") or s.get("id"))
        s_date = s.get("date", "")
        try:
            day_num = int(s_date.split("-")[2])
        except Exception:
            continue

        st = s.get("start_time", "08:00")
        et = s.get("end_time", "16:00")
        dur = _calculate_hours(st, et)

        item = WeeklyRosterDayShiftItem(
            shift_id=s_id,
            client_id=str(s.get("client_id", "")),
            client_name=s.get("client_name", "Client"),
            location_id=str(s.get("location_id", "")),
            location_name=s.get("location_name", "Location"),
            start_time=st,
            end_time=et,
            duration_hours=dur,
            status=s.get("status", "scheduled")
        )
        total_shifts_count += 1

        for w in s.get("workers", []):
            wid = str(w.get("worker_id"))
            if wid not in worker_month_shifts:
                worker_month_shifts[wid] = {}
            if day_num not in worker_month_shifts[wid]:
                worker_month_shifts[wid][day_num] = []
            worker_month_shifts[wid][day_num].append(item)

    team_members = []
    for w in workers_raw:
        wid = str(w.get("_id"))
        wname = w.get("full_name", "Worker")
        photo = w.get("profile_photo")

        daily_summaries = []
        worker_total_month_shifts = 0

        for d in range(1, num_days + 1):
            d_shifts = worker_month_shifts.get(wid, {}).get(d, [])
            scnt = len(d_shifts)
            thours = sum(it.duration_hours for it in d_shifts)
            worker_total_month_shifts += scnt

            f_date_str = f"{target_y:04d}-{target_m:02d}-{d:02d}"
            daily_summaries.append(MonthlyRosterDaySummary(
                day_number=d,
                full_date=f_date_str,
                shift_count=scnt,
                total_hours=round(thours, 1),
                shifts=d_shifts
            ))

        # Only include workers who have shifts this month!
        if worker_total_month_shifts == 0:
            continue

        lbl = f"{worker_total_month_shifts} shift" if worker_total_month_shifts == 1 else f"{worker_total_month_shifts} shifts"

        team_members.append(MonthlyRosterWorkerRow(
            worker_id=wid,
            worker_name=wname,
            profile_photo=photo,
            total_month_shifts=worker_total_month_shifts,
            total_month_shifts_label=lbl,
            daily_summaries=daily_summaries
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
# 4. Shift Details Modal (Picture 4)
# ================================

@roster_mgmt_router.get("/shifts/{shift_id}", response_model=RosterShiftDetailModalResponse, summary="Get Shift Details Modal Card")
async def get_roster_shift_details(
    shift_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": shift_id}, {"id": shift_id}]}
    shift_doc = await db["shifts"].find_one(query)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers = shift_doc.get("workers", [])
    primary_worker_name = workers[0].get("name", "Worker") if workers else "Worker"
    primary_worker_id = str(workers[0].get("worker_id", "")) if workers else ""
    primary_worker_photo = workers[0].get("profile_picture") if workers else None

    if primary_worker_id and not primary_worker_photo:
        w_doc = await db["users"].find_one({"_id": ObjectId(primary_worker_id)}) if ObjectId.is_valid(primary_worker_id) else await db["users"].find_one({"_id": primary_worker_id})
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

# ================================
# 5. Shift Creation Step 1: Create Shift Draft
# ================================

@roster_mgmt_router.post("/drafts", response_model=ShiftDraftResponse, status_code=status.HTTP_201_CREATED, summary="Step 1: Create Shift Draft")
async def create_roster_shift_draft(
    draft_in: ShiftDraftCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    cdoc = await db["client_list"].find_one({"$or": [{"_id": draft_in.client_id}, {"id": draft_in.client_id}]})
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    ldoc = await db["locations"].find_one({"$or": [{"_id": draft_in.location_id}, {"id": draft_in.location_id}]})
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(timezone.utc)
    draft_id = f"draft_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": draft_id,
        "id": draft_id,
        "client_id": draft_in.client_id,
        "client_name": cdoc.get("company_name", "Client"),
        "location_id": draft_in.location_id,
        "location_name": ldoc.get("name", "Location"),
        "date": draft_in.date,
        "start_time": draft_in.start_time,
        "end_time": draft_in.end_time,
        "repeat_shift": draft_in.repeat_shift or "Does not repeat",
        "shift_notes": draft_in.shift_notes,
        "cleaning_plan_id": draft_in.cleaning_plan_id,
        "rooms": [],
        "total_tasks_count": 0,
        "total_photo_required": 0,
        "status": "draft",
        "created_at": now,
        "updated_at": now
    }

    await db["shift_drafts"].insert_one(doc)

    return ShiftDraftResponse(
        id=draft_id,
        client_id=draft_in.client_id,
        client_name=cdoc.get("company_name", "Client"),
        location_id=draft_in.location_id,
        location_name=ldoc.get("name", "Location"),
        date=draft_in.date,
        start_time=draft_in.start_time,
        end_time=draft_in.end_time,
        repeat_shift=draft_in.repeat_shift or "Does not repeat",
        shift_notes=draft_in.shift_notes,
        cleaning_plan_id=draft_in.cleaning_plan_id,
        rooms=[],
        total_tasks_count=0,
        total_photo_required=0,
        status="draft",
        created_at=now,
        updated_at=now
    )

@roster_mgmt_router.get("/drafts", response_model=ShiftDraftPaginatedResponse, summary="List Shift Drafts")
async def list_roster_shift_drafts(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    total_count = await db["shift_drafts"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["shift_drafts"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_drafts = await cursor.to_list(length=limit)

    draft_list = []
    for d in raw_drafts:
        cat = d.get("created_at") if isinstance(d.get("created_at"), datetime) else datetime.now(timezone.utc)
        uat = d.get("updated_at") if isinstance(d.get("updated_at"), datetime) else datetime.now(timezone.utc)
        draft_list.append(ShiftDraftResponse(
            id=str(d.get("_id") or d.get("id")),
            client_id=d.get("client_id", ""),
            client_name=d.get("client_name", "Client"),
            location_id=d.get("location_id", ""),
            location_name=d.get("location_name", "Location"),
            date=d.get("date", ""),
            start_time=d.get("start_time", "08:00"),
            end_time=d.get("end_time", "16:00"),
            repeat_shift=d.get("repeat_shift", "Does not repeat"),
            shift_notes=d.get("shift_notes"),
            cleaning_plan_id=d.get("cleaning_plan_id"),
            rooms=[],
            total_tasks_count=0,
            total_photo_required=0,
            status=d.get("status", "draft"),
            created_at=cat,
            updated_at=uat
        ))

    return ShiftDraftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        drafts=draft_list
    )

# ================================
# 6. Shift Creation Step 2: Assign Employees & Publish Shift
# ================================

@roster_mgmt_router.post("/shifts/assign", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED, summary="Step 2: Assign Employees & Publish Shift")
async def assign_workers_and_publish_roster_shift(
    assign_in: ShiftAssignRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    draft_query = {"$or": [{"_id": assign_in.draft_id}, {"id": assign_in.draft_id}]}
    draft_doc = await db["shift_drafts"].find_one(draft_query)
    if not draft_doc:
        raise HTTPException(status_code=404, detail="Shift draft not found")

    w_ids = assign_in.worker_ids or []
    assigned_workers = []

    for wid in w_ids:
        u_query = {"_id": ObjectId(wid)} if ObjectId.is_valid(wid) else {"_id": wid}
        w_user = await db["users"].find_one(u_query)
        if w_user:
            role_label = "team_leader" if (assign_in.team_leader_id and str(w_user.get("_id")) == assign_in.team_leader_id) else "cleaning_specialist"
            assigned_workers.append({
                "worker_id": str(w_user.get("_id")),
                "name": w_user.get("full_name", "Worker"),
                "profile_picture": w_user.get("profile_photo"),
                "worker_type": str(w_user.get("worker_type", "employee")),
                "shift_role": role_label
            })

    now = datetime.now(timezone.utc)
    shift_id = f"shift_{uuid.uuid4().hex[:10]}"

    shift_doc = {
        "_id": shift_id,
        "id": shift_id,
        "draft_id": assign_in.draft_id,
        "client_id": draft_doc.get("client_id"),
        "client_name": draft_doc.get("client_name"),
        "location_id": draft_doc.get("location_id"),
        "location_name": draft_doc.get("location_name"),
        "date": draft_doc.get("date"),
        "start_time": draft_doc.get("start_time"),
        "end_time": draft_doc.get("end_time"),
        "repeat_shift": draft_doc.get("repeat_shift", "Does not repeat"),
        "shift_notes": draft_doc.get("shift_notes"),
        "cleaning_plan_id": draft_doc.get("cleaning_plan_id"),
        "rooms": draft_doc.get("rooms", []),
        "status": "published",
        "workers": assigned_workers,
        "created_at": now,
        "updated_at": now
    }

    await db["shifts"].insert_one(shift_doc)
    await db["shift_drafts"].delete_one(draft_query)

    return _format_shift_response(shift_doc)

# ================================
# 7. Roster Dropdowns (Clients, Locations, Rooms, Workers)
# ================================

@roster_mgmt_router.get("/dropdowns/clients", summary="Roster Client Dropdown List")
async def get_roster_client_dropdowns(
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"is_active": True}
    if search:
        query["company_name"] = {"$regex": search, "$options": "i"}

    clients = await db["client_list"].find(query).sort("company_name", 1).to_list(length=100)
    return [
        {
            "client_id": str(c.get("_id") or c.get("id")),
            "company_name": c.get("company_name", "Client"),
            "email": c.get("email", "")
        }
        for c in clients
    ]

@roster_mgmt_router.get("/dropdowns/locations", response_model=LocationDropdownPaginatedResponse, summary="Roster Location Dropdown List")
async def get_roster_location_dropdowns(
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("name", 1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    dropdowns = []
    for l in raw_locs:
        cid = l.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        cname = cdoc.get("company_name", "Client") if cdoc else "Client"
        lid = str(l.get("_id") or l.get("id"))

        dropdowns.append(LocationDropdownItemResponse(
            location_id=lid,
            location_name=l.get("name", ""),
            client_id=cid,
            client_name=cname,
            address=l.get("address", "")
        ))

    return LocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=dropdowns
    )

@roster_mgmt_router.get("/dropdowns/rooms", response_model=RoomDropdownPaginatedResponse, summary="Roster Room Dropdown List")
async def get_roster_room_dropdowns(
    location_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if location_id:
        query["location_id"] = location_id
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("name", 1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    dropdowns = []
    for r in raw_rooms:
        lid = r.get("location_id", "")
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        lname = ldoc.get("name", "Location") if ldoc else "Location"
        rid = str(r.get("_id") or r.get("id"))

        dropdowns.append(RoomDropdownItemResponse(
            room_id=rid,
            room_name=r.get("name", ""),
            location_id=lid,
            location_name=lname,
            floor=r.get("floor", 1),
            cleaning_type=r.get("cleaning_type", "standard"),
            duration=r.get("est_cleaning_duration_minutes", 30)
        ))

    return RoomDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=dropdowns
    )

@roster_mgmt_router.get("/dropdowns/workers", response_model=WorkerDropdownPaginatedResponse, summary="Roster Employee/Worker List (Step 2 Selection)")
async def get_roster_worker_dropdowns(
    worker_type: Optional[str] = None,  # all, employee, freelancer
    status_filter: Optional[str] = None,  # available, on_shift, off_duty
    search: Optional[str] = None,
    target_date: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker", "is_active": True}

    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("full_name", 1).skip(skip).limit(limit)
    raw_workers = await cursor.to_list(length=limit)

    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Find workers currently on shift today
    running_shifts = await db["shifts"].find({"date": target_date}).to_list(length=300)
    on_shift_worker_ids = set()
    for s in running_shifts:
        for w in s.get("workers", []):
            on_shift_worker_ids.add(str(w.get("worker_id")))

    items = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        w_status = "on_shift" if wid in on_shift_worker_ids else "available"
        if status_filter and status_filter.lower() != w_status:
            continue

        items.append(WorkerDropdownItem(
            worker_id=wid,
            name=w.get("full_name", "Worker"),
            profile_picture=w.get("profile_photo"),
            status=w_status,
            worker_type=str(w.get("worker_type", "employee")),
            position=w.get("position")
        ))

    return WorkerDropdownPaginatedResponse(
        total_count=len(items),
        page=page,
        limit=limit,
        workers=items
    )

# ================================
# 8. Direct Shift Creation & Management
# ================================

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
