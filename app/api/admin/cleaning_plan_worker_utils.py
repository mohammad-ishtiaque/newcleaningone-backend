import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any, List
from bson import ObjectId
from app.api.worker_shift_utils import normalize_working_days, extract_date_weekday


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
    repeat_shift: Optional[str] = None,
    working_days: Optional[List[str]] = None,
    frequency: Optional[List[str]] = None,
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


def compute_worker_month_stats_from_docs(
    plans: List[dict],
    shifts: List[dict],
    current_dt: Optional[datetime] = None
) -> Dict[str, Any]:
    """Computes monthly stats from pre-fetched plan and shift docs in memory."""
    if not current_dt:
        current_dt = datetime.now(timezone.utc)

    current_year = current_dt.year
    current_month = current_dt.month
    current_day = max(1, current_dt.day)

    total_shifts_this_month = 0
    total_work_minutes_this_month = 0

    for p in plans:
        p_date = p.get("date") or ""
        p_created = p.get("created_at")
        is_this_month = False

        if isinstance(p_created, datetime):
            if p_created.year == current_year and p_created.month == current_month:
                is_this_month = True
        elif isinstance(p_date, str) and p_date:
            try:
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

    for s in shifts:
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
            dur = s.get("duration_minutes") or s.get("duration", 60)
            total_work_minutes_this_month += int(dur)

    avg_daily_work_minutes = int(total_work_minutes_this_month / current_day)

    return {
        "avg_daily_work_minutes": avg_daily_work_minutes,
        "total_shifts_this_month": total_shifts_this_month,
        "total_work_minutes_this_month": total_work_minutes_this_month,
        "formatted_avg_work": f"{avg_daily_work_minutes} mins/day"
    }


def compute_worker_time_availability_from_docs(
    plans: List[dict],
    shifts: List[dict],
    plan_date: str,
    plan_start_mins: int,
    plan_end_mins: int,
    exclude_plan_id: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """Checks worker time availability from pre-fetched plan and shift docs in memory."""
    # Check cleaning plans
    for op in plans:
        op_id = str(op.get("_id") or op.get("id"))
        if exclude_plan_id and op_id == str(exclude_plan_id):
            continue
        if op.get("date") != plan_date:
            continue
        if op.get("status") in ["cancelled", "deleted", "completed"]:
            continue

        op_title = op.get("title") or op.get("plan_name", "Cleaning Plan")
        op_start = op.get("start_time", "08:00 AM")
        op_dur = op.get("duration_minutes", 60)
        op_start_mins = parse_time_to_minutes(op_start)
        op_end_mins = (op_start_mins + op_dur) % 1440

        if check_intervals_overlap(plan_start_mins, plan_end_mins, op_start_mins, op_end_mins):
            op_end = op.get("end_time") or f"{op_end_mins // 60:02d}:{op_end_mins % 60:02d}"
            return False, f"Assigned to '{op_title}' ({op_start} - {op_end})"

    # Check shifts
    for os in shifts:
        if os.get("date") != plan_date:
            continue
        if os.get("status") in ["cancelled", "deleted", "completed"]:
            continue

        os_title = os.get("title") or os.get("shift_name", "Shift")
        os_start = os.get("start_time", "08:00 AM")
        os_dur = os.get("duration_minutes") or os.get("duration", 60)
        os_start_mins = parse_time_to_minutes(os_start)
        os_end_mins = (os_start_mins + os_dur) % 1440

        if check_intervals_overlap(plan_start_mins, plan_end_mins, os_start_mins, os_end_mins):
            os_end = os.get("end_time") or f"{os_end_mins // 60:02d}:{os_end_mins % 60:02d}"
            return False, f"Assigned to Shift '{os_title}' ({os_start} - {os_end})"

    return True, None


def compute_worker_last_work_ended_from_docs(
    plans: List[dict],
    shifts: List[dict],
    target_date_str: str,
    target_start_mins: int,
    exclude_plan_id: Optional[str] = None
) -> Dict[str, Any]:
    """Computes last work ended info from pre-fetched plan and shift docs in memory."""
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

    for p in plans:
        op_id = str(p.get("_id") or p.get("id"))
        if exclude_plan_id and op_id == str(exclude_plan_id):
            continue
        if p.get("status") in ["cancelled", "deleted"]:
            continue
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

    for s in shifts:
        if s.get("status") in ["cancelled", "deleted"]:
            continue
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


async def batch_compute_worker_metrics(
    worker_ids: List[str],
    plan_id: str,
    plan_date: str,
    plan_start_mins: int,
    plan_end_mins: int,
    db: Any,
    current_dt: Optional[datetime] = None
) -> Dict[str, Dict[str, Any]]:
    """
    High-Performance Batch Loader: Fetches all plans & shifts in parallel (2 queries total),
    then computes availability, fatigue, workload, and rest time in memory in < 1ms!
    """
    if not current_dt:
        current_dt = datetime.now(timezone.utc)

    if not worker_ids:
        return {}

    plans_query = {
        "$and": [
            {"worker_ids": {"$in": worker_ids}},
            {"status": {"$nin": ["cancelled", "deleted"]}}
        ]
    }
    shifts_query = {
        "$and": [
            {"$or": [
                {"workers.worker_id": {"$in": worker_ids}},
                {"worker_ids": {"$in": worker_ids}}
            ]},
            {"status": {"$nin": ["cancelled", "deleted"]}}
        ]
    }

    plan_projection = {
        "_id": 1, "id": 1, "worker_ids": 1, "date": 1, "start_time": 1,
        "end_time": 1, "duration_minutes": 1, "status": 1, "created_at": 1,
        "title": 1, "plan_name": 1
    }
    shift_projection = {
        "_id": 1, "id": 1, "workers": 1, "worker_ids": 1, "date": 1,
        "start_time": 1, "end_time": 1, "duration_minutes": 1, "duration": 1,
        "status": 1, "created_at": 1, "title": 1, "shift_name": 1
    }

    cursor_p = db["cleaning_plans"].find(plans_query, plan_projection)
    cursor_s = db["shifts"].find(shifts_query, shift_projection)

    user_query = {"$or": [{"_id": {"$in": worker_ids}}, {"id": {"$in": worker_ids}}]}
    oid_list = [ObjectId(x) for x in worker_ids if ObjectId.is_valid(x)]
    if oid_list:
        user_query["$or"].append({"_id": {"$in": oid_list}})
    cursor_u = db["users"].find(user_query, {"_id": 1, "id": 1, "working_days": 1})
    cursor_a = db["worker_availability"].find({"worker_id": {"$in": worker_ids}})

    all_plans, all_shifts, all_users, all_avails = await asyncio.gather(
        cursor_p.to_list(length=2000),
        cursor_s.to_list(length=2000),
        cursor_u.to_list(length=len(worker_ids) + 50),
        cursor_a.to_list(length=len(worker_ids) + 50)
    )

    worker_user_map = {}
    for u in all_users:
        worker_user_map[str(u.get("_id"))] = u
        if u.get("id"):
            worker_user_map[str(u["id"])] = u

    worker_avail_map = {str(a.get("worker_id")): a for a in all_avails}

    plan_day_abbr, plan_day_full = extract_date_weekday(plan_date)

    worker_plans_map: Dict[str, List[dict]] = {wid: [] for wid in worker_ids}
    for p in all_plans:
        for pwid in p.get("worker_ids", []):
            spwid = str(pwid)
            if spwid in worker_plans_map:
                worker_plans_map[spwid].append(p)

    worker_shifts_map: Dict[str, List[dict]] = {wid: [] for wid in worker_ids}
    for s in all_shifts:
        s_wids = set()
        for w_item in s.get("workers", []):
            if isinstance(w_item, dict):
                s_wids.add(str(w_item.get("worker_id", "")))
            elif isinstance(w_item, str):
                s_wids.add(w_item)
        for w_id in s.get("worker_ids", []):
            s_wids.add(str(w_id))

        for wid in s_wids:
            if wid in worker_shifts_map:
                worker_shifts_map[wid].append(s)

    results = {}
    for wid in worker_ids:
        w_plans = worker_plans_map.get(wid, [])
        w_shifts = worker_shifts_map.get(wid, [])

        # 1. Check worker working days / off-day schedule
        w_user = worker_user_map.get(wid, {})
        w_raw_days = w_user.get("working_days")
        if w_raw_days is None and wid in worker_avail_map:
            w_raw_days = [
                d["day"][:3].lower()
                for d in worker_avail_map[wid].get("weekly_availability", [])
                if d.get("is_available", True)
            ]

        norm_w_days, _ = normalize_working_days(w_raw_days)
        is_off_day = bool(plan_day_abbr and (plan_day_abbr not in norm_w_days))

        if is_off_day:
            is_avail = False
            unavail_reason = f"Off Day ({plan_day_full or 'Weekly Schedule'})"
        else:
            is_avail, unavail_reason = compute_worker_time_availability_from_docs(
                plans=w_plans,
                shifts=w_shifts,
                plan_date=plan_date,
                plan_start_mins=plan_start_mins,
                plan_end_mins=plan_end_mins,
                exclude_plan_id=plan_id
            )

        month_stats = compute_worker_month_stats_from_docs(
            plans=w_plans,
            shifts=w_shifts,
            current_dt=current_dt
        )

        last_ended = compute_worker_last_work_ended_from_docs(
            plans=w_plans,
            shifts=w_shifts,
            target_date_str=plan_date,
            target_start_mins=plan_start_mins,
            exclude_plan_id=plan_id
        )

        results[wid] = {
            "is_available": is_avail,
            "unavailable_reason": unavail_reason,
            "month_stats": month_stats,
            "last_ended": last_ended,
            "working_days": norm_w_days
        }

    return results


async def calculate_worker_month_stats(worker_id: str, db, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """Single worker fallback for calculate_worker_month_stats."""
    plans = await db["cleaning_plans"].find({"worker_ids": str(worker_id), "status": {"$nin": ["cancelled", "deleted"]}}).to_list(length=300)
    shifts = await db["shifts"].find({"$or": [{"workers.worker_id": str(worker_id)}, {"worker_ids": str(worker_id)}], "status": {"$nin": ["cancelled", "deleted"]}}).to_list(length=300)
    return compute_worker_month_stats_from_docs(plans, shifts, current_dt=current_dt)


async def check_worker_time_availability(
    worker_id: str, plan_date: str, plan_start_mins: int,
    plan_end_mins: int, exclude_plan_id: Optional[str] = None, db: Any = None
) -> Tuple[bool, Optional[str]]:
    """Single worker fallback for check_worker_time_availability with working days check."""
    if db is None:
        from app.core.database import get_database
        db = get_database()
    plan_day_abbr, plan_day_full = extract_date_weekday(plan_date)
    u_doc = await db["users"].find_one({"$or": [{"_id": ObjectId(worker_id) if ObjectId.is_valid(worker_id) else worker_id}, {"_id": worker_id}, {"id": worker_id}]}) or {}
    w_raw_days = u_doc.get("working_days")
    if w_raw_days is None:
        avail_doc = await db["worker_availability"].find_one({"worker_id": str(worker_id)})
        if avail_doc:
            w_raw_days = [d["day"][:3].lower() for d in avail_doc.get("weekly_availability", []) if d.get("is_available", True)]
    norm_w_days, _ = normalize_working_days(w_raw_days)
    if plan_day_abbr and (plan_day_abbr not in norm_w_days):
        return False, f"Off Day ({plan_day_full or 'Weekly Schedule'})"
    plans = await db["cleaning_plans"].find({"worker_ids": str(worker_id), "date": plan_date, "status": {"$nin": ["cancelled", "deleted", "completed"]}}).to_list(length=50)
    shifts = await db["shifts"].find({"$or": [{"workers.worker_id": str(worker_id)}, {"worker_ids": str(worker_id)}], "date": plan_date, "status": {"$nin": ["cancelled", "deleted", "completed"]}}).to_list(length=50)
    return compute_worker_time_availability_from_docs(plans, shifts, plan_date, plan_start_mins, plan_end_mins, exclude_plan_id)


async def calculate_worker_last_work_ended(worker_id: str, target_date_str: str, target_start_mins: int, exclude_plan_id: Optional[str], db: Any) -> Dict[str, Any]:
    """Single worker fallback for calculate_worker_last_work_ended."""
    plans = await db["cleaning_plans"].find({"worker_ids": str(worker_id), "status": {"$nin": ["cancelled", "deleted"]}}).to_list(length=100)
    shifts = await db["shifts"].find({"$or": [{"workers.worker_id": str(worker_id)}, {"worker_ids": str(worker_id)}], "status": {"$nin": ["cancelled", "deleted"]}}).to_list(length=100)
    return compute_worker_last_work_ended_from_docs(plans, shifts, target_date_str, target_start_mins, exclude_plan_id)


def sort_workers_by_suitability(items: List[Any], sort_by: str = "smart") -> List[Any]:
    """Ranks and sorts cleaning plan worker candidates so that the best suitable workers appear first."""
    mode = str(sort_by or "smart").strip().lower()
    if mode in ["workload_asc", "workload", "lowest_workload"]:
        key_func = lambda w: (0 if getattr(w, "is_available", True) else 1, getattr(w, "avg_daily_work_minutes", 0), getattr(w, "name", "").lower())
    elif mode in ["workload_desc", "highest_workload"]:
        key_func = lambda w: (0 if getattr(w, "is_available", True) else 1, -getattr(w, "avg_daily_work_minutes", 0), getattr(w, "name", "").lower())
    elif mode in ["name", "name_asc", "alphabetical"]:
        key_func = lambda w: (0 if getattr(w, "is_available", True) else 1, getattr(w, "name", "").lower())
    elif mode in ["rest_time", "rest_desc"]:
        key_func = lambda w: (0 if getattr(w, "is_available", True) else 1, -(getattr(w, "minutes_since_last_work", None) or 99999), getattr(w, "avg_daily_work_minutes", 0))
    else:
        key_func = lambda w: (
            0 if getattr(w, "is_available", True) else 1,
            getattr(w, "avg_daily_work_minutes", 0),
            -(getattr(w, "minutes_since_last_work", None) if getattr(w, "minutes_since_last_work", None) is not None else 99999),
            getattr(w, "total_shifts_this_month", 0),
            0 if str(getattr(w, "worker_type", "employee")).lower() == "employee" else 1,
            getattr(w, "name", "").lower()
        )
    return sorted(items, key=key_func)


async def send_cleaning_plan_assignment_notifications(plan_doc: dict, assigned_workers: List[dict], db) -> List[dict]:
    """Fires push & in-app notifications for all workers assigned to a cleaning plan."""
    from app.services.notification_service import NotificationService
    notif_service = NotificationService()
    plan_id = str(plan_doc.get("id") or plan_doc.get("_id"))
    plan_title = plan_doc.get("title") or plan_doc.get("plan_name", "Cleaning Shift")
    plan_date = plan_doc.get("date", "")
    start_time = plan_doc.get("start_time", "")
    end_time = plan_doc.get("end_time", "")
    sent_notifications = []

    for w_entry in assigned_workers:
        wid = str(w_entry.get("worker_id") or "")
        if not wid:
            continue
        position = normalize_worker_position(w_entry.get("position"))
        position_display = "Team Leader" if position == "teamleader" else ("Co-Leader" if position == "co_leader" else "Cleaner")
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
        message = f"You have been assigned as {position_display} for '{plan_title}' on {plan_date} ({start_time} - {end_time})."
        rich_data = {
            "plan_id": plan_id, "shift_id": plan_id, "service_kind": "cleaning_plan",
            "deeplink": f"cleaningone://worker/shifts/{plan_id}", "route": f"/worker/shifts/{plan_id}",
            "title": plan_title, "position": position, "position_display": position_display,
            "date": plan_date, "start_time": start_time, "end_time": end_time,
            "duration_minutes": plan_doc.get("duration_minutes", 60),
            "location_name": plan_doc.get("location_name") or plan_doc.get("location_id") or "",
            "client_names": plan_doc.get("client_names", []),
            "rooms_count": len(plan_doc.get("room_ids", [])),
            "total_tasks_count": plan_doc.get("total_tasks_count", 0),
            "shift_notes": plan_doc.get("shift_notes") or plan_doc.get("description") or ""
        }
        notif_doc = await notif_service.create_notification(
            title=title, message=message, notification_type="shift_assignment",
            recipient_type="worker", user_id=wid, player_ids=player_ids or None,
            plan_id=plan_id, data=rich_data
        )
        sent_notifications.append(notif_doc)
        try:
            from app.api.chat import ws_manager
            await ws_manager.broadcast_to_users({"type": "shift_assignment", "plan_id": plan_id, "title": title, "message": message, "data": rich_data}, [wid])
        except Exception:
            pass

    return sent_notifications

