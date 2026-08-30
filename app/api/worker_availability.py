import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_modules import (
    DayAvailability, LeaveRequestItem, WorkerAvailabilityResponse,
    WorkerAvailabilityUpdateRequest, LeaveRequestCreate
)

router = APIRouter(prefix="/worker/availability", tags=["Worker Availability Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


DEFAULT_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@router.get(
    "",
    response_model=WorkerAvailabilityResponse,
    summary="Get Worker Weekly Availability & Leave Requests",
    description="Returns worker's recurring 7-day availability schedule, preferred weekly working hours, and submitted leave requests."
)
async def get_worker_availability(
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    doc = await db["worker_availability"].find_one({"worker_id": worker_id})
    if not doc:
        weekly = [
            DayAvailability(day=d, is_available=d not in ["sunday"], start_time="08:00 AM", end_time="05:00 PM")
            for d in DEFAULT_DAYS
        ]
        return WorkerAvailabilityResponse(
            worker_id=worker_id,
            weekly_availability=weekly,
            preferred_hours_per_week=40,
            leave_requests=[]
        )

    weekly_data = doc.get("weekly_availability", [])
    weekly_items = [DayAvailability(**item) for item in weekly_data] if weekly_data else [
        DayAvailability(day=d, is_available=d not in ["sunday"], start_time="08:00 AM", end_time="05:00 PM")
        for d in DEFAULT_DAYS
    ]

    leaves_data = doc.get("leave_requests", [])
    leave_items = [LeaveRequestItem(**l) for l in leaves_data] if leaves_data else []

    return WorkerAvailabilityResponse(
        worker_id=worker_id,
        weekly_availability=weekly_items,
        preferred_hours_per_week=int(doc.get("preferred_hours_per_week", 40)),
        leave_requests=leave_items
    )


@router.put(
    "",
    response_model=WorkerAvailabilityResponse,
    summary="Update Worker Weekly Availability",
    description="Updates worker's weekly day-by-day availability slots and preferred weekly hours."
)
async def update_worker_availability(
    update_in: WorkerAvailabilityUpdateRequest,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    update_fields = {"updated_at": datetime.now(timezone.utc)}
    if update_in.weekly_availability is not None:
        update_fields["weekly_availability"] = [d.model_dump() for d in update_in.weekly_availability]
    if update_in.preferred_hours_per_week is not None:
        update_fields["preferred_hours_per_week"] = update_in.preferred_hours_per_week

    await db["worker_availability"].update_one(
        {"worker_id": worker_id},
        {"$set": update_fields, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True
    )

    return await get_worker_availability(current_user=current_user)


@router.post(
    "/leave",
    response_model=LeaveRequestItem,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Worker Leave / Time-Off Request",
    description="Submits a date range time-off or vacation leave request for manager approval."
)
async def submit_leave_request(
    leave_in: LeaveRequestCreate,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    req_id = f"leave_{uuid.uuid4().hex[:8]}"
    leave_item = LeaveRequestItem(
        id=req_id,
        start_date=leave_in.start_date,
        end_date=leave_in.end_date,
        reason=leave_in.reason,
        status="pending",
        created_at=datetime.now(timezone.utc)
    )

    await db["worker_availability"].update_one(
        {"worker_id": worker_id},
        {"$push": {"leave_requests": leave_item.model_dump()}},
        upsert=True
    )

    return leave_item
