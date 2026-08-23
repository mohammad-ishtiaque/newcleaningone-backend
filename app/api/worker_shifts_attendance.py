import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift_monitoring import (
    WorkerCheckInResponse, WorkerCheckOutResponse,
    WorkerAttendanceToggleResponse, WorkerShiftStateResponse
)
from app.api.worker_shift_utils import (
    resolve_shift_execution, evaluate_worker_attendance_status
)

attendance_router = APIRouter()

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@attendance_router.post(
    "/{shift_id}/check-in",
    response_model=WorkerCheckInResponse,
    summary="Worker Shift Check-in",
    description="Allows an assigned worker to check in to a shift. Evaluates on-time status with 15-minute grace period."
)
async def worker_shift_check_in(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    target_idx = next((idx for idx, w in enumerate(workers_list) if str(w.get("worker_id")) == worker_id), None)
    if target_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = workers_list[target_idx]

    # If already checked in
    if w_record.get("checkin_time"):
        c_time = w_record["checkin_time"]
        if isinstance(c_time, str):
            c_time = datetime.fromisoformat(c_time)
        if c_time and hasattr(c_time, "tzinfo") and c_time.tzinfo is None:
            c_time = c_time.replace(tzinfo=timezone.utc)
        return WorkerCheckInResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            checkin_time=c_time,
            status=w_record.get("status") or "ontime"
        )

    # Evaluate ontime vs late using 15-minute grace period
    attendance_status = evaluate_worker_attendance_status(
        plan_doc=shift_doc,
        worker_record={"checkin_time": now},
        now_utc=now
    )

    workers_list[target_idx]["checkin_time"] = now
    workers_list[target_idx]["status"] = attendance_status

    # Automatically activate the first room if no room is yet started
    rooms = shift_doc.get("rooms", [])
    has_started_room = any(r.get("status") in ["in_progress", "completed", "photo_submitted"] for r in rooms)
    if rooms and not has_started_room:
        rooms[0]["status"] = "in_progress"
        rooms[0]["started_at"] = now

    field_name = "assigned_workers" if "assigned_workers" in shift_doc else "workers"
    update_fields = {
        field_name: workers_list,
        "rooms": rooms,
        "checkin_time": now,
        "status": "in_progress",
        "updated_at": now
    }

    doc_id = shift_doc.get("_id")
    await db[coll_name].update_one({"_id": doc_id}, {"$set": update_fields})

    # Broadcast real-time attendance change to Client, Manager & Workers
    try:
        from app.services.shift_ws_service import broadcast_shift_attendance_event
        await broadcast_shift_attendance_event(
            db=db,
            shift_doc=shift_doc,
            worker_id=worker_id,
            action="check_in",
            state="checked_in",
            checkin_time=now,
            status_label=attendance_status
        )
    except Exception:
        pass

    return WorkerCheckInResponse(
        shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
        worker_id=worker_id,
        checkin_time=now,
        status=attendance_status
    )


@attendance_router.post(
    "/{shift_id}/check-out",
    response_model=WorkerCheckOutResponse,
    summary="Worker Shift Check-out",
    description="Allows a worker to check out of an assigned shift and calculates total hours worked."
)
async def worker_shift_check_out(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    target_idx = next((idx for idx, w in enumerate(workers_list) if str(w.get("worker_id")) == worker_id), None)
    if target_idx is None:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    now = datetime.now(timezone.utc)
    w_record = workers_list[target_idx]

    c_time = w_record.get("checkin_time")
    if not c_time:
        raise HTTPException(status_code=400, detail="Worker must check in before checking out")

    if w_record.get("checkout_time"):
        co_time = w_record["checkout_time"]
        if isinstance(co_time, str):
            co_time = datetime.fromisoformat(co_time)
        if co_time and hasattr(co_time, "tzinfo") and co_time.tzinfo is None:
            co_time = co_time.replace(tzinfo=timezone.utc)
        return WorkerCheckOutResponse(
            shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
            worker_id=worker_id,
            checkout_time=co_time,
            hours_worked=w_record.get("hours_worked") or 0.0
        )

    checkin_dt = c_time
    if isinstance(checkin_dt, str):
        checkin_dt = datetime.fromisoformat(checkin_dt)
    if checkin_dt and checkin_dt.tzinfo is None:
        checkin_dt = checkin_dt.replace(tzinfo=timezone.utc)

    duration_seconds = (now - checkin_dt).total_seconds() if checkin_dt else 0.0
    hours_worked = round(max(0.0, duration_seconds / 3600.0), 2)

    workers_list[target_idx]["checkout_time"] = now
    workers_list[target_idx]["hours_worked"] = hours_worked

    field_name = "assigned_workers" if "assigned_workers" in shift_doc else "workers"
    update_fields = {
        field_name: workers_list,
        "updated_at": now
    }

    # If all assigned workers have checked out
    if all(w.get("checkout_time") for w in workers_list):
        update_fields["status"] = "completed"

    doc_id = shift_doc.get("_id")
    await db[coll_name].update_one({"_id": doc_id}, {"$set": update_fields})

    # Broadcast real-time checkout change to Client, Manager & Workers
    try:
        from app.services.shift_ws_service import broadcast_shift_attendance_event
        await broadcast_shift_attendance_event(
            db=db,
            shift_doc=shift_doc,
            worker_id=worker_id,
            action="check_out",
            state="completed",
            checkin_time=checkin_dt,
            checkout_time=now,
            hours_worked=hours_worked,
            status_label="completed"
        )
    except Exception:
        pass

    return WorkerCheckOutResponse(
        shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
        worker_id=worker_id,
        checkout_time=now,
        hours_worked=hours_worked
    )


@attendance_router.get(
    "/{shift_id}/state",
    response_model=WorkerShiftStateResponse,
    summary="Get Worker Shift Attendance State",
    description="Returns the current attendance state ('not_checked_in', 'checked_in', 'completed') of the worker."
)
async def get_worker_shift_state(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, _ = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    target_worker = next((w for w in workers_list if str(w.get("worker_id")) == worker_id), None)
    if not target_worker:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    c_time = target_worker.get("checkin_time")
    co_time = target_worker.get("checkout_time")

    if isinstance(c_time, str):
        c_time = datetime.fromisoformat(c_time)
    if isinstance(co_time, str):
        co_time = datetime.fromisoformat(co_time)

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
        status=target_worker.get("status"),
        hours_worked=target_worker.get("hours_worked"),
        is_auto_checked_out=target_worker.get("is_auto_checked_out", False)
    )


# ============================================================================
# Hidden / Deprecated Endpoints (Preserved for backwards compatibility)
# ============================================================================

@attendance_router.post("/{shift_id}/attendance", response_model=WorkerAttendanceToggleResponse, include_in_schema=False)
async def toggle_worker_shift_attendance(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    db = get_database()
    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    workers_list = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    target_worker = next((w for w in workers_list if str(w.get("worker_id")) == worker_id), None)
    if not target_worker:
        raise HTTPException(status_code=403, detail="Worker is not assigned to this shift")

    if not target_worker.get("checkin_time"):
        in_res = await worker_shift_check_in(shift_id, current_user)
        return WorkerAttendanceToggleResponse(
            shift_id=in_res.shift_id,
            worker_id=in_res.worker_id,
            action="check_in",
            state="checked_in",
            checkin_time=in_res.checkin_time,
            checkout_time=None,
            status=in_res.status,
            hours_worked=None,
            is_auto_checked_out=False,
            message="Check-in successful"
        )
    else:
        out_res = await worker_shift_check_out(shift_id, current_user)
        return WorkerAttendanceToggleResponse(
            shift_id=out_res.shift_id,
            worker_id=out_res.worker_id,
            action="check_out",
            state="completed",
            checkin_time=target_worker.get("checkin_time"),
            checkout_time=out_res.checkout_time,
            status=target_worker.get("status"),
            hours_worked=out_res.hours_worked,
            is_auto_checked_out=False,
            message="Check-out successful"
        )
