import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift import ShiftResponse
from app.schemas.shift_monitoring import (
    WorkerCheckInResponse, WorkerCheckOutResponse,
    WorkerAttendanceToggleResponse, WorkerShiftStateResponse
)

worker_shift_router = APIRouter(prefix="/worker/shifts", tags=["Workers Shift Management"])

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _eval_auto_checkout(shift_doc: dict, w_record: dict, now: datetime) -> dict:
    """Helper to check if a worker checkin needs auto checkout at 23:59:59 of shift date."""
    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if c_time and not co_time:
        if isinstance(c_time, str):
            c_time = datetime.fromisoformat(c_time)
        if c_time and c_time.tzinfo is None:
            c_time = c_time.replace(tzinfo=timezone.utc)

        shift_date_str = shift_doc.get("date")
        try:
            sy, smon, sd = map(int, shift_date_str.split("-"))
            auto_co_dt = datetime(sy, smon, sd, 23, 59, 59, tzinfo=timezone.utc)

            if now > auto_co_dt and c_time:
                duration_seconds = (auto_co_dt - c_time).total_seconds()
                h_worked = round(max(0.0, duration_seconds / 3600.0), 2)
                w_record["checkout_time"] = auto_co_dt
                w_record["hours_worked"] = h_worked
                w_record["is_auto_checked_out"] = True
        except Exception:
            pass

    return w_record


@worker_shift_router.get(
    "/my-shifts",
    response_model=List[ShiftResponse],
    summary="Get Worker Assigned Shifts",
    description="Returns all shifts assigned to the current logged-in worker."
)
async def get_my_assigned_shifts(
    status_val: Optional[str] = None,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    query = {"workers.worker_id": worker_id}
    if status_val:
        query["status"] = status_val

    cursor = db["shifts"].find(query).sort("created_at", -1)
    raw_shifts = await cursor.to_list(length=200)

    shifts_res = []
    for s in raw_shifts:
        s["id"] = str(s.get("_id") or s.get("id"))
        shifts_res.append(ShiftResponse(**s))

    return shifts_res


@worker_shift_router.get(
    "/{shift_id}/state",
    response_model=WorkerShiftStateResponse,
    summary="Get Worker Shift Attendance State",
    description="Returns the current attendance state ('not_checked_in', 'checked_in', 'completed') of the worker for a specific shift."
)
async def get_worker_shift_state(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    w_record = None
    w_idx = None
    for idx, w in enumerate(workers_list):
        if str(w.get("worker_id")) == worker_id:
            w_record = w
            w_idx = idx
            break

    if w_record is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = _eval_auto_checkout(shift_doc, w_record, now)

    if w_record.get("is_auto_checked_out"):
        workers_list[w_idx] = w_record
        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if not c_time:
        state_label = "not_checked_in"
    elif c_time and not co_time:
        state_label = "checked_in"
    else:
        state_label = "completed"

    return WorkerShiftStateResponse(
        shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
        worker_id=worker_id,
        state=state_label,
        checkin_time=c_time,
        checkout_time=co_time,
        status=w_record.get("status"),
        hours_worked=w_record.get("hours_worked"),
        is_auto_checked_out=w_record.get("is_auto_checked_out", False)
    )


@worker_shift_router.post(
    "/{shift_id}/attendance",
    response_model=WorkerAttendanceToggleResponse,
    summary="Toggle Worker Shift Attendance (Combined Check-in & Check-out)",
    description="Single combined endpoint for worker attendance. Automatically executes Check-in if not checked in, or Check-out if currently checked in."
)
async def toggle_worker_shift_attendance(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    shift_doc = await db["shifts"].find_one({"$or": [{"_id": shift_id}, {"id": shift_id}]})
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("workers", [])
    target_worker_idx = None
    for idx, w in enumerate(workers_list):
        if str(w.get("worker_id")) == worker_id:
            target_worker_idx = idx
            break

    if target_worker_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = workers_list[target_worker_idx]

    w_record = _eval_auto_checkout(shift_doc, w_record, now)
    workers_list[target_worker_idx] = w_record

    c_time = w_record.get("checkin_time")
    co_time = w_record.get("checkout_time")

    if not c_time:
        shift_date_str = shift_doc.get("date")
        start_time_str = shift_doc.get("start_time")

        attendance_status = "ontime"
        try:
            start_hour, start_min = map(int, start_time_str.split(":"))
            shift_year, shift_month, shift_day = map(int, shift_date_str.split("-"))
            shift_start_dt = datetime(shift_year, shift_month, shift_day, start_hour, start_min, tzinfo=timezone.utc)

            if now > shift_start_dt:
                attendance_status = "late"
            else:
                attendance_status = "ontime"
        except Exception:
            attendance_status = "ontime"

        workers_list[target_worker_idx]["checkin_time"] = now
        workers_list[target_worker_idx]["status"] = attendance_status

        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="check_in",
            state="checked_in",
            checkin_time=now,
            checkout_time=None,
            status=attendance_status,
            hours_worked=None,
            is_auto_checked_out=False,
            message="Check-in successful"
        )

    elif c_time and not co_time:
        checkin_dt = c_time
        if isinstance(checkin_dt, str):
            checkin_dt = datetime.fromisoformat(checkin_dt)
        if checkin_dt and checkin_dt.tzinfo is None:
            checkin_dt = checkin_dt.replace(tzinfo=timezone.utc)

        duration_seconds = (now - checkin_dt).total_seconds() if checkin_dt else 0.0
        hours_worked = round(max(0.0, duration_seconds / 3600.0), 2)

        workers_list[target_worker_idx]["checkout_time"] = now
        workers_list[target_worker_idx]["hours_worked"] = hours_worked

        await db["shifts"].update_one(
            {"$or": [{"_id": shift_id}, {"id": shift_id}]},
            {"$set": {"workers": workers_list, "updated_at": now}}
        )

        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="check_out",
            state="completed",
            checkin_time=checkin_dt,
            checkout_time=now,
            status=w_record.get("status"),
            hours_worked=hours_worked,
            is_auto_checked_out=False,
            message="Check-out successful"
        )

    else:
        return WorkerAttendanceToggleResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            action="already_completed",
            state="completed",
            checkin_time=c_time,
            checkout_time=co_time,
            status=w_record.get("status"),
            hours_worked=w_record.get("hours_worked"),
            is_auto_checked_out=w_record.get("is_auto_checked_out", False),
            message="Worker has already completed attendance for this shift"
        )


@worker_shift_router.post(
    "/{shift_id}/check-in",
    response_model=WorkerCheckInResponse,
    summary="Worker Shift Check-in (Backward Compatible)",
    description="Allows a worker to check in to an assigned shift."
)
async def worker_shift_check_in(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    res = await toggle_worker_shift_attendance(shift_id, current_user)
    return WorkerCheckInResponse(
        shift_id=res.shift_id,
        worker_id=res.worker_id,
        checkin_time=res.checkin_time or datetime.now(timezone.utc),
        status=res.status or "ontime"
    )


@worker_shift_router.post(
    "/{shift_id}/check-out",
    response_model=WorkerCheckOutResponse,
    summary="Worker Shift Check-out (Backward Compatible)",
    description="Allows a worker to check out of an assigned shift."
)
async def worker_shift_check_out(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    res = await toggle_worker_shift_attendance(shift_id, current_user)
    return WorkerCheckOutResponse(
        shift_id=res.shift_id,
        worker_id=res.worker_id,
        checkout_time=res.checkout_time or datetime.now(timezone.utc),
        hours_worked=res.hours_worked or 0.0
    )
