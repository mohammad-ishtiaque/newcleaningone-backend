import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import resolve_hourly_rate
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.shift_monitoring import (
    WorkerCheckInResponse, WorkerCheckOutResponse,
    WorkerAttendanceToggleResponse, WorkerShiftStateResponse
)
from app.api.worker_shift_utils import (
    resolve_shift_execution, evaluate_worker_attendance_status,
    calculate_rounded_work_hours, evaluate_shift_overtime
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

    # Strictly verify worker assignment
    assigned_worker_ids = set()
    for w in (shift_doc.get("assigned_workers") or []):
        if isinstance(w, dict) and w.get("worker_id"):
            assigned_worker_ids.add(str(w["worker_id"]))
    for w in (shift_doc.get("workers") or []):
        if isinstance(w, dict) and (w.get("worker_id") or w.get("id")):
            assigned_worker_ids.add(str(w.get("worker_id") or w.get("id")))
    for wid in (shift_doc.get("worker_ids") or []):
        if wid:
            assigned_worker_ids.add(str(wid))

    if worker_id not in assigned_worker_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker is not assigned to this shift")

    workers_list = shift_doc.get("assigned_workers") or shift_doc.get("workers") or []
    target_idx = next((idx for idx, w in enumerate(workers_list) if str(w.get("worker_id")) == worker_id), None)
    if target_idx is None:
        # If worker was in worker_ids list, add record to workers_list
        w_record = {"worker_id": worker_id, "position": "normal", "role": "worker"}
        workers_list.append(w_record)
        target_idx = len(workers_list) - 1

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

    update_fields = {
        "assigned_workers": workers_list,
        "workers": workers_list,
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
    description="Allows a worker to check out of an assigned shift once all rooms, tasks, and photos are approved."
)
@attendance_router.post("/{shift_id}/checkout", response_model=WorkerCheckOutResponse, include_in_schema=False)
async def worker_shift_check_out(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    # Strictly verify worker assignment
    assigned_worker_ids = set()
    for w in (shift_doc.get("assigned_workers") or []):
        if isinstance(w, dict) and w.get("worker_id"):
            assigned_worker_ids.add(str(w["worker_id"]))
    for w in (shift_doc.get("workers") or []):
        if isinstance(w, dict) and (w.get("worker_id") or w.get("id")):
            assigned_worker_ids.add(str(w.get("worker_id") or w.get("id")))
    for wid in (shift_doc.get("worker_ids") or []):
        if wid:
            assigned_worker_ids.add(str(wid))

    if worker_id not in assigned_worker_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker is not assigned to this shift")

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

    # Validation: all rooms must be completed, all tasks completed, all required photos approved
    rooms = shift_doc.get("rooms", [])
    additional_tasks = shift_doc.get("additional_tasks", [])

    pending_items = []
    for r in rooms:
        r_name = r.get("room_name") or r.get("name") or "Room"
        if r.get("status") != "completed":
            pending_items.append(f"Room '{r_name}' is {r.get('status', 'pending')}")
        for t in r.get("tasks", []):
            t_name = t.get("name", "Task")
            if not t.get("is_completed"):
                pending_items.append(f"Task '{t_name}' in room '{r_name}' is not completed")
            for p in t.get("photo", []):
                p_id = str(p.get("id"))
                matched_sub = next((sp for sp in (t.get("submitted_photos") or []) if str(sp.get("photo_id")) == p_id), None)
                if not matched_sub:
                    pending_items.append(f"Required photo '{p.get('name')}' for task '{t_name}' is not uploaded")
                elif matched_sub.get("status") != "approved":
                    pending_items.append(f"Photo '{p.get('name')}' for task '{t_name}' is {matched_sub.get('status', 'pending_review')}")

    for at in additional_tasks:
        at_name = at.get("name", "Additional Task")
        if not at.get("is_completed"):
            pending_items.append(f"Additional task '{at_name}' is not completed")
        for p in at.get("photo", []):
            p_id = str(p.get("id"))
            matched_sub = next((sp for sp in (at.get("submitted_photos") or []) if str(sp.get("photo_id")) == p_id), None)
            if not matched_sub:
                pending_items.append(f"Required photo '{p.get('name')}' for additional task '{at_name}' is not uploaded")
            elif matched_sub.get("status") != "approved":
                pending_items.append(f"Photo '{p.get('name')}' for additional task '{at_name}' is {matched_sub.get('status', 'pending_review')}")

    if pending_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot check out: all rooms, tasks, and required photos must be approved by manager. Pending items: {'; '.join(pending_items[:3])}"
        )

    checkin_dt = c_time
    if isinstance(checkin_dt, str):
        checkin_dt = datetime.fromisoformat(checkin_dt)
    if checkin_dt and checkin_dt.tzinfo is None:
        checkin_dt = checkin_dt.replace(tzinfo=timezone.utc)

    duration_seconds = (now - checkin_dt).total_seconds() if checkin_dt else 0.0
    rounded_hours, raw_hours, duration_mins = calculate_rounded_work_hours(duration_seconds)

    # Determine scheduled shift duration in minutes for overtime comparison
    scheduled_minutes = shift_doc.get("duration_minutes") or 0.0
    if not scheduled_minutes and shift_doc.get("start_time") and shift_doc.get("end_time"):
        try:
            from app.core.timezone_utils import parse_plan_start_datetime, get_timezone
            s_tz = get_timezone(shift_doc.get("timezone", "Europe/Amsterdam"))
            s_dt = parse_plan_start_datetime("2000-01-01", shift_doc["start_time"], s_tz)
            e_dt = parse_plan_start_datetime("2000-01-01", shift_doc["end_time"], s_tz)
            if e_dt > s_dt:
                scheduled_minutes = (e_dt - s_dt).total_seconds() / 60.0
        except Exception:
            pass

    regular_hours, overtime_hours = evaluate_shift_overtime(rounded_hours, scheduled_minutes)

    # Retrieve worker hourly salary from users collection
    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one(user_query)
    hourly_rate = resolve_hourly_rate(user_doc)
    shift_earnings = round(rounded_hours * hourly_rate, 2)

    workers_list[target_idx]["checkout_time"] = now
    workers_list[target_idx]["hours_worked"] = rounded_hours
    workers_list[target_idx]["raw_hours_worked"] = raw_hours
    workers_list[target_idx]["duration_minutes"] = duration_mins
    workers_list[target_idx]["regular_hours"] = regular_hours
    workers_list[target_idx]["overtime_hours"] = overtime_hours
    workers_list[target_idx]["hourly_rate"] = hourly_rate
    workers_list[target_idx]["shift_earnings"] = shift_earnings

    update_fields = {
        "assigned_workers": workers_list,
        "workers": workers_list,
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
            hours_worked=rounded_hours,
            status_label="completed"
        )
    except Exception:
        pass

    return WorkerCheckOutResponse(
        shift_id=str(shift_doc.get("id") or shift_doc.get("_id")),
        worker_id=worker_id,
        checkout_time=now,
        hours_worked=rounded_hours
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
