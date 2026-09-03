import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from zoneinfo import ZoneInfo
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.dependencies.timezone import get_request_timezone
from app.core.timezone_utils import now_in_tz, get_today_str, get_timezone
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import (
    ClientLiveStatusResponse, ClientLiveRoomProgress, ClientLiveTaskItem,
    AssignedCleanerCard, CurrentLocationCard, ArrivalTimeCard, ShiftResponse,
    ClientLiveShiftPaginatedResponse, ClientLiveStatusSessionSummary
)

router = APIRouter(prefix="/client/live-status", tags=["Client Live Status Management"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


def _build_client_live_status(
    shift_doc: dict,
    loc_address: Optional[str] = None,
    cleaner_doc: Optional[dict] = None,
    active_sessions: Optional[List[ClientLiveStatusSessionSummary]] = None
) -> ClientLiveStatusResponse:
    shift_id = str(shift_doc.get("_id") or shift_doc.get("id"))
    client_name = shift_doc.get("client_name") or shift_doc.get("client_company_name") or "Client"
    location_name = shift_doc.get("location_name") or "Location"

    # 1. Dynamic Assigned Cleaner Information
    cleaner_name = "Assigned Cleaner"
    cleaner_id = "cleaner_1"
    cleaner_type = "Cleaning Specialist"
    cleaner_pic = None
    worker_checkin_time = None

    workers = shift_doc.get("assigned_workers", []) or shift_doc.get("workers", [])
    if workers and isinstance(workers, list):
        w0 = workers[0]
        if isinstance(w0, dict):
            cleaner_id = str(w0.get("worker_id") or w0.get("id") or "cleaner_1")
            cleaner_name = w0.get("name") or w0.get("full_name") or cleaner_name
            cleaner_pic = w0.get("profile_picture") or w0.get("profile_photo")
            cleaner_type = w0.get("position") or w0.get("worker_type") or cleaner_type
            worker_checkin_time = w0.get("checkin_time")

    if cleaner_doc:
        cleaner_name = cleaner_doc.get("full_name") or cleaner_doc.get("name") or cleaner_name
        cleaner_pic = cleaner_doc.get("profile_photo") or cleaner_doc.get("profile_picture") or cleaner_pic
        w_t = cleaner_doc.get("position") or cleaner_doc.get("worker_type") or cleaner_doc.get("onboarding_draft", {}).get("worker_type") or cleaner_type
        cleaner_type = str(w_t)

    assigned_cleaner = AssignedCleanerCard(
        worker_id=cleaner_id,
        name=cleaner_name,
        designation=cleaner_type,
        profile_picture=cleaner_pic
    )

    # 2. Current Location Information
    current_location = CurrentLocationCard(
        location_id=str(shift_doc.get("location_id", "")),
        location_name=location_name,
        address_subtitle=loc_address or location_name
    )

    # 3. Dynamic Arrival Time Information from Check-in
    start_time_str = shift_doc.get("start_time", "08:00 AM")
    date_val = shift_doc.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    try:
        y, m, d = map(int, str(date_val).split("-"))
        dt_obj = datetime(y, m, d)
        date_formatted = dt_obj.strftime("%b %d, %Y")
    except Exception:
        date_formatted = str(date_val)

    checkin_dt = shift_doc.get("checkin_time") or worker_checkin_time
    if checkin_dt:
        if isinstance(checkin_dt, str):
            try:
                checkin_dt = datetime.fromisoformat(checkin_dt)
            except Exception:
                pass
        if isinstance(checkin_dt, datetime):
            arrival_time_str = checkin_dt.strftime("%I:%M %p").lstrip("0")
        else:
            arrival_time_str = str(checkin_dt)
        arrival_status = f"On time • {date_formatted}"
    else:
        arrival_time_str = start_time_str
        arrival_status = f"Scheduled • {date_formatted}"

    arrival_time_info = ArrivalTimeCard(
        arrival_time=arrival_time_str,
        arrival_status=arrival_status,
        date_str=date_formatted
    )

    # 4. Rooms & Checklist Task Progress Shared by Worker
    rooms = shift_doc.get("rooms", [])
    all_rooms_progress = []
    active_room_progress = None
    active_room_name = location_name

    total_all_tasks = 0
    completed_all_tasks = 0

    for r_idx, r in enumerate(rooms):
        r_name = r.get("room_name") or r.get("name") or r.get("custom_room_name") or f"Room {r_idx+1}"
        r_tasks = r.get("tasks", [])
        total_all_tasks += len(r_tasks)

        live_tasks = []
        found_active_for_room = False

        for idx, t in enumerate(r_tasks):
            t_is_done = t.get("is_completed", False)
            if t_is_done:
                completed_all_tasks += 1
                t_status = "DONE"
                c_at = t.get("completed_at")
                if c_at and isinstance(c_at, datetime):
                    t_time_str = c_at.strftime("%I:%M %p").lstrip("0")
                elif c_at:
                    t_time_str = str(c_at)
                else:
                    t_time_str = "Completed"
            elif not found_active_for_room and (idx == 0 or (idx > 0 and r_tasks[idx-1].get("is_completed"))):
                t_status = "ACTIVE"
                t_time_str = "In Progress"
                found_active_for_room = True
            else:
                t_status = "PENDING"
                t_time_str = None

            live_tasks.append(ClientLiveTaskItem(
                id=str(t.get("id")),
                name=t.get("name", "Task"),
                time_str=t_time_str,
                status=t_status,
                completed_at=t.get("completed_at") if isinstance(t.get("completed_at"), datetime) else None
            ))

        completed_count = sum(1 for t in r_tasks if t.get("is_completed"))
        room_prog = ClientLiveRoomProgress(
            room_id=str(r.get("room_id", r.get("id", f"r_{r_idx}"))),
            room_name=r_name,
            location_name=location_name,
            completed_tasks_count=completed_count,
            total_tasks_count=len(r_tasks),
            tasks=live_tasks
        )

        all_rooms_progress.append(room_prog)

        if active_room_progress is None or r.get("status") in ["in_progress", "photo_submitted"]:
            active_room_progress = room_prog
            active_room_name = r_name

    if active_room_progress is None and all_rooms_progress:
        active_room_progress = all_rooms_progress[0]

    if total_all_tasks > 0:
        overall_pct = round((completed_all_tasks / total_all_tasks) * 100.0, 1)
    else:
        overall_pct = float(shift_doc.get("overall_progress_percentage", 0.0))

    active_location_text = f"Active at {location_name} • {active_room_name}"
    end_time_str = shift_doc.get("end_time", "12:00 PM")

    status_raw = str(shift_doc.get("status", "scheduled")).lower()
    if status_raw in ["in_progress", "running"]:
        status_lbl = "CLEANING IN PROGRESS"
    elif status_raw in ["completed"]:
        status_lbl = "COMPLETED"
    elif status_raw in ["photo_submitted"]:
        status_lbl = "PENDING REVIEW"
    else:
        status_lbl = "SCHEDULED"

    # Calculate checkout blockers
    pending_approvals = 0
    rejected_photos = 0
    uncompleted_tasks = 0
    pending_photos = 0
    blocker_reasons = []

    for r in rooms:
        for t in r.get("tasks", []):
            if not t.get("is_completed"):
                uncompleted_tasks += 1
            for p in t.get("photo", []) + t.get("required_photos", []):
                p_st = p.get("status", "not_uploaded")
                if p_st == "pending_review":
                    pending_approvals += 1
                    pending_photos += 1
                elif p_st == "rejected":
                    rejected_photos += 1
                    pending_photos += 1
                elif p_st == "not_uploaded":
                    pending_photos += 1
        for p in r.get("required_photos", []):
            p_st = p.get("status", "not_uploaded")
            if p_st == "pending_review":
                pending_approvals += 1
            elif p_st == "rejected":
                rejected_photos += 1

    if uncompleted_tasks > 0:
        blocker_reasons.append(f"{uncompleted_tasks} task(s) uncompleted")
    if pending_approvals > 0:
        blocker_reasons.append(f"{pending_approvals} photo(s) pending approval")
    if rejected_photos > 0:
        blocker_reasons.append(f"{rejected_photos} photo(s) rejected")

    checkout_blocked_reason = ", ".join(blocker_reasons) if blocker_reasons else None
    can_checkout = (uncompleted_tasks == 0 and pending_approvals == 0 and rejected_photos == 0)

    return ClientLiveStatusResponse(
        shift_id=shift_id,
        date=str(date_val),
        shift_date=str(date_val),
        status_label=status_lbl,
        active_room_location_text=active_location_text,
        overall_progress_percentage=overall_pct,
        est_completion_time=f"Est. completion: {end_time_str}",
        assigned_cleaner=assigned_cleaner,
        current_location=current_location,
        arrival_time_info=arrival_time_info,
        current_active_room=active_room_progress,
        all_rooms_progress=all_rooms_progress,
        active_sessions=active_sessions or [],
        pending_approval_count=pending_approvals,
        pending_photos_count=pending_photos,
        rejected_photos_count=rejected_photos,
        uncompleted_tasks_count=uncompleted_tasks,
        checkout_blocked_reason=checkout_blocked_reason,
        can_checkout=can_checkout
    )


async def _resolve_client_id_aliases(current_user: UserInDB, db) -> List[str]:
    client_ids = set()
    cid = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "")
    if cid:
        client_ids.add(cid)

    or_conds = []
    if getattr(current_user, "company_name", None):
        or_conds.append({"company_name": current_user.company_name})
    if getattr(current_user, "email", None):
        or_conds.append({"email": current_user.email})

    if or_conds:
        client_cursor = db["client_list"].find({"$or": or_conds})
        async for c in client_cursor:
            if "_id" in c:
                client_ids.add(str(c["_id"]))
            if "id" in c and c["id"]:
                client_ids.add(str(c["id"]))

    return list(client_ids)


@router.get(
    "",
    response_model=ClientLiveStatusResponse,
    summary="Get Active Client Live Status Dashboard",
    description="Returns the real-time live cleaning session progress for the authenticated client's active or most recent shift. Calculates real-time task progress percentage, assigned cleaner details, arrival time, and room checklist items completed by the worker."
)
async def get_client_live_status_dashboard(
    shift_id: Optional[str] = None,
    client_tz: ZoneInfo = Depends(get_request_timezone),
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Live Status Management Endpoint.
    Polls active cleaning shift progress, assigned cleaner info, arrival timestamp, and task checklist completion.
    """
    from app.api.worker_shift_utils import resolve_shift_execution, is_plan_active_on_date, get_or_create_shift_execution
    db = get_database()
    client_aliases = await _resolve_client_id_aliases(current_user, db)
    today_str = get_today_str(client_tz)

    shift_doc = None
    if shift_id:
        shift_doc, _ = await resolve_shift_execution(shift_id, db)
    else:
        # 1. Search shift_executions for IN_PROGRESS / RUNNING sessions on today_str
        shift_doc = await db["shift_executions"].find_one({
            "client_id": {"$in": client_aliases},
            "date": today_str,
            "status": {"$in": ["in_progress", "running", "photo_submitted"]}
        }, sort=[("updated_at", -1)])

        # 2. If none, search shift_executions for SCHEDULED / ASSIGNED sessions on today_str
        if not shift_doc:
            shift_doc = await db["shift_executions"].find_one({
                "client_id": {"$in": client_aliases},
                "date": today_str,
                "status": {"$in": ["scheduled", "assigned", "draft"]}
            }, sort=[("start_time", 1)])

        # 3. Check active cleaning plans for today_str
        if not shift_doc:
            cursor_p = db["cleaning_plans"].find({
                "$or": [
                    {"client_id": {"$in": client_aliases}},
                    {"client_ids": {"$in": client_aliases}}
                ],
                "status": {"$ne": "cancelled"}
            })
            plans = await cursor_p.to_list(length=50)
            for p in plans:
                if is_plan_active_on_date(p, today_str):
                    shift_doc = await get_or_create_shift_execution(p, today_str, db)
                    break

        # 4. Fallback to any recent in-progress execution
        if not shift_doc:
            shift_doc = await db["shift_executions"].find_one({
                "client_id": {"$in": client_aliases},
                "status": {"$in": ["in_progress", "running", "photo_submitted"]}
            }, sort=[("updated_at", -1)])

        # 5. Fallback to any recent or upcoming execution or shift
        if not shift_doc:
            shift_doc = await db["shift_executions"].find_one(
                {"client_id": {"$in": client_aliases}, "status": {"$ne": "cancelled"}},
                sort=[("date", -1), ("start_time", 1)]
            )
        if not shift_doc:
            shift_doc = await db["shifts"].find_one(
                {"client_id": {"$in": client_aliases}, "status": {"$ne": "cancelled"}},
                sort=[("date", -1), ("created_at", -1)]
            )

    if not shift_doc:
        raise HTTPException(status_code=404, detail="No active or recent cleaning session found for client")

    loc_address = None
    loc_id = shift_doc.get("location_id")
    if loc_id:
        l_query = {"$or": [{"_id": ObjectId(loc_id)}, {"id": loc_id}]} if ObjectId.is_valid(loc_id) else {"$or": [{"_id": loc_id}, {"id": loc_id}]}
        loc_doc = await db["locations"].find_one(l_query)
        if loc_doc:
            loc_address = loc_doc.get("address")

    cleaner_doc = None
    workers = shift_doc.get("assigned_workers", []) or shift_doc.get("workers", [])
    if workers and isinstance(workers, list):
        w0 = workers[0]
        if isinstance(w0, dict):
            w_id = str(w0.get("worker_id") or w0.get("id") or "")
            if w_id:
                w_queries = [{"_id": w_id}, {"id": w_id}]
                if ObjectId.is_valid(w_id):
                    w_queries.append({"_id": ObjectId(w_id)})
                cleaner_doc = await db["users"].find_one({"$or": w_queries})

    # Build active_sessions for all today's sessions for this client
    from app.api.worker_shift_utils import calculate_cleaning_plan_progress
    active_sessions = []
    seen_session_ids = set()

    active_cursor = db["shift_executions"].find({
        "client_id": {"$in": client_aliases},
        "date": today_str,
        "status": {"$ne": "cancelled"}
    }).sort("start_time", 1)
    todays_execs = await active_cursor.to_list(length=20)
    for ex in todays_execs:
        ex_id = str(ex.get("id") or ex.get("_id"))
        if ex_id not in seen_session_ids:
            seen_session_ids.add(ex_id)
            ex_prog = calculate_cleaning_plan_progress(ex)
            r_name = ex.get("rooms", [{}])[0].get("room_name") if ex.get("rooms") else None
            ex_date = ex.get("date") or today_str
            active_sessions.append(ClientLiveStatusSessionSummary(
                shift_id=ex_id,
                date=ex_date,
                shift_date=ex_date,
                location_name=ex.get("location_name") or "Location",
                room_name=r_name,
                status=str(ex.get("status", "scheduled")),
                overall_progress_percentage=ex_prog["overall_progress_percentage"],
                start_time=str(ex.get("start_time", "08:00 AM")),
                end_time=str(ex.get("end_time", "12:00 PM"))
            ))

    return _build_client_live_status(
        shift_doc,
        loc_address=loc_address,
        cleaner_doc=cleaner_doc,
        active_sessions=active_sessions
    )


@router.get(
    "/shifts",
    response_model=ClientLiveShiftPaginatedResponse,
    summary="List Client Cleaning Sessions for Live Status Selection (Paginated)",
    description="Lists all historical and active cleaning shifts for the logged-in client with pagination (page, limit) to select for live status polling."
)
async def list_client_live_shifts(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    List Client Cleaning Sessions Endpoint (Paginated).
    Returns paginated cleaning shifts assigned to the current client.
    """
    from app.api.worker_shift_utils import calculate_cleaning_plan_progress
    db = get_database()
    client_aliases = await _resolve_client_id_aliases(current_user, db)

    # 1. Fetch from shift_executions and shifts concurrently with projection
    shift_proj = {
        "rooms.tasks.photo": 0,
        "rooms.required_photos": 0,
        "rooms.tasks.description": 0
    }
    fetch_limit = max(limit, min(30, page * limit + 10))
    exec_task = db["shift_executions"].find(
        {"client_id": {"$in": client_aliases}},
        projection=shift_proj
    ).sort("created_at", -1).limit(fetch_limit).to_list(length=fetch_limit)

    shift_task = db["shifts"].find(
        {"client_id": {"$in": client_aliases}},
        projection=shift_proj
    ).sort("created_at", -1).limit(fetch_limit).to_list(length=fetch_limit)

    exec_shifts, direct_shifts = await asyncio.gather(exec_task, shift_task)

    combined_map = {}
    for s in exec_shifts + direct_shifts:
        sid = str(s.get("id") or s.get("_id"))
        if sid not in combined_map:
            s["id"] = sid
            prog = calculate_cleaning_plan_progress(s)
            s["overall_progress_percentage"] = prog["overall_progress_percentage"]
            s["completed_rooms_count"] = prog["completed_rooms_count"]
            s["in_progress_rooms_count"] = prog["in_progress_rooms_count"]
            s["pending_rooms_count"] = prog["pending_rooms_count"]
            combined_map[sid] = s

    all_items = list(combined_map.values())
    total_count = len(all_items)
    skip = (page - 1) * limit
    paged_items = all_items[skip:skip + limit]

    res = []
    for item in paged_items:
        try:
            res.append(ShiftResponse(**item))
        except Exception:
            pass

    return ClientLiveShiftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        shifts=res
    )


@router.get(
    "/shifts/{shift_id}",
    response_model=ClientLiveStatusResponse,
    summary="Get Live Status for Specific Shift",
    description="Returns real-time live status and task completion progress for a specific shift ID."
)
async def get_client_live_status_by_shift(
    shift_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Get Live Status By Shift ID Endpoint.
    Returns real-time room task completion and status for the given shift ID.
    """
    return await get_client_live_status_dashboard(shift_id=shift_id, current_user=current_user)


