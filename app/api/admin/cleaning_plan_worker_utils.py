import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any, List
from bson import ObjectId


def normalize_worker_position(p_val: Optional[str]) -> str:
    """Normalizes worker position into standard values: 'teamleader', 'co_leader', or 'normal'."""
    if not p_val:
        return "normal"
    clean = str(p_val).strip().lower().replace("-", "_").replace(" ", "_")
    if clean in ["teamleader", "team_leader", "leader"]:
        return "teamleader"
    elif clean in ["co_leader", "coleader", "co"]:
        return "co_leader"
    return "normal"


def resolve_plan_working_days(
    repeat_shift: Optional[str],
    working_days: Optional[List[str]],
    frequency: Optional[List[str]],
    date_str: Optional[str] = None
) -> List[str]:
    """Resolves correct working days based on repeat_shift or explicit days."""
    # 1. If explicit working_days or frequency provided, prioritize it
    if working_days:
        cleaned = [str(d).strip().lower() for d in working_days if str(d).strip()]
        if cleaned:
            return cleaned
    if frequency:
        cleaned = [str(d).strip().lower() for d in frequency if str(d).strip()]
        if cleaned:
            return cleaned

    # 2. Normalize repeat_shift string
    norm_shift = str(repeat_shift or "standard working week").strip().lower().replace("-", "_").replace(" ", "_")

    if norm_shift in ["everyday", "every_day", "daily", "all_days", "all_day"]:
        return ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    elif norm_shift in ["standard_working_week", "standard_working_days", "weekday", "weekdays"]:
        return ["mon", "tue", "wed", "thu", "fri"]
    elif norm_shift in ["does_not_repeat", "once", "single", "none"]:
        if date_str:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(date_str.strip(), fmt)
                    return [dt.strftime("%a").lower()]
                except Exception:
                    pass
        return ["mon"]
    elif norm_shift in ["weekly", "monthly"]:
        if date_str:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(date_str.strip(), fmt)
                    return [dt.strftime("%a").lower()]
                except Exception:
                    pass
        return ["sun"]
    else:
        return ["mon", "tue", "wed", "thu", "fri"]



def parse_time_to_minutes(time_str: Optional[str]) -> int:
    """Parses 12-hour ('08:00 AM', '1:30 PM') or 24-hour ('08:00', '13:30') time to minutes from midnight."""
    if not time_str:
        return 8 * 60  # Default 8:00 AM (480 mins)
    
    s = str(time_str).strip()
    s_upper = s.upper()

    # 12-hour format
    if "AM" in s_upper or "PM" in s_upper:
        clean = re.sub(r"\s+", "", s_upper)
        for fmt in ("%I:%M%p", "%I%p", "%I:%M %p"):
            try:
                dt = datetime.strptime(clean if fmt != "%I:%M %p" else s_upper, fmt)
                return dt.hour * 60 + dt.minute
            except Exception:
                pass
    # 24-hour format
    try:
        parts = s.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return (h * 60 + m) % 1440
    except Exception:
        return 8 * 60


def check_intervals_overlap(start1: int, end1: int, start2: int, end2: int) -> bool:
    """Checks if two time intervals overlap (handles past-midnight wrap)."""
    # Normalize wraps
    def get_segments(st: int, en: int) -> List[Tuple[int, int]]:
        st = st % 1440
        en = en % 1440
        if st == en:
            return [(0, 1440)]  # 24h
        if st < en:
            return [(st, en)]
        else:
            return [(st, 1440), (0, en)]

    segs1 = get_segments(start1, end1)
    segs2 = get_segments(start2, end2)

    for s1, e1 in segs1:
        for s2, e2 in segs2:
            if max(s1, s2) < min(e1, e2):
                return True
    return False


async def calculate_worker_month_stats(worker_id: str, db, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Calculates the worker's total shifts, total worked minutes, and daily average minutes
    for the current calendar month.
    Example: 7 shifts of 330 mins = 2310 mins. On day 10, avg is 2310 / 10 = 231 mins/day.
    """
    if not current_dt:
        current_dt = datetime.now(timezone.utc)

    current_year = current_dt.year
    current_month = current_dt.month
    current_day = max(1, current_dt.day)

    # 1. Fetch assigned cleaning plans for this worker
    plans_cursor = db["cleaning_plans"].find({
        "$or": [
            {"worker_ids": worker_id},
            {"worker_ids": str(worker_id)}
        ]
    })
    plans = await plans_cursor.to_list(length=300)

    total_shifts_this_month = 0
    total_work_minutes_this_month = 0

    for p in plans:
        # Check if plan belongs to current month/year
        p_date = p.get("date") or ""
        p_created = p.get("created_at")
        is_this_month = False

        if isinstance(p_created, datetime):
            if p_created.year == current_year and p_created.month == current_month:
                is_this_month = True
        elif isinstance(p_date, str) and p_date:
            try:
                # Try parsing YYYY-MM-DD or MM/DD/YYYY
                if "-" in p_date:
                    parts = [int(x) for x in p_date.split("-")]
                    if len(parts) == 3 and parts[0] == current_year and parts[1] == current_month:
                        is_this_month = True
                elif "/" in p_date:
                    parts = [int(x) for x in p_date.split("/")]
                    if len(parts) == 3 and parts[0] == current_month and parts[2] == current_year:
                        is_this_month = True
            except Exception:
                pass

        if is_this_month:
            total_shifts_this_month += 1
            dur = p.get("duration_minutes") or 60
            total_work_minutes_this_month += int(dur)

    # 2. Also check shifts collection if present
    shifts_cursor = db["shifts"].find({
        "$or": [
            {"workers.worker_id": worker_id},
            {"worker_ids": worker_id}
        ]
    })
    raw_shifts = await shifts_cursor.to_list(length=300)
    for s in raw_shifts:
        s_date = s.get("date") or ""
        is_this_month = False
        try:
            if isinstance(s_date, str):
                if "-" in s_date:
                    parts = [int(x) for x in s_date.split("-")]
                    if len(parts) == 3 and parts[0] == current_year and parts[1] == current_month:
                        is_this_month = True
                elif "/" in s_date:
                    parts = [int(x) for x in s_date.split("/")]
                    if len(parts) == 3 and parts[0] == current_month and parts[2] == current_year:
                        is_this_month = True
        except Exception:
            pass

        if is_this_month:
            total_shifts_this_month += 1
            dur = s.get("duration_minutes") or s.get("duration") or 60
            total_work_minutes_this_month += int(dur)

    avg_daily_work_minutes = int(total_work_minutes_this_month / current_day)

    return {
        "avg_daily_work_minutes": avg_daily_work_minutes,
        "total_shifts_this_month": total_shifts_this_month,
        "total_work_minutes_this_month": total_work_minutes_this_month,
        "formatted_avg_work": f"{avg_daily_work_minutes} mins/day"
    }


async def check_worker_time_availability(
    worker_id: str,
    plan_date: str,
    plan_start_mins: int,
    plan_end_mins: int,
    exclude_plan_id: str,
    db
) -> Tuple[bool, Optional[str]]:
    """
    Checks if a worker has an overlapping assigned cleaning plan or shift on the given date.
    Returns (is_available, unavailable_reason).
    """
    # 1. Check existing cleaning plans on that date
    plans_query = {
        "$and": [
            {"$or": [{"_id": {"$ne": exclude_plan_id}}, {"id": {"$ne": exclude_plan_id}}]},
            {"$or": [{"worker_ids": worker_id}, {"worker_ids": str(worker_id)}]},
            {"date": plan_date},
            {"status": {"$nin": ["cancelled", "deleted", "completed"]}}
        ]
    }
    other_plans = await db["cleaning_plans"].find(plans_query).to_list(length=50)

    for op in other_plans:
        op_title = op.get("title") or op.get("plan_name", "Cleaning Plan")
        op_start = op.get("start_time", "08:00 AM")
        op_dur = op.get("duration_minutes", 60)
        op_start_mins = parse_time_to_minutes(op_start)
        op_end_mins = (op_start_mins + op_dur) % 1440

        if check_intervals_overlap(plan_start_mins, plan_end_mins, op_start_mins, op_end_mins):
            op_end = op.get("end_time") or f"{op_end_mins // 60:02d}:{op_end_mins % 60:02d}"
            return False, f"Assigned to '{op_title}' ({op_start} - {op_end})"

    # 2. Check shifts collection on that date
    shifts_query = {
        "$and": [
            {"$or": [{"workers.worker_id": worker_id}, {"worker_ids": worker_id}]},
            {"date": plan_date},
            {"status": {"$nin": ["cancelled", "deleted", "completed"]}}
        ]
    }
    other_shifts = await db["shifts"].find(shifts_query).to_list(length=50)

    for os in other_shifts:
        os_title = os.get("title") or os.get("shift_name", "Shift")
        os_start = os.get("start_time", "08:00 AM")
        os_dur = os.get("duration_minutes") or os.get("duration", 60)
        os_start_mins = parse_time_to_minutes(os_start)
        os_end_mins = (os_start_mins + os_dur) % 1440

        if check_intervals_overlap(plan_start_mins, plan_end_mins, os_start_mins, os_end_mins):
            os_end = os.get("end_time") or f"{os_end_mins // 60:02d}:{os_end_mins % 60:02d}"
            return False, f"Assigned to Shift '{os_title}' ({os_start} - {os_end})"

    return True, None


async def calculate_worker_last_work_ended(
    worker_id: str,
    target_date_str: str,
    target_start_mins: int,
    exclude_plan_id: Optional[str],
    db
) -> Dict[str, Any]:
    """
    Finds when the worker's most recent assigned work ended relative to the target plan shift.
    Returns:
      {
        "last_work_end_time": "2026-08-17 01:30 PM" (or None),
        "last_work_ended_ago": "30 minutes ago" (or None),
        "minutes_since_last_work": 30 (or None)
      }
    """
    # 1. Parse target plan base datetime
    target_base_dt = None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            target_base_dt = datetime.strptime(str(target_date_str).strip(), fmt)
            break
        except Exception:
            pass
    if not target_base_dt:
        target_base_dt = datetime.now(timezone.utc)

    target_start_dt = target_base_dt.replace(
        hour=(target_start_mins // 60) % 24,
        minute=target_start_mins % 60,
        second=0,
        microsecond=0
    )

    candidate_ends = []

    # 2. Query assigned cleaning plans
    plans_query = {
        "$and": [
            {"$or": [{"_id": {"$ne": exclude_plan_id}}, {"id": {"$ne": exclude_plan_id}}]},
            {"$or": [{"worker_ids": worker_id}, {"worker_ids": str(worker_id)}]},
            {"status": {"$nin": ["cancelled", "deleted"]}}
        ]
    }
    plans = await db["cleaning_plans"].find(plans_query).to_list(length=100)

    for p in plans:
        p_date = p.get("date")
        if not p_date:
            continue
        p_base_dt = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                p_base_dt = datetime.strptime(str(p_date).strip(), fmt)
                break
            except Exception:
                pass
        if not p_base_dt:
            continue

        p_start_mins = parse_time_to_minutes(p.get("start_time", "08:00 AM"))
        p_dur = int(p.get("duration_minutes") or 60)
        p_end_total_mins = p_start_mins + p_dur
        p_end_dt = p_base_dt.replace(
            hour=(p_end_total_mins // 60) % 24,
            minute=p_end_total_mins % 60,
            second=0,
            microsecond=0
        )
        if p_end_total_mins >= 1440:
            p_end_dt += timedelta(days=p_end_total_mins // 1440)

        time_diff = (target_start_dt - p_end_dt).total_seconds() / 60.0
        p_end_str = p.get("end_time") or f"{(p_end_total_mins // 60) % 24:02d}:{p_end_total_mins % 60:02d}"
        candidate_ends.append({
            "end_dt": p_end_dt,
            "diff_mins": int(time_diff),
            "end_time_str": p_end_str,
            "date_str": str(p_date)
        })

    # 3. Query assigned shifts
    shifts_query = {
        "$and": [
            {"$or": [{"workers.worker_id": worker_id}, {"worker_ids": worker_id}]},
            {"status": {"$nin": ["cancelled", "deleted"]}}
        ]
    }
    shifts = await db["shifts"].find(shifts_query).to_list(length=100)

    for s in shifts:
        s_date = s.get("date")
        if not s_date:
            continue
        s_base_dt = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                s_base_dt = datetime.strptime(str(s_date).strip(), fmt)
                break
            except Exception:
                pass
        if not s_base_dt:
            continue

        s_start_mins = parse_time_to_minutes(s.get("start_time", "08:00 AM"))
        s_dur = int(s.get("duration_minutes") or s.get("duration", 60))
        s_end_total_mins = s_start_mins + s_dur
        s_end_dt = s_base_dt.replace(
            hour=(s_end_total_mins // 60) % 24,
            minute=s_end_total_mins % 60,
            second=0,
            microsecond=0
        )
        if s_end_total_mins >= 1440:
            s_end_dt += timedelta(days=s_end_total_mins // 1440)

        time_diff = (target_start_dt - s_end_dt).total_seconds() / 60.0
        s_end_str = s.get("end_time") or f"{(s_end_total_mins // 60) % 24:02d}:{s_end_total_mins % 60:02d}"
        candidate_ends.append({
            "end_dt": s_end_dt,
            "diff_mins": int(time_diff),
            "end_time_str": s_end_str,
            "date_str": str(s_date)
        })

    if not candidate_ends:
        return {
            "last_work_end_time": None,
            "last_work_ended_ago": None,
            "minutes_since_last_work": None
        }

    # Find jobs that ended before/at target shift start (diff_mins >= 0)
    past_jobs = [c for c in candidate_ends if c["diff_mins"] >= 0]
    if past_jobs:
        best = min(past_jobs, key=lambda x: x["diff_mins"])
    else:
        best = max(candidate_ends, key=lambda x: x["end_dt"])

    diff_mins = best["diff_mins"]

    if diff_mins < 0:
        ended_ago_str = f"In {-diff_mins} mins"
    elif diff_mins == 0:
        ended_ago_str = "Just ended"
    elif diff_mins < 60:
        ended_ago_str = f"{diff_mins} minutes ago" if diff_mins > 1 else "1 minute ago"
    elif diff_mins < 1440:
        hours = diff_mins // 60
        mins = diff_mins % 60
        ended_ago_str = f"{hours} hours {mins} mins ago" if mins > 0 else (f"{hours} hours ago" if hours > 1 else "1 hour ago")
    else:
        days = diff_mins // 1440
        hours = (diff_mins % 1440) // 60
        ended_ago_str = f"{days} days {hours} hrs ago" if hours > 0 else (f"{days} days ago" if days > 1 else "1 day ago")

    return {
        "last_work_end_time": f"{best['date_str']} {best['end_time_str']}",
        "last_work_ended_ago": ended_ago_str,
        "minutes_since_last_work": diff_mins
    }


def sort_workers_by_suitability(
    items: List[Any],
    sort_by: str = "smart"
) -> List[Any]:
    """
    Ranks and sorts cleaning plan worker candidates so that the best suitable workers appear first.

    Multi-tier Smart Sort Ranking Algorithm (sort_by='smart'):
    1. Availability (Primary): is_available=True (0) ranks before False (1). Conflicted workers are pushed to bottom.
    2. Workload / Equal Opportunity: Lower average daily minutes (avg_daily_work_minutes) ranks first (e.g. 0 mins/day before 45 mins/day).
    3. Fatigue / Rest Time: Workers with minutes_since_last_work=None (100% fresh, no prior shift) or higher rest minutes rank first.
    4. Total Shifts: Fewer shifts worked this month (total_shifts_this_month) ranks first.
    5. Employment Priority: Full-time 'employee' (0) ranks before 'freelancer' (1).
    6. Name: Alphabetical order tie-breaker.
    """
    mode = str(sort_by or "smart").strip().lower()

    if mode in ["workload_asc", "workload", "lowest_workload"]:
        def key_func(w):
            avail = 0 if getattr(w, "is_available", True) else 1
            wl = getattr(w, "avg_daily_work_minutes", 0)
            return (avail, wl, getattr(w, "name", "").lower())
    elif mode in ["workload_desc", "highest_workload"]:
        def key_func(w):
            avail = 0 if getattr(w, "is_available", True) else 1
            wl = -getattr(w, "avg_daily_work_minutes", 0)
            return (avail, wl, getattr(w, "name", "").lower())
    elif mode in ["name", "name_asc", "alphabetical"]:
        def key_func(w):
            avail = 0 if getattr(w, "is_available", True) else 1
            return (avail, getattr(w, "name", "").lower())
    elif mode in ["rest_time", "rest_desc"]:
        def key_func(w):
            avail = 0 if getattr(w, "is_available", True) else 1
            m = getattr(w, "minutes_since_last_work", None)
            rest_val = m if m is not None else 99999
            return (avail, -rest_val, getattr(w, "avg_daily_work_minutes", 0))
    else:
        # Default: smart recommendation
        def key_func(w):
            # Tier 1: Availability
            avail_prio = 0 if getattr(w, "is_available", True) else 1

            # Tier 2: Average Daily Workload (0 mins/day comes first)
            workload = getattr(w, "avg_daily_work_minutes", 0)

            # Tier 3: Freshness / Rest Time (None = 100% fresh, prioritized as 99999 mins)
            m = getattr(w, "minutes_since_last_work", None)
            rest_val = m if m is not None else 99999
            rest_prio = -rest_val

            # Tier 4: Total shifts this month
            shifts_cnt = getattr(w, "total_shifts_this_month", 0)

            # Tier 5: Employee before Freelancer
            w_type = str(getattr(w, "worker_type", "employee")).lower()
            type_prio = 0 if w_type == "employee" else 1

            # Tier 6: Name tie-breaker
            name_prio = getattr(w, "name", "").lower()

            return (avail_prio, workload, rest_prio, shifts_cnt, type_prio, name_prio)

    return sorted(items, key=key_func)


async def send_cleaning_plan_assignment_notifications(
    plan_doc: dict,
    assigned_workers: List[dict],
    db
) -> List[dict]:
    """
    Fires rich push notifications (via OneSignal) & saves in-app notifications
    for all workers assigned to a cleaning plan.
    """
    from app.services.notification_service import NotificationService
    notif_service = NotificationService()

    plan_id = str(plan_doc.get("id") or plan_doc.get("_id"))
    plan_title = plan_doc.get("title") or plan_doc.get("plan_name", "Cleaning Shift")
    plan_date = plan_doc.get("date", "")
    start_time = plan_doc.get("start_time", "")
    end_time = plan_doc.get("end_time", "")
    duration_mins = plan_doc.get("duration_minutes", 60)
    location_name = plan_doc.get("location_name") or plan_doc.get("location_id") or ""
    client_names = plan_doc.get("client_names", [])
    shift_notes = plan_doc.get("shift_notes") or plan_doc.get("description") or ""
    rooms_count = len(plan_doc.get("room_ids", []))
    tasks_count = plan_doc.get("total_tasks_count", 0)

    sent_notifications = []

    for w_entry in assigned_workers:
        wid = str(w_entry.get("worker_id") or "")
        if not wid:
            continue
        position = normalize_worker_position(w_entry.get("position"))
        position_display = "Team Leader" if position == "teamleader" else ("Co-Leader" if position == "co_leader" else "Cleaner")

        # Lookup worker user doc
        user_doc = await db["users"].find_one({"$or": [{"_id": wid}, {"id": wid}]})
        if not user_doc and ObjectId.is_valid(wid):
            try:
                user_doc = await db["users"].find_one({"_id": ObjectId(wid)})
            except Exception:
                pass

        player_ids = []
        if user_doc:
            p_id = user_doc.get("onesignal_player_id") or user_doc.get("onesignal_id") or user_doc.get("player_id")
            if p_id:
                player_ids.append(str(p_id))
            for pid_item in user_doc.get("player_ids", []):
                if pid_item and str(pid_item) not in player_ids:
                    player_ids.append(str(pid_item))

        title = f"New Cleaning Shift Assigned: {plan_title}"
        message = (
            f"You have been assigned as {position_display} for '{plan_title}' "
            f"on {plan_date} ({start_time} - {end_time})."
        )

        rich_data = {
            "plan_id": plan_id,
            "title": plan_title,
            "position": position,
            "position_display": position_display,
            "date": plan_date,
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": duration_mins,
            "location_name": location_name,
            "client_names": client_names,
            "rooms_count": rooms_count,
            "total_tasks_count": tasks_count,
            "shift_notes": shift_notes
        }

        notif_doc = await notif_service.create_notification(
            title=title,
            message=message,
            notification_type="shift_assignment",
            recipient_type="worker",
            user_id=wid,
            player_ids=player_ids if player_ids else None,
            plan_id=plan_id,
            data=rich_data
        )
        sent_notifications.append(notif_doc)

    return sent_notifications
