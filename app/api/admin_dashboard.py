import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Dict, Any, Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.admin_dashboard import (
    MinimalItem, ShiftSummaryGroup, WorkerSummaryGroup,
    LocationSummaryGroup, AdminDashboardHomeResponse,
    InProgressShiftItem, InProgressShiftPaginatedResponse,
    WorkerAttendanceDetailItem, WorkerAttendanceGroup, WorkerAttendanceSummaryResponse,
    AttentionWorkerCallPill, AttentionRequiredBanner, DashboardSummaryCards,
    DashboardWorkerItem, ClientLocationGroup, OpenEscalationsBanner,
    AdminDashboardOperationsOverviewResponse
)

admin_dashboard_router = APIRouter(prefix="/manager/dashboard", tags=["Manager Dashboard HomePage"])

def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin, "manager", "admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
    return current_user


async def _find_location_image_url(db, location_id: str) -> Optional[str]:
    loc_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
    if loc_doc and loc_doc.get("image_url"):
        return loc_doc.get("image_url")

    client_doc = await db["client_list"].find_one({"locations.id": location_id})
    if client_doc and "locations" in client_doc:
        for loc in client_doc["locations"]:
            if str(loc.get("id")) == str(location_id) or str(loc.get("_id")) == str(location_id):
                return loc.get("image_url")
    return None


@admin_dashboard_router.get(
    "/overview",
    response_model=AdminDashboardOperationsOverviewResponse,
    summary="Get Admin Dashboard HomePage Operations Overview",
    description="""
### Admin Dashboard HomePage Operations Overview
Returns 100% dynamic, real-time operations overview data directly computed from MongoDB.

#### Live Shift Rules:
- **`live_operations_by_client`**: Shows **only** current in-progress shifts (`start_time <= current_time <= end_time` or workers currently on site). Shifts from earlier or later in the day are excluded.
- **`attention_banner`**: Shows only workers assigned to currently active shifts who are late (past 15-minute grace period without check-in) or missing, including their actual phone number. Workers who checked in on time are not shown here.
- **`summary_cards`**:
  - `active_shifts_count`: Count of active shifts currently running right now.
  - `workers_on_site_count`: Count of workers currently checked in on site.
  - `late_no_show_count`: Count of workers on active shifts who are late or no-show.
  - `reviews_pending_count`: Count of pending photo reviews for shifts that have **ended** (photos from ongoing/future shifts are excluded until the shift ends).
"""
)
async def get_admin_dashboard_overview(
    status_filter: Optional[str] = None,
    timezone: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    from app.api.worker_shift_utils import is_plan_active_on_date, get_or_create_shift_execution
    from app.core.timezone_utils import parse_time_to_minutes, get_timezone
    db = get_database()

    # Determine effective timezone:
    if timezone and str(timezone).strip():
        eff_tz = get_timezone(timezone)
    elif getattr(current_user, "timezone", None):
        eff_tz = get_timezone(current_user.timezone)
    else:
        # Fall back to server's local system timezone
        eff_tz = datetime.now().astimezone().tzinfo or get_timezone("Europe/Amsterdam")

    now_local = datetime.now(eff_tz)
    today_str = now_local.strftime("%Y-%m-%d")
    date_formatted = now_local.strftime("%A, %d %B")
    subtitle_dt_str = f"{date_formatted} • Live status across all locations"
    current_time_minutes = now_local.hour * 60 + now_local.minute

    admin_fname = getattr(current_user, "full_name", None) or getattr(current_user, "name", "Admin")
    greeting_str = f"Good morning, {admin_fname.split()[0]}"

    # 1. Fetch active plans, executions, direct shifts, open escalations, and pending reviews in parallel
    plans, today_execs, direct_shifts, open_esc_cnt, pending_reviews = await asyncio.gather(
        db["cleaning_plans"].find(
            {"status": {"$ne": "cancelled"}},
            {"rooms": 0, "additional_tasks": 0, "additional_required_photos": 0}
        ).to_list(length=200),
        db["shift_executions"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=100),
        db["shifts"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=100),
        db["escalations"].count_documents({"status": {"$in": ["open", "in_progress"]}}),
        db["photo_reviews"].find({"status": "pending_review"}, {"shift_id": 1}).to_list(length=500)
    )

    seen_shift_ids = set()
    raw_shifts = []
    existing_exec_map = {}
    for ex in today_execs:
        pid = str(ex.get("plan_id") or "")
        if pid:
            existing_exec_map[pid] = ex
        eid = str(ex.get("id") or ex.get("_id") or "")
        if eid:
            existing_exec_map[eid] = ex

    active_plans = [p for p in plans if is_plan_active_on_date(p, today_str)]
    create_tasks = []
    for p in active_plans:
        pid = str(p.get("id") or p.get("_id"))
        if pid in existing_exec_map:
            doc = existing_exec_map[pid]
            sid = str(doc.get("id") or doc.get("_id"))
            if sid not in seen_shift_ids:
                seen_shift_ids.add(sid)
                raw_shifts.append(doc)
        else:
            create_tasks.append(get_or_create_shift_execution(p, today_str, db))

    if create_tasks:
        created_execs = await asyncio.gather(*create_tasks)
        for exec_doc in created_execs:
            sid = str(exec_doc.get("id") or exec_doc.get("_id"))
            if sid not in seen_shift_ids:
                seen_shift_ids.add(sid)
                raw_shifts.append(exec_doc)

    for ex in today_execs:
        sid = str(ex.get("id") or ex.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ex)

    for ds in direct_shifts:
        sid = str(ds.get("id") or ds.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ds)

    # 2. Filter raw_shifts to only live / in-progress shifts:
    # A shift is live if:
    # - current_time_minutes is within [start_mins, end_mins], OR
    # - a worker is currently checked in (on site), AND
    # - the shift has not completed / all workers checked out
    live_shifts = []
    for s in raw_shifts:
        if s.get("status") in ["completed", "cancelled"]:
            continue

        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time") or "04:00 PM"
        start_mins = parse_time_to_minutes(start_t)
        end_mins = parse_time_to_minutes(end_t)
        if end_mins < start_mins:
            end_mins += 1440

        worker_entries = s.get("assigned_workers") or s.get("workers") or []
        if not worker_entries and s.get("worker_ids"):
            worker_entries = [{"worker_id": wid} for wid in s.get("worker_ids")]

        has_checked_in = any(
            isinstance(w, dict) and w.get("checkin_time") and not w.get("checkout_time")
            for w in worker_entries
        )
        all_checked_out = (
            len(worker_entries) > 0 and
            all(isinstance(w, dict) and w.get("checkout_time") for w in worker_entries)
        )

        is_in_time_window = (start_mins <= current_time_minutes <= end_mins)
        is_live = (is_in_time_window or has_checked_in) and not all_checked_out

        if is_live:
            live_shifts.append(s)

    # 3. High-performance reviews count with single batch lookup for missing shifts
    shift_status_cache = {}
    for s in raw_shifts:
        sid = str(s.get("id") or s.get("_id"))
        shift_status_cache[sid] = (
            str(s.get("date") or ""),
            str(s.get("status") or ""),
            parse_time_to_minutes(s.get("end_time") or "18:00")
        )

    missing_sids = list({
        str(r.get("shift_id")) for r in pending_reviews
        if r.get("shift_id") and str(r.get("shift_id")) not in shift_status_cache
    })

    if missing_sids:
        s_docs, direct_s_docs = await asyncio.gather(
            db["shift_executions"].find(
                {"$or": [{"id": {"$in": missing_sids}}, {"_id": {"$in": missing_sids}}]},
                {"id": 1, "date": 1, "status": 1, "end_time": 1}
            ).to_list(length=len(missing_sids)),
            db["shifts"].find(
                {"$or": [{"id": {"$in": missing_sids}}, {"_id": {"$in": missing_sids}}]},
                {"id": 1, "date": 1, "status": 1, "end_time": 1}
            ).to_list(length=len(missing_sids))
        )
        for s in s_docs + direct_s_docs:
            sid = str(s.get("id") or s.get("_id"))
            shift_status_cache[sid] = (
                str(s.get("date") or ""),
                str(s.get("status") or ""),
                parse_time_to_minutes(s.get("end_time") or "18:00")
            )

    reviews_pending_cnt = 0
    for r in pending_reviews:
        r_sid = str(r.get("shift_id") or "")
        if not r_sid:
            reviews_pending_cnt += 1
            continue
        s_date, s_stat, s_em = shift_status_cache.get(r_sid, ("1970-01-01", "completed", 0))
        if s_date < today_str or s_stat in ["completed", "cancelled"]:
            reviews_pending_cnt += 1
        elif s_date == today_str and (s_stat == "completed" or current_time_minutes > s_em):
            reviews_pending_cnt += 1

    # 4. Batch resolve worker details from users collection
    all_worker_ids = set()
    for s in live_shifts:
        for wid in s.get("worker_ids", []):
            if wid:
                all_worker_ids.add(str(wid))
        for w in (s.get("assigned_workers") or s.get("workers") or []):
            if isinstance(w, dict):
                wid = str(w.get("worker_id") or w.get("id") or "")
                if wid:
                    all_worker_ids.add(wid)

    worker_user_map = {}
    if all_worker_ids:
        w_list = list(all_worker_ids)
        oid_list = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        async for u in db["users"].find({"$or": or_clauses}):
            uid_str = str(u.get("_id") or u.get("id"))
            worker_user_map[uid_str] = u
            if "id" in u and u["id"]:
                worker_user_map[str(u["id"])] = u
            if "_id" in u:
                worker_user_map[str(u["_id"])] = u

    att_pills = []
    late_no_show_cnt = 0
    workers_on_site_cnt = 0
    group_map = {}

    for s in live_shifts:
        c_name = s.get("client_name") or s.get("client_company_name") or s.get("company_name") or "Client"
        c_id = str(s.get("client_id") or "c_1")
        l_name = s.get("location_name") or "Location"
        l_id = str(s.get("location_id") or "l_1")

        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time") or "04:00 PM"
        start_mins = parse_time_to_minutes(start_t)
        end_mins = parse_time_to_minutes(end_t)
        if end_mins < start_mins:
            end_mins += 1440
        grace_mins = start_mins + 15

        grp_key = f"{c_id}_{l_id}"
        if grp_key not in group_map:
            group_map[grp_key] = ClientLocationGroup(
                client_id=c_id,
                client_company_name=c_name,
                location_id=l_id,
                location_name=l_name,
                roster_count_text="0 on roster",
                workers=[]
            )

        worker_entries = s.get("assigned_workers") or s.get("workers") or []
        if not worker_entries and s.get("worker_ids"):
            worker_entries = [{"worker_id": wid} for wid in s.get("worker_ids")]

        for w in worker_entries:
            if not isinstance(w, dict):
                continue
            w_id = str(w.get("worker_id") or w.get("id") or "")
            if not w_id:
                continue

            u_doc = worker_user_map.get(w_id, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
            w_phone = u_doc.get("phone") or u_doc.get("phone_number") or w.get("phone_number") or w.get("phone")
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_picture") or w.get("profile_photo")

            c_time_raw = w.get("checkin_time")
            co_time_raw = w.get("checkout_time")

            is_checked_in = (c_time_raw is not None and co_time_raw is None)
            is_checked_out = (co_time_raw is not None)

            if is_checked_in:
                workers_on_site_cnt += 1
                c_mins = start_mins
                if isinstance(c_time_raw, str):
                    try:
                        c_dt = datetime.fromisoformat(c_time_raw)
                        c_mins = c_dt.hour * 60 + c_dt.minute
                    except Exception:
                        c_mins = start_mins
                elif isinstance(c_time_raw, datetime):
                    c_mins = c_time_raw.hour * 60 + c_time_raw.minute

                if c_mins > grace_mins:
                    st_val = "late"
                    lbl = "Late (Checked In)"
                else:
                    st_val = "on_time"
                    lbl = "On site"
            elif is_checked_out:
                st_val = "completed"
                lbl = "Completed"
            elif current_time_minutes > grace_mins:
                st_val = "late"
                lbl = "Late (No checkin)"
                late_no_show_cnt += 1
                late_mins = current_time_minutes - start_mins
                late_text = f"{late_mins // 60}h {late_mins % 60}m late" if late_mins >= 60 else f"{late_mins}m late"
                att_pills.append(AttentionWorkerCallPill(
                    worker_id=w_id,
                    worker_name=w_name,
                    late_duration_minutes=late_mins,
                    late_duration_text=late_text,
                    phone_number=w_phone
                ))
            else:
                st_val = "scheduled"
                lbl = "Scheduled"

            wk_item = DashboardWorkerItem(
                worker_id=w_id,
                name=w_name,
                profile_picture=w_pic,
                shift_time_range=f"{start_t}–{end_t}",
                delay_reason=w.get("delay_reason"),
                status=st_val,
                status_badge_label=lbl,
                can_call=st_val in ["late", "no_show"],
                phone_number=w_phone
            )

            if not status_filter or status_filter.lower() == "all" or st_val == status_filter.lower():
                group_map[grp_key].workers.append(wk_item)

    for grp in group_map.values():
        grp.roster_count_text = f"{len(grp.workers)} on roster"

    groups = [g for g in group_map.values() if g.workers]

    att_banner = AttentionRequiredBanner(
        people_need_attention_count=len(att_pills),
        badge_text=f"{len(att_pills)} late" if att_pills else "All on time",
        banner_subtitle="Contact them now or arrange a replacement." if att_pills else "All scheduled workers are on time.",
        call_pills=att_pills
    )

    cards = DashboardSummaryCards(
        active_shifts_count=len(live_shifts),
        workers_on_site_count=workers_on_site_cnt,
        late_no_show_count=late_no_show_cnt,
        reviews_pending_count=reviews_pending_cnt
    )

    open_esc_banner = OpenEscalationsBanner(
        open_escalations_count=open_esc_cnt,
        subtitle="One requires a response today" if open_esc_cnt > 0 else "All escalations resolved",
        action_url="/admin/escalations"
    )

    return AdminDashboardOperationsOverviewResponse(
        greeting=greeting_str,
        subtitle_date=subtitle_dt_str,
        attention_banner=att_banner,
        summary_cards=cards,
        live_operations_by_client=groups,
        open_escalations_banner=open_esc_banner
    )


@admin_dashboard_router.get(
    "/in-progress-shifts",
    response_model=InProgressShiftPaginatedResponse,
    summary="Get In-Progress / Live Cleaning Shifts (Paginated)",
    description="""
### Get In-Progress / Live Cleaning Shifts (Paginated)
Returns a paginated list of live in-progress shifts currently running today, including worker details, location details with image URL, check-in status ('on_time', 'late', 'missing'), and dynamic time-based progress percentage.

#### Supported Query Parameters:
- **`page`**: Page number (default: `1`)
- **`limit`**: Page size limit (default: `10`)
- **`search`**: Optional filter matching worker name or location name
- **`timezone`**: Optional client timezone override (e.g. `Asia/Dhaka`, `Europe/Amsterdam`)
"""
)
async def get_in_progress_shifts(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    timezone: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    from app.core.timezone_utils import parse_time_to_minutes, get_timezone
    db = get_database()

    # Resolve effective timezone
    if timezone and str(timezone).strip():
        eff_tz = get_timezone(timezone)
    elif getattr(current_user, "timezone", None):
        eff_tz = get_timezone(current_user.timezone)
    else:
        eff_tz = datetime.now().astimezone().tzinfo or get_timezone("Europe/Amsterdam")

    now_local = datetime.now(eff_tz)
    today_str = now_local.strftime("%Y-%m-%d")
    current_time_minutes = now_local.hour * 60 + now_local.minute

    # Fetch today's shift executions and shifts in parallel
    today_execs, direct_shifts = await asyncio.gather(
        db["shift_executions"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=200),
        db["shifts"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=100)
    )

    seen_shift_ids = set()
    raw_shifts = []
    for s in today_execs + direct_shifts:
        sid = str(s.get("id") or s.get("_id") or "")
        if sid and sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(s)

    # Collect location IDs and worker IDs for batch pre-fetching
    loc_ids = list({str(s.get("location_id")) for s in raw_shifts if s.get("location_id")})
    worker_ids = set()
    for s in raw_shifts:
        for w in (s.get("assigned_workers") or s.get("workers") or []):
            if isinstance(w, dict) and (w.get("worker_id") or w.get("id")):
                worker_ids.add(str(w.get("worker_id") or w.get("id")))
        for wid in s.get("worker_ids", []):
            if wid:
                worker_ids.add(str(wid))

    loc_query = {"$or": [{"_id": {"$in": loc_ids}}, {"id": {"$in": loc_ids}}]} if loc_ids else None
    w_list = list(worker_ids)
    w_oids = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
    user_or = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
    if w_oids:
        user_or.append({"_id": {"$in": w_oids}})
    user_query = {"$or": user_or} if w_list else None

    fetch_tasks = []
    if loc_query:
        fetch_tasks.append(db["locations"].find(loc_query).to_list(length=len(loc_ids)))
    else:
        fetch_tasks.append(asyncio.sleep(0, result=[]))
    if user_query:
        fetch_tasks.append(db["users"].find(user_query).to_list(length=len(w_list)))
    else:
        fetch_tasks.append(asyncio.sleep(0, result=[]))

    raw_locs, raw_users = await asyncio.gather(*fetch_tasks)

    loc_img_map = {}
    for loc in raw_locs:
        img = loc.get("image_url")
        if img:
            for k in (loc.get("_id"), loc.get("id")):
                if k:
                    loc_img_map[str(k)] = img

    user_map = {}
    for u in raw_users:
        uid = str(u.get("_id") or u.get("id"))
        user_map[uid] = u
        if "id" in u and u["id"]:
            user_map[str(u["id"])] = u
        if "_id" in u:
            user_map[str(u["_id"])] = u

    in_progress_items = []

    for s in raw_shifts:
        if s.get("status") in ["completed", "cancelled"]:
            continue

        shift_id = str(s.get("id") or s.get("_id"))
        l_id = str(s.get("location_id") or "")
        l_name = s.get("location_name") or "Location"
        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time") or "04:00 PM"
        start_mins = parse_time_to_minutes(start_t)
        end_mins = parse_time_to_minutes(end_t)
        if end_mins < start_mins:
            end_mins += 1440
        grace_mins = start_mins + 15

        worker_entries = s.get("assigned_workers") or s.get("workers") or []
        if not worker_entries and s.get("worker_ids"):
            worker_entries = [{"worker_id": wid} for wid in s.get("worker_ids")]

        has_checked_in = any(
            isinstance(w, dict) and w.get("checkin_time") and not w.get("checkout_time")
            for w in worker_entries
        )
        all_checked_out = (
            len(worker_entries) > 0 and
            all(isinstance(w, dict) and w.get("checkout_time") for w in worker_entries)
        )

        is_live = (start_mins <= current_time_minutes <= end_mins or has_checked_in) and not all_checked_out
        if not is_live:
            continue

        tot_dur = max(1, end_mins - start_mins)
        elapsed = max(0, current_time_minutes - start_mins)
        prog_val = round(min(100.0, (elapsed / tot_dur) * 100.0), 1)
        prog_str = f"{int(prog_val)}%" if prog_val.is_integer() else f"{prog_val:.1f}%"

        loc_img_url = loc_img_map.get(l_id)

        for w in worker_entries:
            if not isinstance(w, dict):
                continue
            wid = str(w.get("worker_id") or w.get("id") or "")
            if not wid:
                continue

            u_doc = user_map.get(wid, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_picture")

            c_raw = w.get("checkin_time")
            co_raw = w.get("checkout_time")
            c_dt = datetime.fromisoformat(c_raw) if isinstance(c_raw, str) else c_raw

            if c_raw and not co_raw:
                c_mins = start_mins
                if isinstance(c_raw, str):
                    try:
                        c_mins = datetime.fromisoformat(c_raw).hour * 60 + datetime.fromisoformat(c_raw).minute
                    except Exception:
                        pass
                st = "late" if c_mins > grace_mins else "on_time"
            elif co_raw:
                st = "on_time"
            elif current_time_minutes > grace_mins:
                st = "late"
            else:
                st = "on_time"

            if search:
                s_lower = search.lower()
                if not (s_lower in w_name.lower() or s_lower in l_name.lower()):
                    continue

            in_progress_items.append(InProgressShiftItem(
                shift_id=shift_id,
                worker_id=wid,
                worker_name=w_name,
                worker_profile_picture=w_pic,
                location_id=l_id,
                location_name=l_name,
                location_picture_url=loc_img_url,
                worker_checkin_time=c_dt,
                shift_start_time=start_t,
                shift_end_time=end_t,
                progress=prog_val,
                progress_percentage=prog_str,
                checkin_status=st
            ))

    total_count = len(in_progress_items)
    skip = (page - 1) * limit
    paginated_items = in_progress_items[skip : skip + limit]

    return InProgressShiftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        shifts=paginated_items
    )


@admin_dashboard_router.get(
    "/worker-attendance-summary",
    response_model=WorkerAttendanceSummaryResponse,
    summary="Get Worker Attendance Summary Breakdown (Check-in, Late, Missing)",
    description="""
### Get Worker Attendance Summary Breakdown
Returns breakdown metrics for today's shifts containing worker_id, name, profile picture, and worker_type for:
- **`total_checkin`**: Total workers currently or previously checked in today.
- **`late_workers`**: Workers who checked in late (after 15-minute grace period) or whose shift has started (>15m) without check-in.
- **`missing_workers`**: Workers whose shift has ended and who never checked in today.
"""
)
async def get_worker_attendance_summary(
    timezone: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    from app.core.timezone_utils import parse_time_to_minutes, get_timezone
    db = get_database()

    # Resolve effective timezone
    if timezone and str(timezone).strip():
        eff_tz = get_timezone(timezone)
    elif getattr(current_user, "timezone", None):
        eff_tz = get_timezone(current_user.timezone)
    else:
        eff_tz = datetime.now().astimezone().tzinfo or get_timezone("Europe/Amsterdam")

    now_local = datetime.now(eff_tz)
    today_str = now_local.strftime("%Y-%m-%d")
    current_time_minutes = now_local.hour * 60 + now_local.minute

    # Fetch today's shift executions and shifts in parallel
    today_execs, direct_shifts = await asyncio.gather(
        db["shift_executions"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=200),
        db["shifts"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=100)
    )

    seen_shift_ids = set()
    raw_shifts = []
    for s in today_execs + direct_shifts:
        sid = str(s.get("id") or s.get("_id") or "")
        if sid and sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(s)

    # Collect worker IDs for batch lookup
    worker_ids = set()
    for s in raw_shifts:
        for w in (s.get("assigned_workers") or s.get("workers") or []):
            if isinstance(w, dict) and (w.get("worker_id") or w.get("id")):
                worker_ids.add(str(w.get("worker_id") or w.get("id")))
        for wid in s.get("worker_ids", []):
            if wid:
                worker_ids.add(str(wid))

    w_list = list(worker_ids)
    w_oids = [ObjectId(x) for x in w_list if ObjectId.is_valid(x)]
    user_or = [{"_id": {"$in": w_list}}, {"id": {"$in": w_list}}]
    if w_oids:
        user_or.append({"_id": {"$in": w_oids}})
    user_map = {}
    if w_list:
        async for u in db["users"].find({"$or": user_or}):
            uid = str(u.get("_id") or u.get("id"))
            user_map[uid] = u
            if "id" in u and u["id"]:
                user_map[str(u["id"])] = u
            if "_id" in u:
                user_map[str(u["_id"])] = u

    checked_in_workers = []
    late_workers = []
    missing_workers = []

    seen_checked_in = set()
    seen_late = set()
    seen_missing = set()

    for s in raw_shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time") or "04:00 PM"
        start_mins = parse_time_to_minutes(start_t)
        end_mins = parse_time_to_minutes(end_t)
        if end_mins < start_mins:
            end_mins += 1440
        grace_mins = start_mins + 15

        worker_entries = s.get("assigned_workers") or s.get("workers") or []
        if not worker_entries and s.get("worker_ids"):
            worker_entries = [{"worker_id": wid} for wid in s.get("worker_ids")]

        for w in worker_entries:
            if not isinstance(w, dict):
                continue
            wid = str(w.get("worker_id") or w.get("id") or "")
            if not wid:
                continue

            u_doc = user_map.get(wid, {})
            w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Worker"
            w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_picture")
            w_type = str(u_doc.get("worker_type") or w.get("worker_type") or "freelancer")

            c_raw = w.get("checkin_time")
            c_dt = datetime.fromisoformat(c_raw) if isinstance(c_raw, str) else c_raw

            detail = WorkerAttendanceDetailItem(
                worker_id=wid,
                name=w_name,
                profile_picture=w_pic,
                worker_type=w_type,
                shift_id=shift_id,
                checkin_time=c_dt
            )

            if c_raw:
                if wid not in seen_checked_in:
                    checked_in_workers.append(detail)
                    seen_checked_in.add(wid)
                c_mins = start_mins
                if isinstance(c_raw, str):
                    try:
                        c_mins = datetime.fromisoformat(c_raw).hour * 60 + datetime.fromisoformat(c_raw).minute
                    except Exception:
                        pass
                elif isinstance(c_raw, datetime):
                    c_mins = c_raw.hour * 60 + c_raw.minute

                if c_mins > grace_mins and wid not in seen_late:
                    late_workers.append(detail)
                    seen_late.add(wid)
            else:
                if current_time_minutes > end_mins and wid not in seen_missing:
                    missing_workers.append(detail)
                    seen_missing.add(wid)
                elif current_time_minutes > grace_mins and wid not in seen_late:
                    late_workers.append(detail)
                    seen_late.add(wid)

    return WorkerAttendanceSummaryResponse(
        total_checkin=WorkerAttendanceGroup(
            count=len(checked_in_workers),
            workers=checked_in_workers
        ),
        late_workers=WorkerAttendanceGroup(
            count=len(late_workers),
            workers=late_workers
        ),
        missing_workers=WorkerAttendanceGroup(
            count=len(missing_workers),
            workers=missing_workers
        )
    )
