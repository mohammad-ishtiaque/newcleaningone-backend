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
    description="Returns 100% dynamic Operations Overview data computed directly from MongoDB."
)
async def get_admin_dashboard_overview(
    status_filter: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    from app.api.worker_shift_utils import is_plan_active_on_date, get_or_create_shift_execution, evaluate_worker_attendance_status
    from app.core.timezone_utils import parse_plan_start_datetime, get_timezone
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    date_formatted = now_utc.strftime("%A, %d %B")
    subtitle_dt_str = f"{date_formatted} • Live status across all locations"

    admin_fname = getattr(current_user, "full_name", None) or getattr(current_user, "name", "Admin")
    greeting_str = f"Good morning, {admin_fname.split()[0]}"

    reviews_pending_cnt = await db["photo_reviews"].count_documents({"status": "pending_review"})
    open_esc_cnt = await db["escalations"].count_documents({"status": {"$in": ["open", "in_progress"]}})

    # 1. Fetch active plans and daily shift executions for today
    raw_shifts = []
    seen_shift_ids = set()

    cursor_plans = db["cleaning_plans"].find({"status": {"$ne": "cancelled"}})
    plans = await cursor_plans.to_list(length=200)
    for p in plans:
        if is_plan_active_on_date(p, today_str):
            exec_doc = await get_or_create_shift_execution(p, today_str, db)
            sid = str(exec_doc.get("id") or exec_doc.get("_id"))
            if sid not in seen_shift_ids:
                seen_shift_ids.add(sid)
                raw_shifts.append(exec_doc)

    exec_cursor = db["shift_executions"].find({"date": today_str, "status": {"$ne": "cancelled"}})
    today_execs = await exec_cursor.to_list(length=100)
    for ex in today_execs:
        sid = str(ex.get("id") or ex.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ex)

    direct_shifts = await db["shifts"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=100)
    for ds in direct_shifts:
        sid = str(ds.get("id") or ds.get("_id"))
        if sid not in seen_shift_ids:
            seen_shift_ids.add(sid)
            raw_shifts.append(ds)

    # 2. Batch resolve worker details from users collection
    all_worker_ids = set()
    for s in raw_shifts:
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

    for s in raw_shifts:
        c_name = s.get("client_name") or s.get("client_company_name") or s.get("company_name") or "Client"
        c_id = str(s.get("client_id") or "c_1")
        l_name = s.get("location_name") or "Location"
        l_id = str(s.get("location_id") or "l_1")

        start_t = s.get("start_time", "08:00 AM")
        end_t = s.get("end_time", "04:00 PM")
        s_tz = s.get("timezone") or "Europe/Amsterdam"

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

            # Evaluate attendance status
            status_label = evaluate_worker_attendance_status(
                plan_doc=s,
                worker_record={"checkin_time": c_time_raw, "checkout_time": co_time_raw},
                now_utc=now_utc,
                target_date_str=today_str
            )

            is_checked_in = (c_time_raw is not None and co_time_raw is None)
            is_checked_out = (co_time_raw is not None)

            if is_checked_in:
                workers_on_site_cnt += 1
                if status_label == "late":
                    st_val = "late"
                    lbl = "Late (Checked In)"
                else:
                    st_val = "on_time"
                    lbl = "On site"
            elif is_checked_out:
                st_val = "completed"
                lbl = "Completed"
            elif status_label == "late":
                st_val = "late"
                late_no_show_cnt += 1
                lbl = "Late (No checkin)"
                att_pills.append(AttentionWorkerCallPill(
                    worker_id=w_id,
                    worker_name=w_name,
                    late_duration_minutes=15,
                    late_duration_text="15+ min",
                    phone_number=w_phone
                ))
            elif status_label == "missing":
                st_val = "no_show"
                late_no_show_cnt += 1
                lbl = "No show"
                att_pills.append(AttentionWorkerCallPill(
                    worker_id=w_id,
                    worker_name=w_name,
                    late_duration_minutes=60,
                    late_duration_text="Shift ended",
                    phone_number=w_phone
                ))
            elif status_label == "ontime":
                st_val = "on_time"
                lbl = "On time"
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

    groups = [g for g in group_map.values() if g.workers or not status_filter or status_filter.lower() == "all"]

    att_banner = AttentionRequiredBanner(
        people_need_attention_count=len(att_pills),
        badge_text=f"{len(att_pills)} late" if att_pills else "All on time",
        banner_subtitle="Contact them now or arrange a replacement." if att_pills else "All scheduled workers are on time.",
        call_pills=att_pills
    )

    cards = DashboardSummaryCards(
        active_shifts_count=len(raw_shifts),
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
    description="Returns a paginated list of live in-progress shifts currently running today, including worker details, location details with image URL, check-in status ('on_time', 'late', 'missing'), and dynamic time-based progress percentage."
)
async def get_in_progress_shifts(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_date_str = now_utc.date().isoformat()
    current_time_str = now_utc.strftime("%H:%M")

    now_hours, now_mins = now_utc.hour, now_utc.minute
    current_time_minutes = now_hours * 60 + now_mins

    query = {
        "date": today_date_str,
        "status": {"$ne": "cancelled"}
    }
    raw_shifts = await db["shifts"].find(query).sort("start_time", 1).to_list(length=1000)

    in_progress_items = []

    for s in raw_shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        l_id = s.get("location_id", "")
        l_name = s.get("location_name", "Location")
        start_t = s.get("start_time", "08:00")
        end_t = s.get("end_time", "18:00")

        try:
            sh, sm = map(int, start_t.split(":"))
            eh, em = map(int, end_t.split(":"))
            start_mins = sh * 60 + sm
            end_mins = eh * 60 + em
        except Exception:
            start_mins = 8 * 60
            end_mins = 18 * 60

        total_duration_mins = max(1, end_mins - start_mins)

        loc_img_url = await _find_location_image_url(db, l_id)

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "")
            w_pic = w.get("profile_picture")
            c_time_raw = w.get("checkin_time")
            assigned_status = w.get("status")

            if c_time_raw:
                if isinstance(c_time_raw, str):
                    c_time_raw = datetime.fromisoformat(c_time_raw)
                checkin_status = assigned_status if assigned_status in ["ontime", "late"] else "on_time"
                if checkin_status == "ontime":
                    checkin_status = "on_time"
            else:
                if current_time_minutes > start_mins:
                    checkin_status = "missing"
                else:
                    checkin_status = "on_time"

            if checkin_status == "missing":
                progress_val = 0.0
            else:
                elapsed_mins = current_time_minutes - start_mins
                progress_val = round(min(100.0, max(0.0, (elapsed_mins / total_duration_mins) * 100.0)), 1)

            progress_str = f"{int(progress_val)}%" if progress_val.is_integer() else f"{progress_val:.1f}%"

            if search:
                s_lower = search.lower()
                if not (s_lower in w_name.lower() or s_lower in l_name.lower()):
                    continue

            in_progress_items.append(InProgressShiftItem(
                shift_id=shift_id,
                worker_id=w_id,
                worker_name=w_name,
                worker_profile_picture=w_pic,
                location_id=l_id,
                location_name=l_name,
                location_picture_url=loc_img_url,
                worker_checkin_time=c_time_raw,
                shift_start_time=start_t,
                shift_end_time=end_t,
                progress=progress_val,
                progress_percentage=progress_str,
                checkin_status=checkin_status
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
    description="Returns breakdown metrics for today's shifts containing worker_id, name, profile picture, and worker_type for total checkin count, late worker count, and missing worker count."
)
async def get_worker_attendance_summary(
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    today_date_str = now_utc.date().isoformat()

    now_hours, now_mins = now_utc.hour, now_utc.minute
    current_time_minutes = now_hours * 60 + now_mins

    shifts = await db["shifts"].find({
        "date": today_date_str,
        "status": {"$ne": "cancelled"}
    }).to_list(length=1000)

    checked_in_workers = []
    late_workers = []
    missing_workers = []

    seen_checked_in = set()
    seen_late = set()
    seen_missing = set()

    for s in shifts:
        shift_id = str(s.get("id") or s.get("_id"))
        start_t = s.get("start_time", "08:00")

        try:
            sh, sm = map(int, start_t.split(":"))
            start_mins = sh * 60 + sm
        except Exception:
            start_mins = 8 * 60

        for w in s.get("workers", []):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "")
            w_pic = w.get("profile_picture")
            w_t = w.get("worker_type", "freelancer")
            c_time_raw = w.get("checkin_time")
            w_status = w.get("status")

            c_dt = datetime.fromisoformat(c_time_raw) if isinstance(c_time_raw, str) else c_time_raw

            item = WorkerAttendanceDetailItem(
                worker_id=w_id,
                name=w_name,
                profile_picture=w_pic,
                worker_type=str(w_t),
                shift_id=shift_id,
                checkin_time=c_dt
            )

            if c_time_raw and w_id not in seen_checked_in:
                checked_in_workers.append(item)
                seen_checked_in.add(w_id)

            if c_time_raw and w_status == "late" and w_id not in seen_late:
                late_workers.append(item)
                seen_late.add(w_id)

            if not c_time_raw and current_time_minutes > start_mins and w_id not in seen_missing:
                missing_workers.append(item)
                seen_missing.add(w_id)

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
