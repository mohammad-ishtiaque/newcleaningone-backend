from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import resolve_hourly_rate
from app.schemas.user import (
    WorkerApprovalResponse, WorkerApprovalPaginatedResponse,
    WorkerApproveRequest, WorkerRejectRequest, WorkerDetailResponse,
    WorkerShiftsSummary, WorkerShiftRow, WorkerAttendanceSummary, WorkerAttendanceRow, WorkerDocumentItem
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

worker_approvals_router = APIRouter(prefix="/manager", tags=["Manager Worker Management"])


def _format_viewable_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = str(path).strip()
    if p.startswith("http://") or p.startswith("https://") or p.startswith("/uploads/") or p.startswith("/static/"):
        return p
    clean_name = p.lstrip("/")
    if clean_name.startswith("uploads/"):
        return f"/{clean_name}"
    return f"/uploads/{clean_name}"


def _fmt_hours(dur: float) -> str:
    return f"{int(dur)}h" if float(dur).is_integer() else f"{dur:.1f}h"


def _fmt_time(raw) -> str:
    if not raw:
        return "--:--"
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw)
        except Exception:
            return "--:--"
    return raw.strftime("%H:%M")


async def _build_worker_shifts(db, worker_id: str) -> tuple:
    """
    Builds the Shifts tab data: summary counts (completed/in_progress/upcoming)
    plus the full shift list (date/location/hours/status), sourced from the
    `shifts` collection - the same source the rest of the manager worker-stats
    module (admin_shift_monitoring_worker_stats.py) already uses for this worker.
    """
    raw_shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "status": {"$ne": "cancelled"}
    }).sort("date", -1).to_list(length=1000)

    completed = in_progress = upcoming = 0
    rows: List[WorkerShiftRow] = []

    for s in raw_shifts:
        s_id = str(s.get("id") or s.get("_id"))
        s_status = str(s.get("status") or "scheduled").lower()
        w_record = next((w for w in s.get("workers", []) if str(w.get("worker_id") or w.get("id")) == worker_id), {})
        dur = float(w_record.get("hours_worked", 0.0) or 0.0)

        if s_status == "completed":
            completed += 1
            display_status = "Completed"
        elif s_status == "in_progress":
            in_progress += 1
            display_status = "In Progress"
        else:
            upcoming += 1
            display_status = "Upcoming"

        rows.append(WorkerShiftRow(
            shift_id=s_id,
            date=s.get("date", ""),
            location=s.get("location_name") or s.get("location") or "",
            hours=_fmt_hours(dur),
            hours_numeric=round(dur, 2),
            status=display_status
        ))

    summary = WorkerShiftsSummary(completed=completed, in_progress=in_progress, upcoming=upcoming)
    return summary, rows


async def _build_worker_attendance(db, worker_id: str) -> tuple:
    """
    Builds the Attendance tab data for the current calendar month: summary
    (hours this month, late days, absent days) plus the daily check-in/out
    rows - mirrors the logic in admin_shift_monitoring_worker_stats.py's
    get_worker_daily_activity, scoped here to the current month specifically.
    """
    month_iso = datetime.now(timezone.utc).strftime("%Y-%m")
    raw_shifts = await db["shifts"].find({
        "workers.worker_id": worker_id,
        "date": {"$regex": f"^{month_iso}"},
        "status": {"$ne": "cancelled"}
    }).sort("date", -1).to_list(length=1000)

    total_hours = 0.0
    late_days = 0
    absent_days = 0
    rows: List[WorkerAttendanceRow] = []

    for s in raw_shifts:
        s_id = str(s.get("id") or s.get("_id"))
        w_record = next((w for w in s.get("workers", []) if str(w.get("worker_id") or w.get("id")) == worker_id), {})
        c_raw = w_record.get("checkin_time")
        co_raw = w_record.get("checkout_time")
        w_status = w_record.get("status")

        dur = float(w_record.get("hours_worked", 0.0) or 0.0)
        total_hours += dur

        if w_status == "late":
            display_status = "Late"
            late_days += 1
        elif c_raw or w_status == "ontime":
            display_status = "On Time"
        else:
            display_status = "Absent"
            absent_days += 1

        rows.append(WorkerAttendanceRow(
            shift_id=s_id,
            date=s.get("date", ""),
            check_in=_fmt_time(c_raw),
            check_out=_fmt_time(co_raw),
            hours=_fmt_hours(dur),
            hours_numeric=round(dur, 2),
            status=display_status
        ))

    summary = WorkerAttendanceSummary(
        this_month_hours=_fmt_hours(total_hours),
        this_month_hours_numeric=round(total_hours, 2),
        late_days=late_days,
        absent_days=absent_days
    )
    return summary, rows


def _build_worker_documents(user: dict, draft: dict) -> List[WorkerDocumentItem]:
    docs: List[WorkerDocumentItem] = []

    id_front = _format_viewable_url(user.get("id_card_front") or draft.get("id_card_front"))
    if id_front:
        docs.append(WorkerDocumentItem(name="ID Card Front", type="id_card_front", url=id_front))

    id_back = _format_viewable_url(user.get("id_card_back") or draft.get("id_card_back"))
    if id_back:
        docs.append(WorkerDocumentItem(name="ID Card Back", type="id_card_back", url=id_back))

    contract = _format_viewable_url(user.get("employee_contract_pdf"))
    if contract:
        docs.append(WorkerDocumentItem(name="Employment Contract", type="employee_contract_pdf", url=contract))

    raw_certs = user.get("certificates") or draft.get("certificates") or []
    if isinstance(raw_certs, list):
        for idx, cert in enumerate(raw_certs, start=1):
            cert_url = _format_viewable_url(cert)
            if cert_url:
                docs.append(WorkerDocumentItem(name=f"Certificate {idx}", type="certificate", url=cert_url))

    return docs


@worker_approvals_router.get(
    "/worker-approvals",
    response_model=WorkerApprovalPaginatedResponse,
    summary="List Pending Worker Approvals",
    description="""
### List Pending Worker Approvals
Returns a paginated list of worker registrations awaiting manager approval.
Only workers who have completed their email verification (via OTP) are eligible and listed for approval.
"""
)
@worker_approvals_router.get(
    "/manager/worker-approvals",
    response_model=WorkerApprovalPaginatedResponse,
    summary="List Pending Worker Approvals (Alias)",
    include_in_schema=False
)
async def list_pending_worker_approvals(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = "pending",
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    
    # Must be worker role and must have verified email
    query = {"role": "worker", "is_verified": True}

    if status_filter and status_filter.lower() != "all":
        query["approval_status"] = status_filter

    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_workers = await cursor.to_list(length=limit)

    approvals = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        cat = w.get("created_at") if isinstance(w.get("created_at"), datetime) else datetime.now(timezone.utc)
        uat = w.get("updated_at") if isinstance(w.get("updated_at"), datetime) else datetime.now(timezone.utc)

        draft = w.get("onboarding_draft") or {}
        id_front = _format_viewable_url(w.get("id_card_front") or draft.get("id_card_front"))
        id_back = _format_viewable_url(w.get("id_card_back") or draft.get("id_card_back"))
        photo = _format_viewable_url(w.get("profile_photo") or draft.get("profile_photo"))
        raw_certs = w.get("certificates") or draft.get("certificates") or []
        certs = [_format_viewable_url(c) for c in raw_certs if c] if isinstance(raw_certs, list) else []
        dob = w.get("dob") or draft.get("dob")
        nat = w.get("nationality") or draft.get("nationality")

        approvals.append(WorkerApprovalResponse(
            id=wid,
            full_name=w.get("full_name", ""),
            email=w.get("email", ""),
            phone=w.get("phone"),
            worker_type=str(w.get("worker_type", "freelancer")),
            approval_status=w.get("approval_status", "pending"),
            is_approved=w.get("is_approved", False),
            rejection_reason=w.get("rejection_reason"),
            id_card_front=id_front,
            id_card_back=id_back,
            profile_photo=photo,
            certificates=certs,
            dob=str(dob) if dob else None,
            nationality=str(nat) if nat else None,
            created_at=cat,
            updated_at=uat
        ))

    has_more = (skip + len(approvals)) < total_count

    return WorkerApprovalPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        pending_approvals=approvals
    )


@worker_approvals_router.post(
    "/workers/{worker_id}/approve",
    response_model=WorkerApprovalResponse,
    summary="Approve Worker Signup",
    description="""
### Approve Worker Signup
Approves a worker who signed up themselves. Sets `approval_status: "approved"`, activates the
account, mirrors the worker into `admin_workers`, records the action in `worker_approval_history`,
and notifies the worker by push and WebSocket.

#### Request Body (entirely optional — send `{}` to approve with the worker's own details)

| Field | Type | Supported values | Omitted means |
| :--- | :--- | :--- | :--- |
| `worker_type` | enum | `"employee"`, `"freelancer"` | keep what the worker signed up with |
| `position` | string | e.g. `"Cleaner"` | keep existing, else `"Cleaner"` |
| `base_location` | string | Free text location label | keep existing, else `"Amsterdam-Centrum"` |
| `hourly_rate` | number | `> 0` and `<= 1000`, decimals allowed (`25.5`) | **keep the rate already on the account** |

#### Example Requests
```json
{}
```
```json
{ "worker_type": "employee", "position": "Cleaner", "hourly_rate": 25.5 }
```

#### Note on `hourly_rate`
Omitting it keeps whatever rate the account already carries. Previously this field defaulted to `25`,
which was indistinguishable from an omitted field, so approving with an empty body silently reset a
30/hour worker down to 25. Sending the removed `per_hour_salary` returns a `422`.

#### Errors
- `400` — worker is already approved
- `404` — no worker with this `worker_id`
- `422` — invalid `hourly_rate`, or the removed `per_hour_salary` was sent
- `403` — caller is not a manager
"""
)
@worker_approvals_router.post(
    "/manager/workers/{worker_id}/approve",
    response_model=WorkerApprovalResponse,
    summary="Approve Worker Signup (Alias)",
    include_in_schema=False
)
async def approve_worker_signup(
    worker_id: str,
    approve_in: Optional[WorkerApproveRequest] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not user:
        raise HTTPException(status_code=404, detail="Worker not found")
    if user.get("approval_status") == "approved":
        raise HTTPException(status_code=400, detail="Worker is already approved")

    now = datetime.now(timezone.utc)
    w_type = approve_in.worker_type if approve_in and approve_in.worker_type else (user.get("worker_type") or "employee")
    w_pos = approve_in.position if approve_in and approve_in.position else (user.get("position") or "Cleaner")
    w_loc = approve_in.base_location if approve_in and approve_in.base_location else (user.get("base_location") or "Amsterdam-Centrum")
    # Only an explicitly supplied rate overrides what is already on the account.
    hourly_r = (
        approve_in.hourly_rate
        if (approve_in and approve_in.hourly_rate is not None)
        else resolve_hourly_rate(user)
    )

    update_fields = {
        "is_approved": True,
        "approval_status": "approved",
        "is_active": True,
        "account_status": "active",
        "worker_type": w_type,
        "position": w_pos,
        "base_location": w_loc,
        "location": w_loc,
        "hourly_rate": hourly_r,
        "updated_at": now
    }

    await db["users"].update_one({"_id": user["_id"]}, {"$set": update_fields})
    updated = await db["users"].find_one({"_id": user["_id"]})

    # Sync into admin_workers collection
    await db["admin_workers"].update_one(
        {"email": user.get("email")},
        {"$set": {
            "name": user.get("full_name"),
            "email": user.get("email"),
            "phone": user.get("phone"),
            "worker_type": w_type,
            "position": w_pos,
            "base_location": w_loc,
            "hourly_rate": hourly_r,
            "status": "active",
            "is_active": True,
            "updated_at": now
        }},
        upsert=True
    )

    # Log to worker approval history
    await db["worker_approval_history"].insert_one({
        "worker_id": str(user["_id"]),
        "worker_name": user.get("full_name", ""),
        "worker_email": user.get("email", ""),
        "action": "approved",
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "created_at": now
    })

    from app.services.notification_service import NotificationService
    from app.api.chat import ws_manager

    notif_service = NotificationService()
    player_id = user.get("onesignal_player_id")
    player_ids = [player_id] if player_id else None

    await notif_service.create_notification(
        title="Account Approved",
        message="Your worker account has been approved by the admin. You can now login.",
        notification_type="account_approved",
        route_type="home",
        recipient_type="worker",
        user_id=str(user["_id"]),
        player_ids=player_ids,
        data={
            "route": "/worker/home",
            "deeplink": "cleaningone://worker/home"
        }
    )

    await ws_manager.broadcast_to_users({
        "type": "account_approved",
        "message": "Your worker account has been approved."
    }, [str(user["_id"])])

    cat = updated.get("created_at") if isinstance(updated.get("created_at"), datetime) else datetime.now(timezone.utc)
    uat = updated.get("updated_at") if isinstance(updated.get("updated_at"), datetime) else datetime.now(timezone.utc)

    draft = updated.get("onboarding_draft") or {}
    return WorkerApprovalResponse(
        id=str(updated["_id"]),
        full_name=updated.get("full_name", ""),
        email=updated.get("email", ""),
        phone=updated.get("phone"),
        worker_type=str(updated.get("worker_type", "employee")),
        approval_status="approved",
        is_approved=True,
        hourly_rate=hourly_r,
        rejection_reason=None,
        id_card_front=updated.get("id_card_front") or draft.get("id_card_front"),
        id_card_back=updated.get("id_card_back") or draft.get("id_card_back"),
        profile_photo=updated.get("profile_photo") or draft.get("profile_photo"),
        certificates=updated.get("certificates") or draft.get("certificates") or [],
        dob=str(updated.get("dob") or draft.get("dob")) if (updated.get("dob") or draft.get("dob")) else None,
        nationality=str(updated.get("nationality") or draft.get("nationality")) if (updated.get("nationality") or draft.get("nationality")) else None,
        created_at=cat,
        updated_at=uat
    )


@worker_approvals_router.post(
    "/workers/{worker_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject Worker Signup"
)
@worker_approvals_router.post(
    "/manager/workers/{worker_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject Worker Signup (Alias)",
    include_in_schema=False
)
async def reject_worker_signup(
    worker_id: str,
    reject_in: Optional[WorkerRejectRequest] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not user:
        raise HTTPException(status_code=404, detail="Worker not found")
    if user.get("approval_status") != "pending":
        raise HTTPException(status_code=400, detail="Worker is not pending approval")

    now = datetime.now(timezone.utc)
    reason_txt = reject_in.reject_reason if reject_in else None

    await db["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {
            "is_approved": False,
            "approval_status": "rejected",
            "account_status": "rejected",
            "is_active": False,
            "rejection_reason": reason_txt,
            "updated_at": now
        }}
    )

    await db["worker_approval_history"].insert_one({
        "worker_id": str(user["_id"]),
        "worker_name": user.get("full_name", ""),
        "worker_email": user.get("email", ""),
        "action": "rejected",
        "reject_reason": reason_txt,
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "created_at": now
    })

    from app.services.notification_service import NotificationService
    from app.api.chat import ws_manager

    notif_service = NotificationService()
    player_id = user.get("onesignal_player_id")
    player_ids = [player_id] if player_id else None

    reject_msg = f"Your worker account has been rejected. Reason: {reason_txt}" if reason_txt else "Your worker account has been rejected."

    await notif_service.create_notification(
        title="Account Rejected",
        message=reject_msg,
        notification_type="account_rejected",
        route_type="home",
        recipient_type="worker",
        user_id=str(user["_id"]),
        player_ids=player_ids,
        data={
            "route": "/worker/home",
            "deeplink": "cleaningone://worker/home"
        }
    )

    await ws_manager.broadcast_to_users({
        "type": "account_rejected",
        "message": reject_msg
    }, [str(user["_id"])])

    return {"message": "Worker signup rejected", "worker_id": worker_id, "rejection_reason": reason_txt}


@worker_approvals_router.get(
    "/workers/{worker_id}",
    response_model=WorkerDetailResponse,
    summary="Get Single Worker Details by ID"
)
async def get_worker_detail(
    worker_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not user:
        # Check admin_workers collection
        user = await db["admin_workers"].find_one({"$or": [{"_id": worker_id}, {"id": worker_id}, {"worker_id": worker_id}]})
        if not user:
            raise HTTPException(status_code=404, detail="Worker not found")

    wid = str(user.get("_id") or user.get("id") or worker_id)
    cat = user.get("created_at") if isinstance(user.get("created_at"), datetime) else datetime.now(timezone.utc)
    uat = user.get("updated_at") if isinstance(user.get("updated_at"), datetime) else datetime.now(timezone.utc)

    draft = user.get("onboarding_draft") or {}
    id_front = user.get("id_card_front") or draft.get("id_card_front")
    id_back = user.get("id_card_back") or draft.get("id_card_back")
    photo = user.get("profile_photo") or draft.get("profile_photo")
    certs = user.get("certificates") or draft.get("certificates") or []
    dob = user.get("dob") or draft.get("dob")
    nat = user.get("nationality") or draft.get("nationality")

    # Shift counts
    total_s = await db["shifts"].count_documents({"worker_id": wid}) + await db["shift_executions"].count_documents({"assigned_workers.worker_id": wid})
    comp_s = await db["shifts"].count_documents({"worker_id": wid, "status": "completed"}) + await db["shift_executions"].count_documents({"assigned_workers.worker_id": wid, "status": "completed"})

    shifts_summary, shift_rows = await _build_worker_shifts(db, wid)
    attendance_summary, attendance_rows = await _build_worker_attendance(db, wid)
    document_items = _build_worker_documents(user, draft)

    return WorkerDetailResponse(
        id=wid,
        worker_id=wid,
        full_name=user.get("full_name") or user.get("name", "Worker"),
        email=user.get("email", "worker@cleaningone.com"),
        phone=user.get("phone"),
        worker_type=user.get("worker_type", "employee"),
        position=user.get("position", "Cleaner"),
        base_location=user.get("base_location") or user.get("location") or "Amsterdam-Centrum",
        profile_photo=photo,
        id_card_front=id_front,
        id_card_back=id_back,
        certificates=certs if isinstance(certs, list) else [],
        dob=str(dob) if dob else None,
        nationality=str(nat) if nat else None,
        status=user.get("status") or ("active" if user.get("is_active", True) else "inactive"),
        account_status=user.get("account_status") or "active",
        is_approved=user.get("is_approved", True),
        approval_status=user.get("approval_status") or "approved",
        total_shifts_count=total_s,
        completed_shifts_count=comp_s,
        rating=float(user.get("rating", 5.0)),
        hourly_rate=resolve_hourly_rate(user),
        shifts_summary=shifts_summary,
        shifts=shift_rows,
        attendance_summary=attendance_summary,
        attendance=attendance_rows,
        documents=document_items,
        created_at=cat,
        updated_at=uat
    )
