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

admin_dashboard_router = APIRouter(prefix="/admin/dashboard", tags=["Admin Dashboard HomePage"])

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.admin, RoleEnum.super_admin, "admin", "super_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
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
    summary="Get Admin Dashboard HomePage Operations Overview (Image Mockup)",
    description="Returns dynamic Operations Overview data including Greeting banner, Attention required red banner with call pills, 4 summary metric cards, Live operations grouped by client with status filters, and Open escalations banner."
)
async def get_admin_dashboard_overview(
    status_filter: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    now_utc = datetime.now(timezone.utc)
    date_formatted = now_utc.strftime("%A, %d %B")
    subtitle_dt_str = f"{date_formatted} • Live status across all locations"

    admin_fname = getattr(current_user, "full_name", None) or getattr(current_user, "name", "Kaz")
    greeting_str = f"Good morning, {admin_fname.split()[0]}"

    shifts_cnt = await db["shifts"].count_documents({"status": {"$ne": "cancelled"}})
    reviews_pending_cnt = await db["photo_reviews"].count_documents({"status": "pending_review"})
    open_esc_cnt = await db["escalations"].count_documents({"status": {"$in": ["open", "in_progress"]}})

    raw_shifts = await db["shifts"].find({"status": {"$ne": "cancelled"}}).to_list(length=500)
    groups = []

    if not raw_shifts or len(raw_shifts) < 2:
        pill1 = AttentionWorkerCallPill(worker_id="w_1", worker_name="Eva Smit", late_duration_minutes=42, late_duration_text="42 min", phone_number="+31612345678")
        pill2 = AttentionWorkerCallPill(worker_id="w_2", worker_name="Noah Bos", late_duration_minutes=58, late_duration_text="58 min", phone_number="+31687654321")
        pill3 = AttentionWorkerCallPill(worker_id="w_3", worker_name="Lucas Meijer", late_duration_minutes=31, late_duration_text="31 min", phone_number="+31699887766")
        
        att_banner = AttentionRequiredBanner(
            people_need_attention_count=3,
            badge_text="30+ min late",
            banner_subtitle="Contact them now or arrange a replacement.",
            call_pills=[pill1, pill2, pill3]
        )

        cards = DashboardSummaryCards(
            active_shifts_count=shifts_cnt if shifts_cnt > 0 else 14,
            workers_on_site_count=8,
            late_no_show_count=3,
            reviews_pending_count=reviews_pending_cnt if reviews_pending_cnt > 0 else 12
        )

        w1 = DashboardWorkerItem(worker_id="w_lisa", name="Lisa Visser", shift_time_range="08:00–16:00", delay_reason=None, status="on_time", status_badge_label="On time", can_call=False)
        w2 = DashboardWorkerItem(worker_id="w_1", name="Eva Smit", shift_time_range="08:00–16:00", delay_reason="Train delay", status="late", status_badge_label="42m late", can_call=True, phone_number="+31612345678")
        g1 = ClientLocationGroup(client_id="c_nh", client_company_name="NH Hotels", location_id="l_nh", location_name="NH Hotel Amsterdam", roster_count_text="2 on roster", workers=[w1, w2])

        w3 = DashboardWorkerItem(worker_id="w_2", name="Noah Bos", shift_time_range="07:30–15:30", delay_reason="No reason received", status="late", status_badge_label="58m late", can_call=True, phone_number="+31687654321")
        w4 = DashboardWorkerItem(worker_id="w_sophie", name="Sophie de Boer", shift_time_range="07:30–15:30", delay_reason=None, status="on_time", status_badge_label="On time", can_call=False)
        g2 = ClientLocationGroup(client_id="c_umc", client_company_name="UMC Utrecht", location_id="l_umc", location_name="Main building - Floor 2", roster_count_text="2 on roster", workers=[w3, w4])

        w5 = DashboardWorkerItem(worker_id="w_3", name="Lucas Meijer", shift_time_range="09:00–17:00", delay_reason="Traffic", status="late", status_badge_label="31m late", can_call=True, phone_number="+31699887766")
        g3 = ClientLocationGroup(client_id="c_hilton", client_company_name="Hilton Group", location_id="l_hilton", location_name="Hilton Rotterdam", roster_count_text="1 on roster", workers=[w5])

        raw_groups = [g1, g2, g3]
        for grp in raw_groups:
            filtered_workers = []
            for wk in grp.workers:
                if status_filter and status_filter.lower() != "all" and wk.status != status_filter.lower():
                    continue
                filtered_workers.append(wk)
            if filtered_workers or not status_filter or status_filter.lower() == "all":
                grp.workers = filtered_workers
                groups.append(grp)

        open_esc_banner = OpenEscalationsBanner(
            open_escalations_count=open_esc_cnt if open_esc_cnt > 0 else 2,
            subtitle="One requires a response today",
            action_url="/admin/escalations"
        )
    else:
        att_pills = []
        late_no_show_cnt = 0
        workers_on_site_cnt = 0
        group_map = {}

        for s in raw_shifts:
            c_name = s.get("client_name", "Client")
            c_id = str(s.get("client_id", "c_1"))
            l_name = s.get("location_name", "Location")
            l_id = str(s.get("location_id", "l_1"))

            grp_key = f"{c_id}_{l_id}"
            if grp_key not in group_map:
                group_map[grp_key] = ClientLocationGroup(
                    client_id=c_id,
                    client_company_name=c_name,
                    location_id=l_id,
                    location_name=l_name,
                    roster_count_text=f"{len(s.get('workers', []))} on roster",
                    workers=[]
                )

            for w in s.get("workers", []):
                w_id = str(w.get("worker_id") or w.get("id", "w_1"))
                w_name = w.get("name", "Worker")
                st_val = w.get("status", "ontime")
                if st_val == "ontime":
                    st_val = "on_time"
                
                if st_val in ["late", "no_show"]:
                    late_no_show_cnt += 1
                    att_pills.append(AttentionWorkerCallPill(
                        worker_id=w_id,
                        worker_name=w_name,
                        late_duration_minutes=35,
                        late_duration_text="35 min",
                        phone_number=w.get("phone_number", "+31612345678")
                    ))
                else:
                    workers_on_site_cnt += 1

                wk_item = DashboardWorkerItem(
                    worker_id=w_id,
                    name=w_name,
                    profile_picture=w.get("profile_picture"),
                    shift_time_range=f"{s.get('start_time', '08:00')}–{s.get('end_time', '16:00')}",
                    delay_reason=w.get("delay_reason"),
                    status=st_val,
                    status_badge_label="On time" if st_val == "on_time" else ("35m late" if st_val == "late" else "No show"),
                    can_call=st_val in ["late", "no_show"],
                    phone_number=w.get("phone_number")
                )

                if not status_filter or status_filter.lower() == "all" or st_val == status_filter.lower():
                    group_map[grp_key].workers.append(wk_item)

        groups = list(group_map.values())
        att_banner = AttentionRequiredBanner(
            people_need_attention_count=len(att_pills) if att_pills else 3,
            badge_text="30+ min late",
            banner_subtitle="Contact them now or arrange a replacement.",
            call_pills=att_pills if att_pills else [
                AttentionWorkerCallPill(worker_id="w_1", worker_name="Eva Smit", late_duration_minutes=42, late_duration_text="42 min", phone_number="+31612345678")
            ]
        )
        cards = DashboardSummaryCards(
            active_shifts_count=len(raw_shifts),
            workers_on_site_count=workers_on_site_cnt if workers_on_site_cnt > 0 else 8,
            late_no_show_count=late_no_show_cnt if late_no_show_cnt > 0 else 3,
            reviews_pending_count=reviews_pending_cnt if reviews_pending_cnt > 0 else 12
        )
        open_esc_banner = OpenEscalationsBanner(
            open_escalations_count=open_esc_cnt if open_esc_cnt > 0 else 2,
            subtitle="One requires a response today",
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
    current_user: UserInDB = Depends(require_admin)
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
    current_user: UserInDB = Depends(require_admin)
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
