from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union
from bson import ObjectId
import re
from zoneinfo import ZoneInfo
from app.core.timezone_utils import (
    parse_time_to_minutes, parse_plan_start_datetime,
    get_timezone, now_in_tz, get_today_str, human_time_until
)


def is_plan_active_on_date(plan_doc: dict, target_date_str: str) -> bool:
    """
    Checks if a cleaning plan is active on target_date_str.
    Supports one-time plans (date == target_date_str) and recurring plans
    (date <= target_date_str <= repeat_until and target day of week in working_days).

    Recurrence is driven entirely by `working_days` — a plan with no working_days
    set is treated as one-time (active only on its own `date`); a plan with
    working_days set recurs on those weekdays through `repeat_until`. The
    `repeat_shift` field is NOT used here (kept on the document only for display;
    it never determines aggregation/recurrence).
    """
    if not plan_doc.get("is_active", True) or plan_doc.get("status") == "cancelled":
        return False

    plan_date = str(plan_doc.get("date", "")).strip()
    if not plan_date:
        return False

    working_days = [str(d).strip().lower() for d in (plan_doc.get("working_days") or [])]

    if not working_days:
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

        if plan_date == target_date_str:
            return True

        # Check working days
        day_name = t_dt.strftime("%A").lower()  # e.g. "sunday"
        day_abbr = t_dt.strftime("%a").lower()  # e.g. "sun"

        return any(
            d in [day_name, day_abbr] or d.startswith(day_abbr) or day_name.startswith(d)
            for d in working_days
        )
    except Exception:
        return plan_date == target_date_str


def is_plan_in_date_range(plan_doc: dict, target_date_str: str) -> bool:
    """
    Roster-specific plan gate: is this date inside the plan's own [date, repeat_until]
    window? Unlike is_plan_active_on_date() above, this ignores working_days/repeat_shift
    entirely — the plan only defines *how long* it runs (its date range); which specific
    days within that range actually carry tasks is decided per-task, by matching each
    room task's own frequency_type/weekly_days/monthly_dates/fixed_date against the date
    (see _task_applies_on_date in app/api/admin/roster.py), not by the plan itself.

    Used by every worker/manager roster & shift-list view so they all agree on the same
    rule; app-wide plan-active checks elsewhere (client views, dashboards) still use
    is_plan_active_on_date() above and are unaffected by this.
    """
    if not plan_doc.get("is_active", True) or plan_doc.get("status") == "cancelled":
        return False

    plan_date = str(plan_doc.get("date", "")).strip()
    if not plan_date:
        return False

    try:
        p_dt = datetime.strptime(plan_date, "%Y-%m-%d").date()
        t_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:
        return plan_date == target_date_str

    if t_dt < p_dt:
        return False

    repeat_until = plan_doc.get("repeat_until")
    if repeat_until:
        try:
            u_dt = datetime.strptime(str(repeat_until).strip(), "%Y-%m-%d").date()
            return t_dt <= u_dt
        except Exception:
            return t_dt == p_dt
    # No repeat_until -> single-day plan, active only on its own date.
    return t_dt == p_dt


def evaluate_worker_attendance_status(
    plan_doc: dict,
    worker_record: Optional[dict] = None,
    now_utc: Optional[datetime] = None,
    target_date_str: Optional[str] = None
) -> str:
    """
    Evaluates attendance status with 15-minute grace period:
    - ontime: checkin_time <= start_time + 15 mins
    - late: checkin_time > start_time + 15 mins, OR (no checkin yet, but now > start_time + 15 mins while shift is ongoing)
    - missing: no checkin and shift has ended (or past date)
    - scheduled: no checkin and now <= start_time + 15 mins
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    plan_tz = plan_doc.get("timezone") or "Europe/Amsterdam"
    plan_date = target_date_str or plan_doc.get("date") or now_in_tz(plan_tz).strftime("%Y-%m-%d")
    start_time_str = plan_doc.get("start_time", "08:00 AM")
    start_dt = parse_plan_start_datetime(plan_date, start_time_str, tz=plan_tz)
    grace_cutoff_dt = start_dt + timedelta(minutes=15)

    end_time_str = plan_doc.get("end_time")
    dur_mins = int(plan_doc.get("duration_minutes") or 60)
    if end_time_str:
        try:
            parsed_end = parse_plan_start_datetime(plan_date, end_time_str, tz=plan_tz)
            if parsed_end > start_dt:
                end_dt = parsed_end
            else:
                end_dt = start_dt + timedelta(minutes=dur_mins)
        except Exception:
            end_dt = start_dt + timedelta(minutes=dur_mins)
    else:
        end_dt = start_dt + timedelta(minutes=dur_mins)

    today_in_plan_tz = now_in_tz(plan_tz).strftime("%Y-%m-%d")
    now_in_plan_zone = now_utc.astimezone(get_timezone(plan_tz))

    c_time = worker_record.get("checkin_time") if worker_record else None
    if c_time:
        if isinstance(c_time, str):
            try:
                c_time = datetime.fromisoformat(c_time)
            except Exception:
                c_time = now_utc
        if c_time.tzinfo is None:
            c_time = c_time.replace(tzinfo=timezone.utc)
        c_time_in_zone = c_time.astimezone(get_timezone(plan_tz))

        # Worker has checked in
        if c_time_in_zone <= grace_cutoff_dt:
            return "ontime"
        else:
            return "late"
    else:
        # Worker has not checked in yet
        if now_in_plan_zone > grace_cutoff_dt:
            if plan_date < today_in_plan_tz or now_in_plan_zone > end_dt:
                return "missing"
            else:
                return "late"
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
        is_done = bool(r.get("is_completed") or r_status in ["completed", "approved"])
        if is_done:
            completed_rooms += 1
        elif r_status in ["in_progress", "photo_submitted"] or len(r.get("submitted_photos", [])) > 0:
            in_progress_rooms += 1
        else:
            pending_rooms += 1

        r_tasks = r.get("tasks", [])
        total_tasks += len(r_tasks)
        completed_tasks += sum(1 for t in r_tasks if t.get("is_completed"))

        r_photos = r.get("required_photos", [])
        if r_photos:
            total_photos += len(r_photos)
        else:
            total_photos += sum(len(t.get("photo", [])) for t in r_tasks)

    # Add additional tasks & photos if any
    add_tasks = plan_doc.get("additional_tasks", []) or plan_doc.get("tasks", [])
    total_tasks += len(add_tasks)
    completed_tasks += sum(1 for t in add_tasks if t.get("is_completed"))

    add_photos = plan_doc.get("additional_required_photos", [])
    if add_photos:
        total_photos += len(add_photos)
    else:
        total_photos += sum(len(t.get("photo", [])) for t in add_tasks)

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


def is_task_due_on_date(task: dict, target_date_str: str) -> bool:
    """
    Checks whether a task is due on the target date, using the same rule as the
    manager web roster (app/api/admin/roster.py's _task_applies_on_date): matched
    against the task's own weekly_days/monthly_dates/fixed_date fields, not a
    generic days-since-last-completion guess. Kept in this shared module so both
    the manager roster and every worker-facing endpoint that materializes a shift
    execution (roster, shifts list, checklist) apply the identical rule.

    - every_visit (or unset): always due
    - weekly: target date's weekday must be in task.weekly_days
    - monthly: target date's day-of-month must be in task.monthly_dates
    - fixed_date: target date must equal task.fixed_date exactly
    """
    freq = str(task.get("frequency_type") or "every_visit").strip().lower()
    try:
        t_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:
        return freq in ["every_visit", "daily", "always", ""]

    if freq in ["every_visit", "daily", "always", ""]:
        return True
    elif freq == "weekly":
        day_name = t_dt.strftime("%A").lower()
        day_abbr = t_dt.strftime("%a").lower()
        days = [str(d).strip().lower() for d in (task.get("weekly_days") or [])]
        return any(d in [day_name, day_abbr] or d.startswith(day_abbr) or day_name.startswith(d) for d in days)
    elif freq == "monthly":
        dates = [int(d) for d in (task.get("monthly_dates") or []) if str(d).strip().lstrip("-").isdigit()]
        return t_dt.day in dates
    elif freq == "fixed_date":
        return str(task.get("fixed_date") or "").strip() == target_date_str
    return False


async def get_or_create_shift_execution(plan_doc: dict, target_date_str: str, db) -> dict:
    """
    Retrieves or initializes a dedicated daily shift execution document in `shift_executions`.
    Maintains separate daily tracking and filters tasks by their due frequency for today.
    """
    plan_id = str(plan_doc.get("id") or plan_doc.get("_id"))
    execution_id = f"exec_{plan_id}_{target_date_str}"

    exec_doc = await db["shift_executions"].find_one({
        "$or": [{"_id": execution_id}, {"id": execution_id}, {"plan_id": plan_id, "date": target_date_str}]
    })
    if exec_doc:
        # Sync latest assigned workers from plan_doc if any were newly added
        plan_workers = (plan_doc.get("assigned_workers") or []) + (plan_doc.get("workers") or [])
        plan_w_ids = plan_doc.get("worker_ids") or []

        exec_w_map = {}
        for w in (exec_doc.get("assigned_workers") or exec_doc.get("workers") or []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id") or "")
                if wid:
                    exec_w_map[wid] = w

        needs_update = False
        for pw in plan_workers:
            if isinstance(pw, dict):
                pw_id = str(pw.get("worker_id") or pw.get("id") or "").strip()
                if pw_id and pw_id not in exec_w_map:
                    exec_w_map[pw_id] = {
                        "worker_id": pw_id,
                        "position": pw.get("position", "normal"),
                        "checkin_time": None,
                        "checkout_time": None,
                        "status": "scheduled",
                        "hours_worked": None
                    }
                    needs_update = True
            elif isinstance(pw, str) and pw.strip():
                pw_id = pw.strip()
                if pw_id and pw_id not in exec_w_map:
                    exec_w_map[pw_id] = {
                        "worker_id": pw_id,
                        "position": "normal",
                        "checkin_time": None,
                        "checkout_time": None,
                        "status": "scheduled",
                        "hours_worked": None
                    }
                    needs_update = True

        for pw_id in plan_w_ids:
            pw_id_str = str(pw_id).strip()
            if pw_id_str and pw_id_str not in exec_w_map:
                exec_w_map[pw_id_str] = {
                    "worker_id": pw_id_str,
                    "position": "normal",
                    "checkin_time": None,
                    "checkout_time": None,
                    "status": "scheduled",
                    "hours_worked": None
                }
                needs_update = True

        if needs_update:
            updated_workers_list = list(exec_w_map.values())
            updated_w_ids = list(exec_w_map.keys())
            exec_doc["assigned_workers"] = updated_workers_list
            exec_doc["workers"] = updated_workers_list
            exec_doc["worker_ids"] = updated_w_ids
            await db["shift_executions"].update_one(
                {"_id": exec_doc["_id"]},
                {"$set": {
                    "assigned_workers": updated_workers_list,
                    "workers": updated_workers_list,
                    "worker_ids": updated_w_ids,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
        return exec_doc

    # Resolve rooms checklist from rooms collection
    embedded_rooms = plan_doc.get("rooms", [])
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

    candidate_rooms = embedded_rooms if (embedded_rooms and isinstance(embedded_rooms, list)) else []
    if not candidate_rooms and room_ids:
        for rid in room_ids:
            r = room_map.get(str(rid))
            if r:
                candidate_rooms.append(r)

    seen_room_ids = set()
    unique_candidate_rooms = []
    for r in candidate_rooms:
        orig_rid = str(r.get("room_id") or r.get("id") or r.get("_id") or "")
        if orig_rid and orig_rid in seen_room_ids:
            continue
        if orig_rid:
            seen_room_ids.add(orig_rid)
        unique_candidate_rooms.append(r)

    for idx, r in enumerate(unique_candidate_rooms):
        rid = str(r.get("room_id") or r.get("id") or r.get("_id") or f"room_{idx+1}")
        raw_tasks = r.get("tasks", [])
        due_tasks = []

        for t_idx, t in enumerate(raw_tasks):
            t_id = str(t.get("id") or f"task_{t_idx+1}")

            if not is_task_due_on_date(t, target_date_str):
                continue  # Task is not due today based on frequency

            task_photos = t.get("photo") or []
            is_photo_mandatory = bool(t.get("is_photo_req") or len(task_photos) > 0)
            req_photos_num = len(task_photos) or t.get("total_photos_required", 0)

            due_tasks.append({
                "id": t_id,
                "name": t.get("name") or t.get("task_name", "Task"),
                "frequency_type": t.get("frequency_type", "every_visit"),
                "is_photo_req": is_photo_mandatory,
                "total_photos_required": req_photos_num,
                "photo": task_photos,
                "submitted_photos": [],
                "is_completed": False,
                "completed_at": None
            })

        r_photos = [
            {
                "id": str(p.get("id") or f"photo_{p_idx+1}"),
                "name": p.get("name") or p.get("photo_name", "Photo"),
                "photo_type": p.get("photo_type", "after"),
                "frequency_type": p.get("frequency_type", "every_visit")
            }
            for p_idx, p in enumerate(r.get("required_photos", []) or r.get("photo_requirements", []))
        ]

        total_tasks_cnt += len(due_tasks)
        total_photos_cnt += len(r_photos)

        exec_rooms.append({
            "id": str(rid),
            "room_id": str(rid),
            "room_name": r.get("room_name") or r.get("name", "Room"),
            "floor": r.get("floor", 1),
            "status": "pending",
            "is_completed": False,
            "tasks": due_tasks,
            "required_photos": r_photos,
            "submitted_photos": []
        })

    # Prepare workers list across all possible field representations
    workers_list = []
    seen_worker_ids = set()
    raw_sources = ((plan_doc.get("assigned_workers") or []) + (plan_doc.get("workers") or []) + (plan_doc.get("worker_ids") or []))
    for aw in raw_sources:
        wid, pos, c_in, c_out, st, hrs = None, "normal", None, None, None, None
        if isinstance(aw, dict):
            wid = str(aw.get("worker_id") or aw.get("id") or aw.get("_id") or "").strip()
            pos, c_in, c_out, st, hrs = aw.get("position", "normal"), aw.get("checkin_time"), aw.get("checkout_time"), aw.get("status"), aw.get("hours_worked")
        elif isinstance(aw, str) and aw.strip():
            wid = aw.strip()
        if wid and wid not in seen_worker_ids:
            seen_worker_ids.add(wid)
            workers_list.append({"worker_id": wid, "position": pos, "checkin_time": c_in, "checkout_time": c_out, "status": st, "hours_worked": hrs})

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
        "workers": workers_list,
        "worker_ids": list(seen_worker_ids),
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

    # 5. Fallback to extra_services collection
    es_doc = await db["extra_services"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if es_doc:
        return es_doc, "extra_services"

    return None, None


def calculate_rounded_work_hours(duration_seconds: float) -> Tuple[float, float, float]:
    """
    Computes (rounded_hours, raw_hours, duration_minutes).
    Rounding rule (30-minute blocks):
    - Extra minutes == 0 -> exact whole hours (e.g. 2 hr 0 min -> 2.0 hr).
    - Extra minutes in [1, 30] -> rounded to next half-hour (e.g. 2 hr 1 min -> 2.5 hr, 2 hr 30 min -> 2.5 hr).
    - Extra minutes in [31, 59] -> rounded to next full hour (e.g. 2 hr 31 min -> 3.0 hr, 2 hr 45 min -> 3.0 hr).
    """
    total_minutes = max(0, int(round(duration_seconds / 60.0)))
    hours = total_minutes // 60
    rem_mins = total_minutes % 60

    if rem_mins == 0:
        rounded_hours = float(hours)
    elif rem_mins <= 30:
        rounded_hours = float(hours) + 0.5
    else:
        rounded_hours = float(hours) + 1.0

    raw_hours = round(max(0.0, duration_seconds / 3600.0), 2)
    return rounded_hours, raw_hours, float(total_minutes)


def evaluate_shift_overtime(
    rounded_hours: float,
    scheduled_duration_minutes: Optional[float] = None
) -> Tuple[float, float]:
    """
    Computes (regular_hours, overtime_hours) based on rounded hours worked vs scheduled duration.
    If scheduled duration is not specified, defaults regular hours to rounded hours (no overtime).
    """
    if scheduled_duration_minutes and scheduled_duration_minutes > 0:
        scheduled_hours = round(scheduled_duration_minutes / 60.0, 2)
        if rounded_hours > scheduled_hours:
            regular_hours = scheduled_hours
            overtime_hours = round(rounded_hours - scheduled_hours, 2)
        else:
            regular_hours = rounded_hours
            overtime_hours = 0.0
    else:
        regular_hours = rounded_hours
        overtime_hours = 0.0

    return regular_hours, overtime_hours


VALID_DAYS_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NORM_MAP = {
    "mon": "mon", "monday": "mon", "tue": "tue", "tuesday": "tue",
    "wed": "wed", "wednesday": "wed", "thu": "thu", "thursday": "thu",
    "fri": "fri", "friday": "fri", "sat": "sat", "saturday": "sat",
    "sun": "sun", "sunday": "sun"
}


def normalize_working_days(raw_days: Optional[List[str]]) -> Tuple[List[str], List[str]]:
    """Normalizes a list of working days into ordered 3-letter codes and computes off-days."""
    if not raw_days:
        raw_days = ["mon", "tue", "wed", "thu", "fri", "sat"]
    normalized_set = set()
    for d in raw_days:
        if not d:
            continue
        clean = str(d).strip().lower()
        if clean in DAY_NORM_MAP:
            normalized_set.add(DAY_NORM_MAP[clean])
        elif len(clean) >= 3 and clean[:3] in DAY_NORM_MAP:
            normalized_set.add(DAY_NORM_MAP[clean[:3]])
    if not normalized_set:
        normalized_set = {"mon", "tue", "wed", "thu", "fri", "sat"}
    working_days = [d for d in VALID_DAYS_ORDER if d in normalized_set]
    off_days = [d for d in VALID_DAYS_ORDER if d not in normalized_set]
    return working_days, off_days


def extract_date_weekday(date_val: Any) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (day_abbr, day_full) e.g. ('mon', 'Monday') from date string or datetime."""
    if not date_val:
        return None, None
    if isinstance(date_val, datetime):
        return date_val.strftime("%a").lower(), date_val.strftime("%A")
    d_str = str(date_val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(d_str, fmt)
            return dt.strftime("%a").lower(), dt.strftime("%A")
        except Exception:
            pass
    return None, None
