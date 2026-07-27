from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, status, HTTPException
from app.schemas.user import ClientSignup, ClientUpdate, ClientProfileResponse
from app.schemas.help import LegalDocumentResponse
from app.schemas.notification import NotificationListResponse
from app.schemas.client_overview import (
    ClientOverviewResponse, TodaysOverallProgress, MetricsGrid, NextVisitCard, OnSiteNowCard, LastCompletedCard,
    LiveStatusSection, SpecialistOnSiteItem, NextVisitorItem
)
from app.schemas.shift import LiveStatusResponse, LiveStatusRoomItem, LiveStatusTaskItem
from app.services.user_service import UserService
from app.services.notification_service import NotificationService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.core.database import get_database

router = APIRouter(prefix="/client", tags=["Client"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user

@router.post("/signup", response_model=ClientProfileResponse, status_code=status.HTTP_201_CREATED)
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
        return LegalDocumentResponse(
            title=doc.get("title", "Privacy Policy"),
            content=doc.get("content", ""),
            updated_at=doc.get("updated_at", datetime.now().isoformat())
        )
    return LegalDocumentResponse(
        title="Privacy Policy",
        content="Our Privacy Policy is currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/terms-and-conditions", response_model=LegalDocumentResponse)
async def get_client_terms_and_conditions():
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "terms_and_conditions"})
    if doc:
        return LegalDocumentResponse(
            title=doc.get("title", "Terms & Conditions"),
            content=doc.get("content", ""),
            updated_at=doc.get("updated_at", datetime.now().isoformat())
        )
    return LegalDocumentResponse(
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


@router.get("/shifts/{shift_id}/live-status", response_model=LiveStatusResponse, summary="Get Client Shift Live Status")
async def get_client_shift_live_status(
    shift_id: str,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    cleaner_name = "Assigned Cleaner"
    workers = shift_doc.get("workers", [])
    if workers:
        cleaner_name = workers[0].get("name", "Assigned Cleaner")

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


@router.get("/live-status", response_model=Optional[LiveStatusResponse], summary="Get Latest Active Live Status for Client Portal")
async def get_latest_client_live_status(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({
        "client_id": client_id,
        "status": {"$in": ["running", "published"]}
    }, sort=[("created_at", -1)])

    if not shift_doc:
        shift_doc = await db["shifts"].find_one({"client_id": client_id}, sort=[("created_at", -1)])

    if not shift_doc:
        return None

    return await get_client_shift_live_status(shift_id=str(shift_doc.get("_id") or shift_doc.get("id")), current_user=current_user)


@router.get("/overview", response_model=ClientOverviewResponse, summary="Get Client Dashboard Overview")
async def get_client_overview(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}, {"email": current_user.email}]})
    company_name = getattr(current_user, "company_name", None) or (c_doc.get("company_name") if c_doc else None) or getattr(current_user, "full_name", "Client")

    date_formatted = now.strftime("%A, %d %B")

    today_shift = await db["shifts"].find_one({
        "client_id": client_id,
        "date": today_str,
        "status": {"$ne": "cancelled"}
    })

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
    loc_name = loc_name or "Main office"
    time_slot = "08:00 - 15:30"
    active_workers = []

    if today_shift:
        loc_name = today_shift.get("location_name") or loc_name or "Main office"
        start_t_str = today_shift.get("start_time", "08:00")
        end_t_str = today_shift.get("end_time", "15:30")
        time_slot = f"{start_t_str} - {end_t_str}"
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
            tot_hours = round((et - st).total_seconds() / 3600.0, 2)
        except Exception:
            tot_hours = 8.0

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
                hours_comp = round(elapsed_sec / 3600.0, 2)
        except Exception:
            hours_comp = round(tot_hours * 0.6, 2)

        workers_list = today_shift.get("workers", [])
        for idx, w in enumerate(workers_list):
            w_id = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name", "Specialist")
            w_pic = w.get("profile_picture")
            
            w_comp_h = int(hours_comp)
            w_comp_m = int(round((hours_comp - w_comp_h) * 60))
            w_worked_str = f"{w_comp_h}h {w_comp_m:02d}m" if hours_comp > 0 else "0h 00m"

            active_workers.append(SpecialistOnSiteItem(
                worker_id=w_id,
                name=w_name,
                avatar=w_pic,
                role_title=w.get("shift_role") or ("Team lead" if idx == 0 else "Cleaning specialist"),
                arrived_time_str=f"arrived {start_t_str}",
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

    upcoming_shift = await db["shifts"].find_one({
        "client_id": client_id,
        "date": {"$gt": today_str},
        "status": {"$ne": "cancelled"}
    }, sort=[("date", 1), ("start_time", 1)])

    next_time_str = "No upcoming visits"
    team_name = "Service Team"
    spec_count = 0
    team_avatars = []

    if upcoming_shift:
        u_date = upcoming_shift.get("date", "")
        u_time = upcoming_shift.get("start_time", "09:00")
        next_time_str = f"{u_date}, {u_time}"
        w_list = upcoming_shift.get("workers", [])
        spec_count = len(w_list)
        for w in w_list:
            w_n = w.get("name", "Worker")
            initials = "".join([part[0] for part in w_n.split() if part]).upper()
            team_avatars.append(initials or "W")

    last_shift = await db["shifts"].find_one({
        "client_id": client_id,
        "status": "completed"
    }, sort=[("date", -1)])

    last_worked_str = "0h 00m worked"
    last_sub_text = "No completed shifts"
    if last_shift:
        last_date = last_shift.get("date", "Previous Visit")
        last_sub_text = f"{last_date} • data available"
        try:
            st = datetime.strptime(last_shift.get("start_time", "08:00"), "%H:%M")
            et = datetime.strptime(last_shift.get("end_time", "15:30"), "%H:%M")
            l_dur = round((et - st).total_seconds() / 3600.0, 2)
            lh = int(l_dur)
            lm = int(round((l_dur - lh) * 60))
            last_worked_str = f"{lh}h {lm:02d}m worked"
        except Exception:
            last_worked_str = "7h 18m worked"

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
                sub_text=f"{len(active_workers)} currently active" if active_workers else "No specialists on site"
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
        next_visitors=NextVisitorItem(
            scheduled_time_str=next_time_str,
            team_name=team_name,
            specialists_count=spec_count,
            team_avatars=team_avatars or ["W"],
            description=f"Regular cleaning • approximately {tot_hours} hours" if tot_hours > 0 else "Regular cleaning scheduled"
        )
    )
