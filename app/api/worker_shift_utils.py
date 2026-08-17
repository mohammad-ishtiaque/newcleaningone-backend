from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from bson import ObjectId
import re

def parse_time_to_minutes(time_str: str) -> int:
    """Parses '08:00 AM', '02:30 PM', or '14:00' to minutes from midnight."""
    if not time_str:
        return 480  # Default 8:00 AM
    clean_str = time_str.strip().upper()
    try:
        match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", clean_str)
        if match:
            h, m, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
            if meridiem:
                if meridiem == "PM" and h < 12:
                    h += 12
                elif meridiem == "AM" and h == 12:
                    h = 0
            return h * 60 + m
        parts = clean_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 480


def parse_plan_start_datetime(date_str: str, time_str: str) -> datetime:
    """Returns datetime object for shift start in UTC."""
    try:
        y, mon, d = map(int, date_str.strip().split("-"))
        total_mins = parse_time_to_minutes(time_str)
        hour = total_mins // 60
        minute = total_mins % 60
        return datetime(y, mon, d, hour, minute, tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def is_plan_active_on_date(plan_doc: dict, target_date_str: str) -> bool:
    """
    Checks if a cleaning plan is active on target_date_str.
    Supports one-time plans (date == target_date_str) and recurring plans
    (date <= target_date_str <= repeat_until and target day of week in working_days).
    """
    if not plan_doc.get("is_active", True) or plan_doc.get("status") == "cancelled":
        return False

    plan_date = str(plan_doc.get("date", "")).strip()
    if not plan_date:
        return False

    repeat_type = str(plan_doc.get("repeat_shift") or "does_not_repeat").strip().lower()

    if repeat_type in ["does_not_repeat", "none", "once", ""]:
        return plan_date == target_date_str

    # Recurring plan check
    try:
        p_dt = datetime.strptime(plan_date, "%Y-%m-%d").date()
        t_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()

        if t_dt < p_dt:
            return False

        repeat_until = plan_doc.get("repeat_until")
        if repeat_until:
            u_dt = datetime.strptime(str(repeat_until).strip(), "%Y-%m-%d").date()
            if t_dt > u_dt:
                return False

        # Check working days
        day_name = t_dt.strftime("%A").lower()  # e.g. "monday"
        working_days = [str(d).strip().lower() for d in plan_doc.get("working_days", [])]

        if not working_days or repeat_type == "everyday":
            return True

        return day_name in working_days
    except Exception:
        return plan_date == target_date_str


def evaluate_worker_attendance_status(
    plan_doc: dict,
    worker_record: Optional[dict] = None,
    now_utc: Optional[datetime] = None,
    target_date_str: Optional[str] = None
) -> str:
    """
    Evaluates attendance status with 15-minute grace period:
    - ontime: checkin_time <= start_time + 15 mins
    - late: checkin_time > start_time + 15 mins
    - missing: no checkin and now > start_time + 15 mins on that date
    - scheduled: no checkin and now <= start_time + 15 mins
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    plan_date = target_date_str or plan_doc.get("date") or now_utc.strftime("%Y-%m-%d")
    start_time_str = plan_doc.get("start_time", "08:00 AM")
    start_dt = parse_plan_start_datetime(plan_date, start_time_str)
    grace_cutoff_dt = start_dt + timedelta(minutes=15)

    c_time = worker_record.get("checkin_time") if worker_record else None
    if c_time:
        if isinstance(c_time, str):
            try:
                c_time = datetime.fromisoformat(c_time)
            except Exception:
                c_time = now_utc
        if c_time.tzinfo is None:
            c_time = c_time.replace(tzinfo=timezone.utc)

        # Worker has checked in
        if c_time <= grace_cutoff_dt:
            return "ontime"
        else:
            return "late"
    else:
        # Worker has not checked in yet
        if now_utc > grace_cutoff_dt:
            return "missing"
        else:
            return "scheduled"


def evaluate_auto_checkout_and_hours(
    plan_doc: dict,
    worker_record: Optional[dict] = None,
    now_utc: Optional[datetime] = None,
    target_date_str: Optional[str] = None
) -> Tuple[Optional[datetime], float, str, bool]:
    """
    Evaluates worker hours worked and handles the edge case where a worker forgets to check out:
    - If checked in and checked out: returns (checkout_time, hours_worked, "{hours}h worked", False)
    - If checked in but not checked out:
        - If within shift window (+ 2hr grace): returns (None, elapsed_hours, "{elapsed}h in progress", False)
        - If shift window has passed: auto-calculates checkout at scheduled end time (or max scheduled duration),
          returns (auto_checkout_dt, hours_worked, "{hours}h (Auto)", True)
    - If not checked in: returns (None, 0.0, "Not started", False)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    if not worker_record:
        return None, 0.0, "Not started", False

    c_time = worker_record.get("checkin_time")
    co_time = worker_record.get("checkout_time")

    if isinstance(c_time, str):
        try:
            c_time = datetime.fromisoformat(c_time)
        except Exception:
            pass
    if isinstance(co_time, str):
        try:
            co_time = datetime.fromisoformat(co_time)
        except Exception:
            pass

    if c_time and hasattr(c_time, "tzinfo") and c_time.tzinfo is None:
        c_time = c_time.replace(tzinfo=timezone.utc)
    if co_time and hasattr(co_time, "tzinfo") and co_time.tzinfo is None:
        co_time = co_time.replace(tzinfo=timezone.utc)

    # 1. Not checked in
    if not c_time:
        return None, 0.0, "Not started", False

    # 2. Worker already checked out
    if co_time:
        duration_secs = max(0.0, (co_time - c_time).total_seconds())
        hours_num = round(duration_secs / 3600.0, 2)
        is_auto = worker_record.get("is_auto_checked_out", False)
        disp = f"{hours_num}h (Auto)" if is_auto else f"{hours_num}h worked"
        return co_time, hours_num, disp, is_auto

    # 3. Checked in but has NOT checked out yet
    plan_date = target_date_str or plan_doc.get("date") or now_utc.strftime("%Y-%m-%d")
    end_time_str = plan_doc.get("end_time", "04:00 PM")
    start_time_str = plan_doc.get("start_time", "08:00 AM")

    start_dt = parse_plan_start_datetime(plan_date, start_time_str)
    end_dt = parse_plan_start_datetime(plan_date, end_time_str)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=plan_doc.get("duration_minutes", 480))

    auto_checkout_cutoff = end_dt + timedelta(hours=2)

    if now_utc > auto_checkout_cutoff:
        # Shift has ended long ago -> Auto Checkout worker at scheduled end time
        duration_secs = max(0.0, (end_dt - c_time).total_seconds())
        hours_num = round(max(0.1, duration_secs / 3600.0), 2)
        return end_dt, hours_num, f"{hours_num}h (Auto)", True
    else:
        # Shift is ongoing -> Live elapsed hours
        duration_secs = max(0.0, (now_utc - c_time).total_seconds())
        hours_num = round(duration_secs / 3600.0, 1)
        return None, hours_num, f"{hours_num}h in progress", False


def calculate_cleaning_plan_progress(plan_doc: dict, approved_photos_count: Optional[int] = None) -> dict:
    """
    Calculates overall shift progress using weighted item-based formula:
    Progress % = round(((completed_tasks + approved_photos) / (total_tasks + total_photos)) * 100, 1)
    """
    rooms = plan_doc.get("rooms", [])
    total_rooms = len(rooms)
    completed_rooms = 0
    in_progress_rooms = 0
    pending_rooms = 0

    total_tasks = 0
    completed_tasks = 0
    total_photos = 0

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

        r_photos = r.get("required_photos", [])
        total_photos += len(r_photos)

    # Add additional tasks & photos if any
    add_tasks = plan_doc.get("additional_tasks", [])
    total_tasks += len(add_tasks)
    completed_tasks += sum(1 for t in add_tasks if t.get("is_completed"))

    add_photos = plan_doc.get("additional_required_photos", [])
    total_photos += len(add_photos)

    # Fallback to stored total counts if rooms checklist is not expanded
    if total_tasks == 0 and plan_doc.get("total_tasks_count", 0) > 0:
        total_tasks = plan_doc.get("total_tasks_count", 0)
        completed_tasks = plan_doc.get("completed_tasks_count", 0)

    if total_photos == 0 and plan_doc.get("total_photos_count", 0) > 0:
        total_photos = plan_doc.get("total_photos_count", 0)

    approved_photos = approved_photos_count if approved_photos_count is not None else plan_doc.get("approved_photos_count", 0)

    total_items = total_tasks + total_photos
    completed_items = completed_tasks + approved_photos

    if total_items > 0:
        overall_progress = round(min(100.0, (completed_items / total_items) * 100.0), 1)
    elif plan_doc.get("status") == "completed":
        overall_progress = 100.0
    else:
        overall_progress = 0.0

    return {
        "overall_progress_percentage": overall_progress,
        "total_rooms_count": total_rooms,
        "completed_rooms_count": completed_rooms,
        "in_progress_rooms_count": in_progress_rooms,
        "pending_rooms_count": pending_rooms,
        "total_tasks_count": total_tasks,
        "completed_tasks_count": completed_tasks,
        "total_photos_count": total_photos,
        "approved_photos_count": approved_photos
    }


async def get_or_create_shift_execution(plan_doc: dict, target_date_str: str, db) -> dict:
    """
    Retrieves or initializes a dedicated daily shift execution document in `shift_executions`.
    This maintains completely separate daily tracking for recurring cleaning plans.
    """
    plan_id = str(plan_doc.get("id") or plan_doc.get("_id"))
    execution_id = f"exec_{plan_id}_{target_date_str}"

    exec_doc = await db["shift_executions"].find_one({
        "$or": [{"_id": execution_id}, {"id": execution_id}, {"plan_id": plan_id, "date": target_date_str}]
    })
    if exec_doc:
        return exec_doc

    # Resolve rooms checklist from rooms collection
    room_ids = plan_doc.get("room_ids", [])
    raw_rooms = []
    if room_ids:
        cursor = db["rooms"].find({"$or": [{"_id": {"$in": room_ids}}, {"id": {"$in": room_ids}}, {"room_id": {"$in": room_ids}}]})
        raw_rooms = await cursor.to_list(length=len(room_ids) * 2 + 10)

    room_map = {}
    for r in raw_rooms:
        for k in (r.get("_id"), r.get("id"), r.get("room_id")):
            if k:
                room_map[str(k)] = r

    exec_rooms = []
    total_tasks_cnt = 0
    total_photos_cnt = 0

    for rid in room_ids:
        r = room_map.get(str(rid))
        if not r:
            continue
        r_tasks = [
            {
                "id": str(t.get("id") or f"task_{idx+1}"),
                "name": t.get("name") or t.get("task_name", "Task"),
                "is_completed": False,
                "completed_at": None
            }
            for idx, t in enumerate(r.get("tasks", []))
        ]
        r_photos = [
            {
                "id": str(p.get("id") or f"photo_{idx+1}"),
                "name": p.get("name") or p.get("photo_name", "Photo"),
                "photo_type": p.get("photo_type", "after")
            }
            for idx, p in enumerate(r.get("required_photos", []))
        ]
        total_tasks_cnt += len(r_tasks)
        total_photos_cnt += len(r_photos)

        exec_rooms.append({
            "room_id": str(rid),
            "room_name": r.get("room_name") or r.get("name", "Room"),
            "floor": r.get("floor", 1),
            "status": "pending",
            "tasks": r_tasks,
            "required_photos": r_photos,
            "submitted_photos": []
        })

    # Prepare workers list
    workers_list = []
    for aw in plan_doc.get("assigned_workers", []):
        if isinstance(aw, dict) and aw.get("worker_id"):
            workers_list.append({
                "worker_id": str(aw["worker_id"]),
                "position": aw.get("position", "normal"),
                "checkin_time": None,
                "checkout_time": None,
                "status": None,
                "hours_worked": None
            })
    if not workers_list:
        for wid in plan_doc.get("worker_ids", []):
            if str(wid).strip():
                workers_list.append({
                    "worker_id": str(wid).strip(),
                    "position": "normal",
                    "checkin_time": None,
                    "checkout_time": None,
                    "status": None,
                    "hours_worked": None
                })

    new_exec = {
        "_id": execution_id,
        "id": execution_id,
        "plan_id": plan_id,
        "title": plan_doc.get("title") or plan_doc.get("plan_name", "Cleaning Shift"),
        "date": target_date_str,
        "start_time": plan_doc.get("start_time", "08:00 AM"),
        "end_time": plan_doc.get("end_time", "04:00 PM"),
        "duration_minutes": plan_doc.get("duration_minutes", 60),
        "client_id": plan_doc.get("client_id") or (plan_doc.get("client_ids", [""])[0] if plan_doc.get("client_ids") else ""),
        "client_name": plan_doc.get("company_name") or plan_doc.get("client_name") or (plan_doc.get("client_names", [""])[0] if plan_doc.get("client_names") else "Client"),
        "location_id": plan_doc.get("location_id", ""),
        "location_name": plan_doc.get("location_name", ""),
        "assigned_workers": workers_list,
        "rooms": exec_rooms,
        "additional_tasks": plan_doc.get("additional_tasks", []),
        "additional_required_photos": plan_doc.get("additional_required_photos", []),
        "total_tasks_count": total_tasks_cnt or plan_doc.get("total_tasks_count", 0),
        "completed_tasks_count": 0,
        "total_photos_count": total_photos_cnt or plan_doc.get("total_photos_count", 0),
        "approved_photos_count": 0,
        "overall_progress_percentage": 0.0,
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }

    try:
        await db["shift_executions"].insert_one(new_exec)
    except Exception:
        # Document might have been created concurrently
        exec_doc = await db["shift_executions"].find_one({"$or": [{"_id": execution_id}, {"id": execution_id}]})
        if exec_doc:
            return exec_doc

    return new_exec


async def resolve_shift_execution(shift_id: str, db) -> Tuple[Optional[dict], Optional[str]]:
    """
    Resolves a shift execution document and collection name from any valid identifier:
    - Shift Execution ID (e.g. exec_plan_..._2026-08-17)
    - Cleaning Plan ID (e.g. plan_...)
    - Legacy Shift ID
    """
    if not shift_id:
        return None, None

    # 1. First check shift_executions by ID or _id
    exec_doc = await db["shift_executions"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if exec_doc:
        return exec_doc, "shift_executions"

    # 2. Check if shift_id is a cleaning plan ID
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if plan_doc:
        exec_doc = await get_or_create_shift_execution(plan_doc, today_str, db)
        return exec_doc, "shift_executions"

    # 3. Check if plan_id matches any execution on today's date
    exec_doc = await db["shift_executions"].find_one({"plan_id": shift_id, "date": today_str})
    if exec_doc:
        return exec_doc, "shift_executions"

    # 4. Fallback to shifts collection
    legacy_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if legacy_doc:
        return legacy_doc, "shifts"

    return None, None
