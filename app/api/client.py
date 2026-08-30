import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, status, HTTPException
from app.schemas.user import ClientSignup, ClientUpdate, ClientProfileResponse
from app.schemas.help import LegalDocumentResponse
from app.schemas.notification import NotificationListResponse
from app.schemas.client_overview import (
    ClientOverviewResponse, TodaysOverallProgress, MetricsGrid, NextVisitCard, OnSiteNowCard, LastCompletedCard,
    LiveStatusSection, SpecialistOnSiteItem, NextVisitorItem, QuickActionItem
)
from app.schemas.shift import LiveStatusResponse, LiveStatusRoomItem, LiveStatusTaskItem
from app.services.user_service import UserService
from app.services.notification_service import NotificationService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import rate_limit_signup
from app.models.user import UserInDB, RoleEnum
from app.core.database import get_database

router = APIRouter(prefix="/client", tags=["Client Dashboard Management"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user

@router.post("/signup", response_model=ClientProfileResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_signup)])
async def signup_client(
    user_in: ClientSignup,
    user_service: UserService = Depends(get_user_service)
):
    user_resp = await user_service.signup_client(user_in)
    return user_resp

@router.get("/", response_model=ClientProfileResponse)
async def get_client_profile(current_user: UserInDB = Depends(require_client)):
    return ClientProfileResponse(**current_user.model_dump())

@router.patch("/", response_model=ClientProfileResponse)
async def update_client_profile(
    client_update: ClientUpdate,
    current_user: UserInDB = Depends(require_client),
    user_repo: UserRepository = Depends(UserRepository)
):
    update_data = client_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
        
    await user_repo.update(current_user)
    return ClientProfileResponse(**current_user.model_dump())

@router.get("/privacy-policy", response_model=LegalDocumentResponse)
async def get_client_privacy_policy():
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "privacy_policy"})
    if doc:
        u_at = doc.get("updated_at")
        u_str = u_at.isoformat() if hasattr(u_at, "isoformat") else str(u_at or datetime.now().isoformat())
        return LegalDocumentResponse(
            type="privacy_policy",
            title=doc.get("title", "Privacy Policy"),
            content=doc.get("content", ""),
            updated_at=u_str
        )
    return LegalDocumentResponse(
        type="privacy_policy",
        title="Privacy Policy",
        content="Our Privacy Policy is currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/terms-and-conditions", response_model=LegalDocumentResponse)
async def get_client_terms_and_conditions():
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "terms_and_conditions"})
    if doc:
        u_at = doc.get("updated_at")
        u_str = u_at.isoformat() if hasattr(u_at, "isoformat") else str(u_at or datetime.now().isoformat())
        return LegalDocumentResponse(
            type="terms_and_conditions",
            title=doc.get("title", "Terms & Conditions"),
            content=doc.get("content", ""),
            updated_at=u_str
        )
    return LegalDocumentResponse(
        type="terms_and_conditions",
        title="Terms & Conditions",
        content="Our Terms & Conditions are currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/notifications", response_model=NotificationListResponse)
async def get_client_notifications(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    return await service.get_user_notifications(user_id=current_user.id, recipient_type="client", page=page, limit=limit)

@router.patch("/notifications/{notification_id}/read")
async def mark_client_notification_read(
    notification_id: str,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    success = await service.mark_notification_as_read(notification_id=notification_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}

@router.delete("/notifications/{notification_id}")
async def delete_client_notification(
    notification_id: str,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    success = await service.delete_user_notification(notification_id=notification_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification deleted successfully"}


from app.api.worker_shift_utils import (
    resolve_shift_execution, calculate_cleaning_plan_progress, is_plan_active_on_date, get_or_create_shift_execution
)

@router.get("/shifts/{shift_id}/live-status", response_model=LiveStatusResponse, summary="Get Client Shift Live Status")
async def get_client_shift_live_status(
    shift_id: str,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    cleaner_name = "Assigned Cleaner"
    workers = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    if workers:
        cleaner_name = workers[0].get("name") or "Assigned Cleaner"
        if not workers[0].get("name") and workers[0].get("worker_id"):
            u_doc = await db["users"].find_one({"$or": [{"_id": workers[0]["worker_id"]}, {"id": workers[0]["worker_id"]}]})
            if u_doc:
                cleaner_name = u_doc.get("full_name") or u_doc.get("name") or cleaner_name

    rooms = shift_doc.get("rooms", [])
    live_rooms = []

    for r in rooms:
        r_tasks = r.get("tasks", [])
        live_tasks = []

        for idx, t in enumerate(r_tasks):
            if t.get("is_completed"):
                t_status = "DONE"
            elif idx == 0 or (idx > 0 and r_tasks[idx-1].get("is_completed")):
                t_status = "ACTIVE"
            else:
                t_status = "PENDING"

            c_at_str = None
            if t.get("completed_at"):
                c_at = t.get("completed_at")
                c_at_str = c_at.strftime("%I:%M %p") if isinstance(c_at, datetime) else str(c_at)

            live_tasks.append(LiveStatusTaskItem(
                id=str(t.get("id")),
                name=t.get("name", ""),
                status=t_status,
                completed_at=c_at_str
            ))

        completed_count = sum(1 for t in r_tasks if t.get("is_completed"))
        live_rooms.append(LiveStatusRoomItem(
            room_id=str(r.get("room_id")),
            room_name=r.get("room_name") or r.get("custom_room_name", "Room"),
            completed_tasks=completed_count,
            total_tasks=len(r_tasks),
            tasks=live_tasks
        ))

    total_rooms = len(rooms)
    completed_rooms = sum(1 for r in rooms if r.get("status") == "completed")
    overall_progress = round((completed_rooms / total_rooms) * 100.0, 1) if total_rooms > 0 else 0.0

    return LiveStatusResponse(
        shift_id=str(shift_doc.get("_id") or shift_doc.get("id")),
        client_name=shift_doc.get("client_name", "Client"),
        location_name=shift_doc.get("location_name", "Location"),
        status=shift_doc.get("status", "running"),
        overall_progress_percentage=overall_progress,
        assigned_cleaner_name=cleaner_name,
        arrival_time=shift_doc.get("start_time"),
        rooms=live_rooms
    )


@router.get("/overview", response_model=ClientOverviewResponse, summary="Get Client Dashboard Overview", description="Returns 100% dynamic Client Dashboard HomePage Overview data computed directly from MongoDB (Image Mockup).")
async def get_client_overview(
    current_user: UserInDB = Depends(require_client)
):
    """
    Get Client Dashboard HomePage Overview Endpoint.
    Computes today's overall hours/rooms progress, 3 middle metric cards, team on site live status, quick actions, and next visitors.
    """
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}, {"email": current_user.email}]})
    company_name = getattr(current_user, "company_name", None) or (c_doc.get("company_name") if c_doc else None) or getattr(current_user, "full_name", "Client")

    date_formatted = f"{now.strftime('%A, %d %B')} • Here's today's service at a glance."

    # Look for today's shift execution or cleaning plan for this client
    today_shift = await db["shift_executions"].find_one({
        "$or": [{"client_id": client_id}, {"client_ids": client_id}],
        "date": today_str,
        "status": {"$ne": "cancelled"}
    })

    if not today_shift:
        # Check active cleaning plans for today
        c_plan = await db["cleaning_plans"].find_one({
            "$or": [{"client_id": client_id}, {"client_ids": client_id}],
            "status": {"$ne": "cancelled"}
        })
        if c_plan and is_plan_active_on_date(c_plan, today_str):
            today_shift = await get_or_create_shift_execution(c_plan, today_str, db)

    if not today_shift:
        today_shift = await db["shifts"].find_one({
            "client_id": client_id,
            "date": today_str,
            "status": {"$ne": "cancelled"}
        })

    if not today_shift:
        today_shift = await db["shift_executions"].find_one({
            "$or": [{"client_id": client_id}, {"client_ids": client_id}],
            "status": {"$in": ["running", "in_progress", "published", "scheduled"]}
        }, sort=[("created_at", -1)])

    if not today_shift:
        today_shift = await db["shifts"].find_one({
            "client_id": client_id,
            "status": {"$in": ["running", "in_progress", "published"]}
        }, sort=[("created_at", -1)])

    hours_comp = 0.0
    tot_hours = 0.0
    rooms_comp = 0
    tot_rooms = 0
    status_badge = "No Service Today"
    loc_name = None
    if c_doc:
        loc_name = c_doc.get("location_name") or c_doc.get("company_name")
        if not loc_name and c_doc.get("locations"):
            locs = c_doc.get("locations")
            if isinstance(locs, list) and len(locs) > 0 and isinstance(locs[0], dict):
                loc_name = locs[0].get("name") or locs[0].get("location_name")
    loc_name = loc_name or (company_name + " Office")
    time_slot = "N/A"
    active_workers = []

    if today_shift:
        loc_name = today_shift.get("location_name") or loc_name or "Location"
        start_t_str = today_shift.get("start_time", "08:00")
        end_t_str = today_shift.get("end_time", "16:00")
        time_slot = f"{start_t_str}–{end_t_str}"
        s_status = today_shift.get("status", "published")

        if s_status == "completed":
            status_badge = "Completed"
        elif s_status in ["running", "in_progress", "published"]:
            status_badge = "Service in progress"
        else:
            status_badge = "Scheduled"

        rooms = today_shift.get("rooms", [])
        tot_rooms = len(rooms)
        for r in rooms:
            if r.get("status") == "completed" or (r.get("tasks") and all(t.get("is_completed") for t in r.get("tasks", []))):
                rooms_comp += 1

        try:
            st = datetime.strptime(start_t_str, "%H:%M")
            et = datetime.strptime(end_t_str, "%H:%M")
            tot_hours = round((et - st).total_seconds() / 3600.0, 1)
        except Exception:
            tot_hours = 0.0

        cur_hm = now.strftime("%H:%M")
        try:
            cur_dt = datetime.strptime(cur_hm, "%H:%M")
            st_dt = datetime.strptime(start_t_str, "%H:%M")
            et_dt = datetime.strptime(end_t_str, "%H:%M")

            if s_status == "completed" or cur_dt >= et_dt:
                hours_comp = tot_hours
            elif cur_dt <= st_dt:
                hours_comp = 0.0
            else:
                elapsed_sec = (cur_dt - st_dt).total_seconds()
                hours_comp = round(elapsed_sec / 3600.0, 1)
        except Exception:
            hours_comp = 0.0

        workers_list = today_shift.get("assigned_workers") or today_shift.get("workers", [])
        for idx, w in enumerate(workers_list):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name") or "Specialist"
            w_pic = w.get("profile_photo") or w.get("profile_picture")
            if (not w.get("name") or not w_pic) and w_id:
                u_doc = await db["users"].find_one({"$or": [{"_id": w_id}, {"id": w_id}]})
                if u_doc:
                    w_name = u_doc.get("full_name") or u_doc.get("name") or w_name
                    w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w_pic
            
            w_comp_h = int(hours_comp)
            w_comp_m = int(round((hours_comp - w_comp_h) * 60))
            w_worked_str = f"{w_comp_h}h {w_comp_m:02d}m" if hours_comp > 0 else "0h 00m"

            arr_t = w.get("checkin_time") or start_t_str

            active_workers.append(SpecialistOnSiteItem(
                worker_id=w_id,
                name=w_name,
                avatar=w_pic,
                role_title=w.get("shift_role") or ("Team lead" if idx == 0 else "Cleaning specialist"),
                arrived_time_str=f"arrived {arr_t}",
                time_worked_str=w_worked_str,
                percentage_assigned_time=round((hours_comp / tot_hours * 100.0), 1) if tot_hours > 0 else 0.0
            ))

    rem_hours = max(0.0, tot_hours - hours_comp)
    pct = round((rooms_comp / tot_rooms * 100.0), 1) if tot_rooms > 0 else (round((hours_comp / tot_hours * 100.0), 1) if tot_hours > 0 else 0.0)

    comp_h = int(hours_comp)
    comp_m = int(round((hours_comp - comp_h) * 60))
    hours_completed_str = f"{comp_h}h {comp_m:02d}m completed"

    rem_h = int(rem_hours)
    rem_m = int(round((rem_hours - rem_h) * 60))
    hours_remaining_str = f"{rem_h}h {rem_m:02d}m remaining"

    upcoming_shift = await db["shift_executions"].find_one({
        "$or": [{"client_id": client_id}, {"client_ids": client_id}],
        "date": {"$gt": today_str},
        "status": {"$ne": "cancelled"}
    }, sort=[("date", 1), ("start_time", 1)])

    if not upcoming_shift:
        c_plan = await db["cleaning_plans"].find_one({
            "$or": [{"client_id": client_id}, {"client_ids": client_id}],
            "status": {"$ne": "cancelled"}
        })
        if c_plan:
            tomorrow_dt = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            if is_plan_active_on_date(c_plan, tomorrow_dt):
                upcoming_shift = await get_or_create_shift_execution(c_plan, tomorrow_dt, db)

    if not upcoming_shift:
        upcoming_shift = await db["shifts"].find_one({
            "client_id": client_id,
            "date": {"$gt": today_str},
            "status": {"$ne": "cancelled"}
        }, sort=[("date", 1), ("start_time", 1)])

    next_time_str = "No upcoming visits scheduled"
    team_name = ""
    spec_count = 0
    team_avatars = []

    if upcoming_shift:
        u_date = upcoming_shift.get("date", "")
        u_time = upcoming_shift.get("start_time", "")
        next_time_str = f"{u_date}, {u_time}".strip(", ")
        w_list = upcoming_shift.get("workers", [])
        if w_list:
            spec_count = len(w_list)
            team_avatars = []
            for w in w_list:
                w_n = w.get("name", "Worker")
                initials = "".join([part[0] for part in w_n.split() if part]).upper()
                team_avatars.append(initials or "W")
            w0 = w_list[0]
            team_name = w0.get("team_name") or w0.get("worker_type") or "Team"

    last_shift = await db["shifts"].find_one({
        "client_id": client_id,
        "status": "completed"
    }, sort=[("date", -1)])

    last_worked_str = "0h 00m worked"
    last_sub_text = "No completed visits yet"
    if last_shift:
        last_date = last_shift.get("date", "")
        last_sub_text = f"{last_date} • completed"
        try:
            st = datetime.strptime(last_shift.get("start_time", "08:00"), "%H:%M")
            et = datetime.strptime(last_shift.get("end_time", "16:00"), "%H:%M")
            l_dur = round((et - st).total_seconds() / 3600.0, 2)
            lh = int(l_dur)
            lm = int(round((l_dur - lh) * 60))
            last_worked_str = f"{lh}h {lm:02d}m worked"
        except Exception:
            last_worked_str = "0h 00m worked"

    quick_actions = [
        QuickActionItem(id="act_1", title="Request a service", subtitle="Describe what you need", action_type="request_extra_service"),
        QuickActionItem(id="act_2", title="View full schedule", subtitle="See upcoming visits", action_type="view_schedule"),
        QuickActionItem(id="act_3", title="Contact Clean Ones", subtitle="Private or shared conversation", action_type="open_chat")
    ]

    return ClientOverviewResponse(
        greeting_name=company_name,
        current_date_str=date_formatted,
        todays_progress=TodaysOverallProgress(
            hours_completed=hours_comp,
            total_hours=tot_hours,
            hours_completed_str=hours_completed_str,
            hours_remaining_str=hours_remaining_str,
            rooms_completed=rooms_comp,
            total_rooms=tot_rooms,
            progress_percentage=pct,
            status_badge=status_badge,
            location_name=loc_name,
            service_time_slot=time_slot,
            tracking_note="Room progress is available because this location uses room tracking."
        ),
        metrics_grid=MetricsGrid(
            next_visit=NextVisitCard(
                time_str=next_time_str,
                team_name=team_name,
                specialists_count=spec_count
            ),
            on_site_now=OnSiteNowCard(
                specialists_count=len(active_workers),
                sub_text=f"{len(active_workers)} specialists active" if active_workers else "No specialists on site"
            ),
            last_completed=LastCompletedCard(
                worked_str=last_worked_str,
                sub_text=last_sub_text
            )
        ),
        live_status=LiveStatusSection(
            active_count=len(active_workers),
            specialists=active_workers
        ),
        quick_actions=quick_actions,
        next_visitors=NextVisitorItem(
            scheduled_time_str=next_time_str,
            team_name=team_name,
            specialists_count=spec_count,
            team_avatars=team_avatars,
            description=f"Service scheduled • approximately {tot_hours} hours" if tot_hours > 0 else "No scheduled visits"
        )
    )

from app.schemas.help import (
    FAQListResponse, FAQListItem, FAQDetailResponse,
    ClientSupportMessageRequest, ClientSupportMessageResponse,
    ClientReviewCreate, ClientReviewResponse
)

@router.get("/faq", response_model=FAQListResponse, summary="List FAQs for Clients")
async def get_client_faqs():
    db = get_database()
    raw = await db["faqs"].find({}).sort("serial_no", 1).to_list(length=100)
    items = []
    for idx, f in enumerate(raw, start=1):
        items.append(FAQListItem(
            serial_no=f.get("serial_no", idx),
            question=f.get("question", "")
        ))
    if not items:
        # Default helpful client FAQs
        default_faqs = [
            {"serial_no": 1, "question": "How do I request an extra cleaning service?", "answer": "Go to Extra Services in your client portal or tap 'Request a service' on your dashboard."},
            {"serial_no": 2, "question": "How can I monitor live cleaning progress?", "answer": "Open your Location Monitoring page to view real-time task progress and verified room photos."},
            {"serial_no": 3, "question": "How do I communicate with assigned cleaning specialists?", "answer": "Use the built-in Chat tab to message your cleaning team or manager directly."},
            {"serial_no": 4, "question": "How do I add special access instructions or keycard notes?", "answer": "Use the Notes tab on your client dashboard to record special instructions for your cleaner."},
            {"serial_no": 5, "question": "What happens if a cleaner is late or misses a shift?", "answer": "Our live operations team automatically detects delays and dispatches standby replacements."}
        ]
        for f in default_faqs:
            items.append(FAQListItem(serial_no=f["serial_no"], question=f["question"]))
    return FAQListResponse(faqs=items, total_count=len(items), page=1, limit=50, has_more=False)


@router.get("/faq/{serial_no}", response_model=FAQDetailResponse, summary="Get Single Client FAQ with Answer")
async def get_client_faq_detail(serial_no: int):
    db = get_database()
    faq = await db["faqs"].find_one({"serial_no": serial_no})
    if faq:
        return FAQDetailResponse(
            serial_no=faq.get("serial_no", serial_no),
            question=faq.get("question", ""),
            answer=faq.get("answer", "")
        )
    # Default lookup
    default_answers = {
        1: ("How do I request an extra cleaning service?", "Go to Extra Services in your client portal or tap 'Request a service' on your dashboard."),
        2: ("How can I monitor live cleaning progress?", "Open your Location Monitoring page to view real-time task progress and verified room photos."),
        3: ("How do I communicate with assigned cleaning specialists?", "Use the built-in Chat tab to message your cleaning team or manager directly."),
        4: ("How do I add special access instructions or keycard notes?", "Use the Notes tab on your client dashboard to record special instructions for your cleaner."),
        5: ("What happens if a cleaner is late or misses a shift?", "Our live operations team automatically detects delays and dispatches standby replacements.")
    }
    if serial_no in default_answers:
        q, a = default_answers[serial_no]
        return FAQDetailResponse(serial_no=serial_no, question=q, answer=a)
    raise HTTPException(status_code=404, detail="FAQ not found")

@router.post("/support/messages", response_model=ClientSupportMessageResponse, status_code=status.HTTP_201_CREATED, summary="Submit Client Support Inquiry")
async def submit_client_support_message(
    msg_in: ClientSupportMessageRequest,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    sup_id = f"sup_{uuid.uuid4().hex[:10]}"
    cid = str(current_user.id)

    doc = {
        "_id": sup_id,
        "id": sup_id,
        "client_id": cid,
        "client_name": current_user.full_name,
        "client_email": getattr(current_user, "email", "client@cleaningone.com"),
        "subject": msg_in.subject,
        "category": msg_in.category or "general",
        "description": msg_in.description,
        "status": "pending",
        "admin_reply": None,
        "created_at": now,
        "updated_at": now
    }
    await db["support_messages"].insert_one(doc)

    notif_service = NotificationService()
    await notif_service.create_notification(
        title=f"New Client Support Ticket: {msg_in.subject}",
        message=f"{current_user.full_name} sent an inquiry: {msg_in.description[:100]}",
        notification_type="support",
        recipient_type="manager"
    )

    return ClientSupportMessageResponse(
        id=sup_id,
        client_id=cid,
        client_name=current_user.full_name,
        client_email=getattr(current_user, "email", "client@cleaningone.com"),
        subject=msg_in.subject,
        category=msg_in.category or "general",
        description=msg_in.description,
        status="pending",
        admin_reply=None,
        created_at=now
    )

@router.get("/support/messages", summary="List Client Support Inquiries")
async def get_client_support_messages(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    cid = str(current_user.id)
    raw = await db["support_messages"].find({"client_id": cid}).sort("created_at", -1).to_list(length=100)
    for r in raw:
        r["id"] = str(r.get("_id") or r.get("id"))
    return {"total_count": len(raw), "messages": raw}

@router.post("/reviews", response_model=ClientReviewResponse, status_code=status.HTTP_201_CREATED, summary="Submit Client Review / Rating")
async def submit_client_review(
    rev_in: ClientReviewCreate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    rev_id = f"rev_{uuid.uuid4().hex[:10]}"
    cid = str(current_user.id)

    doc = {
        "_id": rev_id,
        "id": rev_id,
        "client_id": cid,
        "client_name": current_user.full_name,
        "shift_id": rev_in.shift_id,
        "cleaner_name": rev_in.cleaner_name,
        "rating": rev_in.rating,
        "quality_score": rev_in.quality_score or 5,
        "punctuality_score": rev_in.punctuality_score or 5,
        "review_text": rev_in.review_text,
        "created_at": now,
        "updated_at": now
    }
    await db["client_reviews"].insert_one(doc)

    return ClientReviewResponse(
        id=rev_id,
        client_id=cid,
        client_name=current_user.full_name,
        shift_id=rev_in.shift_id,
        cleaner_name=rev_in.cleaner_name,
        rating=rev_in.rating,
        quality_score=rev_in.quality_score or 5,
        punctuality_score=rev_in.punctuality_score or 5,
        review_text=rev_in.review_text,
        created_at=now
    )

@router.get("/reviews", summary="List Client Reviews")
async def get_client_reviews(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    cid = str(current_user.id)
    raw = await db["client_reviews"].find({"client_id": cid}).sort("created_at", -1).to_list(length=100)
    for r in raw:
        r["id"] = str(r.get("_id") or r.get("id"))
    return {"total_count": len(raw), "reviews": raw}
