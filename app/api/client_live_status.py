from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import (
    ClientLiveStatusResponse, ClientLiveRoomProgress, ClientLiveTaskItem,
    AssignedCleanerCard, CurrentLocationCard, ArrivalTimeCard, ShiftResponse
)

router = APIRouter(prefix="/client/live-status", tags=["Client Live Status"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


def _build_client_live_status(shift_doc: dict, loc_address: Optional[str] = None, cleaner_doc: Optional[dict] = None) -> ClientLiveStatusResponse:
    shift_id = str(shift_doc.get("_id") or shift_doc.get("id"))
    client_name = shift_doc.get("client_name", "Client")
    location_name = shift_doc.get("location_name", "Location")

    # Cleaner Info
    cleaner_name = "Sarah Mitchell"
    cleaner_id = "cleaner_default"
    cleaner_type = "Team Lead - Alpha"
    cleaner_pic = None

    workers = shift_doc.get("workers", [])
    if workers:
        w0 = workers[0]
        cleaner_id = str(w0.get("worker_id", "cleaner_1"))
        cleaner_name = w0.get("name", cleaner_name)
        cleaner_pic = w0.get("profile_picture")
        cleaner_type = w0.get("worker_type", "Team Lead - Alpha")

    if cleaner_doc:
        cleaner_name = cleaner_doc.get("full_name", cleaner_name)
        cleaner_pic = cleaner_doc.get("profile_photo", cleaner_pic)
        w_t = cleaner_doc.get("worker_type") or cleaner_doc.get("onboarding_draft", {}).get("worker_type", cleaner_type)
        cleaner_type = str(w_t)

    assigned_cleaner = AssignedCleanerCard(
        worker_id=cleaner_id,
        name=cleaner_name,
        designation=cleaner_type,
        profile_picture=cleaner_pic
    )

    current_location = CurrentLocationCard(
        location_id=str(shift_doc.get("location_id", "")),
        location_name=location_name,
        address_subtitle=loc_address or "Main Office Suite"
    )

    # Arrival time
    start_time_str = shift_doc.get("start_time", "08:55 AM")
    arrival_status = "On time"
    date_val = shift_doc.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    try:
        y, m, d = map(int, date_val.split("-"))
        dt_obj = datetime(y, m, d)
        date_formatted = dt_obj.strftime("%b %d, %Y")
    except Exception:
        date_formatted = date_val

    arrival_time_info = ArrivalTimeCard(
        arrival_time=start_time_str,
        arrival_status=arrival_status,
        date_str=date_formatted
    )

    # Rooms and Task checklist
    rooms = shift_doc.get("rooms", [])
    all_rooms_progress = []
    active_room_progress = None
    active_room_name = "Room 204"

    total_all_tasks = 0
    completed_all_tasks = 0

    for r_idx, r in enumerate(rooms):
        r_name = r.get("room_name") or r.get("custom_room_name", f"Room {r_idx+1}")
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
                    t_time_str = c_at.strftime("%I:%M %p")
                elif c_at:
                    t_time_str = str(c_at)
                else:
                    t_time_str = "Completed"
            elif not found_active_for_room and (idx == 0 or (idx > 0 and r_tasks[idx-1].get("is_completed"))):
                t_status = "ACTIVE"
                t_time_str = "10:09 AM (Est.)"
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
            room_id=str(r.get("room_id")),
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
        overall_pct = 0.0

    active_location_text = f"Active on {location_name} • {active_room_name}"
    end_time_str = shift_doc.get("end_time", "12:00 PM")

    return ClientLiveStatusResponse(
        shift_id=shift_id,
        status_label="CLEANING IN PROGRESS" if shift_doc.get("status") in ["running", "published"] else str(shift_doc.get("status", "CLEANING IN PROGRESS")).upper(),
        active_room_location_text=active_location_text,
        overall_progress_percentage=overall_pct,
        est_completion_time=f"Est. completion: {end_time_str}",
        assigned_cleaner=assigned_cleaner,
        current_location=current_location,
        arrival_time_info=arrival_time_info,
        current_active_room=active_room_progress,
        all_rooms_progress=all_rooms_progress
    )


@router.get("", response_model=ClientLiveStatusResponse, summary="Get Active Client Live Status Dashboard")
async def get_client_live_status_dashboard(
    shift_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)

    if shift_id:
        shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    else:
        shift_doc = await db["shifts"].find_one({
            "client_id": client_id,
            "status": {"$in": ["running", "in_progress", "published"]}
        }, sort=[("created_at", -1)])

    if not shift_doc:
        shift_doc = await db["shifts"].find_one({"client_id": client_id}, sort=[("created_at", -1)])

    if not shift_doc:
        raise HTTPException(status_code=404, detail="No active or recent cleaning session found for client")

    loc_address = None
    c_query = {"_id": ObjectId(client_id)} if ObjectId.is_valid(client_id) else {"_id": client_id}
    c_doc = await db["client_list"].find_one(c_query)
    if c_doc and "locations" in c_doc:
        target_loc_id = shift_doc.get("location_id")
        target_loc = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(target_loc_id)), None)
        if target_loc:
            loc_address = target_loc.get("address")

    cleaner_doc = None
    workers = shift_doc.get("workers", [])
    if workers:
        w_id = workers[0].get("worker_id")
        w_query = {"_id": ObjectId(w_id)} if ObjectId.is_valid(w_id) else {"_id": w_id}
        cleaner_doc = await db["users"].find_one(w_query)

    return _build_client_live_status(shift_doc, loc_address=loc_address, cleaner_doc=cleaner_doc)


@router.get("/shifts", response_model=List[ShiftResponse], summary="List Client Cleaning Sessions for Live Status Selection")
async def list_client_live_shifts(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)

    cursor = db["shifts"].find({"client_id": client_id}).sort("created_at", -1)
    raw_shifts = await cursor.to_list(length=100)

    res = []
    for s in raw_shifts:
        s["id"] = str(s.get("_id") or s.get("id"))
        res.append(ShiftResponse(**s))

    return res


@router.get("/shifts/{shift_id}", response_model=ClientLiveStatusResponse, summary="Get Live Status for Specific Shift")
async def get_client_live_status_by_shift(
    shift_id: str,
    current_user: UserInDB = Depends(require_client)
):
    return await get_client_live_status_dashboard(shift_id=shift_id, current_user=current_user)
