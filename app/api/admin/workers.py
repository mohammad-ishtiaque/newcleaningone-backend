import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Response
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.security.password import get_password_hash
from app.schemas.user import (
    WorkerApprovalUpdate, WorkerApprovalResponse, WorkerApprovalPaginatedResponse,
    WorkerApproveRequest, WorkerRejectRequest,
    WorkerListItem, WorkerListPaginatedResponse,
    AdminWorkerCreate, AdminWorkerStatusUpdate, AdminWorkerTableItem, AdminWorkerTablePaginatedResponse,
    WorkerBulkImportResult
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.worker_csv_utils import generate_csv_template, parse_and_validate_worker_csv, export_workers_to_csv

worker_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Worker Management"])


# ============================================================================
# 1. Admin Workers Overview Table & Stats (Image 1)
# ============================================================================

@worker_mgmt_router.get(
    "/workers",
    response_model=AdminWorkerTablePaginatedResponse,
    summary="Get Admin Workers Management Overview Table (Image 1)"
)
async def get_admin_workers_table(
    page: int = 1,
    limit: int = 10,
    worker_type: Optional[str] = None,  # all, employee, freelancer
    status_filter: Optional[str] = None,  # all, on_shift, active, off_duty, suspended, banned
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker", "account_status": {"$ne": "deleted"}}

    # Worker type filter
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    # Search filter
    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]
        if "$or" in query:
            query = {"$and": [query, {"$or": search_filter}]}
        else:
            query["$or"] = search_filter

    # Overall Summary Counters
    total_workers_cnt = await db["users"].count_documents({"role": "worker", "account_status": {"$ne": "deleted"}})
    employees_cnt = await db["users"].count_documents({"role": "worker", "worker_type": "employee", "account_status": {"$ne": "deleted"}})
    freelancers_cnt = await db["users"].count_documents({"role": "worker", "worker_type": "freelancer", "account_status": {"$ne": "deleted"}})

    # Fetch currently active shifts for On Shift status
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    running_shifts = await db["shifts"].find({"date": today_str, "status": {"$ne": "cancelled"}}).to_list(length=300)
    on_shift_worker_ids = set()
    for s in running_shifts:
        for w in s.get("workers", []):
            on_shift_worker_ids.add(str(w.get("worker_id") or w.get("id")))

    raw_workers = await db["users"].find(query).sort("full_name", 1).to_list(length=1000)

    formatted_workers = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        w_acct_status = w.get("account_status") or "active"
        w_is_active = bool(w.get("is_active", True))

        # Status determination
        if w_acct_status == "banned":
            w_status_label = "Banned"
        elif w_acct_status == "suspended":
            w_status_label = "Suspended"
        elif wid in on_shift_worker_ids:
            w_status_label = "On Shift"
        elif w_is_active:
            w_status_label = "Active"
        else:
            w_status_label = "Off Duty"

        # Apply status filter
        if status_filter and status_filter.lower() != "all":
            sf = status_filter.lower().replace("_", " ")
            if w_status_label.lower() != sf and w_acct_status.lower() != sf:
                continue

        # Calculate worked hours
        w_shifts = await db["shifts"].find({"workers.worker_id": wid, "status": {"$ne": "cancelled"}}).to_list(length=500)
        hw_total = 0.0
        for s in w_shifts:
            for item in s.get("workers", []):
                if str(item.get("worker_id") or item.get("id")) == wid:
                    hw_total += float(item.get("hours_worked", 8.0) or 8.0)
                    break

        formatted_hw = f"{int(hw_total)}h" if hw_total.is_integer() else f"{hw_total:.1f}h"

        langs = w.get("languages") or ["Nederlands", "English"]
        loc = w.get("location") or w.get("base_location") or "Amsterdam-Centrum"

        formatted_workers.append(AdminWorkerTableItem(
            worker_id=wid,
            full_name=w.get("full_name") or "Worker",
            profile_photo=w.get("profile_photo"),
            worker_type=str(w.get("worker_type") or "employee").capitalize(),
            position=w.get("position") or "Cleaner",
            location=loc,
            languages=langs,
            hours_worked=formatted_hw,
            hours_worked_numeric=round(hw_total, 1),
            status=w_status_label,
            account_status=w_acct_status,
            approval_status=w.get("approval_status") or "approved",
            is_active=w_is_active
        ))

    total_filtered = len(formatted_workers)
    skip = (page - 1) * limit
    paginated_workers = formatted_workers[skip : skip + limit]

    return AdminWorkerTablePaginatedResponse(
        total_workers=total_workers_cnt,
        employees_count=employees_cnt,
        freelancers_count=freelancers_cnt,
        page=page,
        limit=limit,
        workers=paginated_workers
    )


# ============================================================================
# 2. Add New Worker (Image 2 Modal)
# ============================================================================

@worker_mgmt_router.post(
    "/workers",
    response_model=AdminWorkerTableItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add New Worker (Image 2 Modal)"
)
async def create_new_worker(
    worker_in: AdminWorkerCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    email_clean = worker_in.email.lower().strip()
    existing = await db["users"].find_one({"email": email_clean})
    if existing:
        raise HTTPException(status_code=400, detail="Worker with this email already exists")

    now = datetime.now(timezone.utc)
    hashed_pwd = get_password_hash("WorkerPass123!")

    doc = {
        "full_name": worker_in.full_name,
        "email": email_clean,
        "phone": worker_in.phone,
        "hashed_password": hashed_pwd,
        "role": "worker",
        "worker_type": worker_in.worker_type.lower(),
        "position": worker_in.position or "Cleaner",
        "location": worker_in.base_location or "Amsterdam-Centrum",
        "base_location": worker_in.base_location or "Amsterdam-Centrum",
        "languages": worker_in.languages or ["Nederlands", "English"],
        "account_status": worker_in.status.lower(),
        "approval_status": "approved",
        "is_approved": True,
        "is_admin_created": True,
        "is_active": worker_in.status.lower() == "active",
        "national_id": worker_in.national_id,
        "certificates": worker_in.certificates or [],
        "created_at": now,
        "updated_at": now
    }

    res = await db["users"].insert_one(doc)
    wid = str(res.inserted_id)

    # Sync pre-creation entry in admin_workers collection
    await db["admin_workers"].update_one(
        {"email": email_clean},
        {"$set": {
            "name": worker_in.full_name,
            "email": email_clean,
            "phone": worker_in.phone,
            "worker_type": worker_in.worker_type.lower(),
            "position": worker_in.position or "Cleaner",
            "base_location": worker_in.base_location or "Amsterdam-Centrum",
            "languages": worker_in.languages or ["Nederlands", "English"],
            "created_at": now
        }},
        upsert=True
    )

    return AdminWorkerTableItem(
        worker_id=wid,
        full_name=worker_in.full_name,
        profile_photo=None,
        worker_type=worker_in.worker_type.capitalize(),
        position=worker_in.position or "Cleaner",
        location=worker_in.base_location or "Amsterdam-Centrum",
        languages=worker_in.languages or ["Nederlands", "English"],
        hours_worked="0h",
        hours_worked_numeric=0.0,
        status="Active" if worker_in.status.lower() == "active" else worker_in.status.capitalize(),
        account_status=worker_in.status.lower(),
        approval_status="approved",
        is_active=worker_in.status.lower() == "active"
    )


# ============================================================================
# 3. Worker Status, Soft Delete & Restore Lifecycle
# ============================================================================

@worker_mgmt_router.patch(
    "/workers/{worker_id}/status",
    summary="Ban, Suspend, or Activate Worker"
)
async def update_worker_status(
    worker_id: str,
    status_in: AdminWorkerStatusUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"_id": worker_id}
    wdoc = await db["users"].find_one(query)
    if not wdoc:
        raise HTTPException(status_code=404, detail="Worker not found")

    new_st = status_in.status.lower()
    is_active = (new_st == "active")

    update_fields = {
        "account_status": new_st,
        "is_active": is_active,
        "updated_at": datetime.now(timezone.utc)
    }
    if status_in.reason:
        update_fields["status_reason"] = status_in.reason

    await db["users"].update_one(query, {"$set": update_fields})
    return {"message": f"Worker account status updated to '{new_st}' successfully"}


@worker_mgmt_router.delete(
    "/workers/{worker_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Worker"
)
async def delete_worker(
    worker_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    wdoc = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not wdoc:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = datetime.now(timezone.utc)
    await db["users"].update_one(
        {"_id": wdoc["_id"]},
        {"$set": {
            "account_status": "deleted",
            "status": "deleted",
            "is_active": False,
            "updated_at": now
        }}
    )
    email = wdoc.get("email")
    if email:
        await db["admin_workers"].update_one(
            {"email": email},
            {"$set": {"status": "deleted", "is_active": False, "updated_at": now}}
        )

    return {"message": "Worker deleted successfully"}


@worker_mgmt_router.get(
    "/workers/deleted-list",
    response_model=AdminWorkerTablePaginatedResponse,
    summary="List Deleted Workers"
)
async def list_deleted_workers(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker", "account_status": "deleted"}
    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]
        query["$or"] = search_filter

    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_workers = await cursor.to_list(length=limit)

    formatted_workers = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        langs = w.get("languages") or ["Nederlands", "English"]
        loc = w.get("location") or w.get("base_location") or "Amsterdam-Centrum"

        formatted_workers.append(AdminWorkerTableItem(
            worker_id=wid,
            full_name=w.get("full_name") or "Worker",
            profile_photo=w.get("profile_photo"),
            worker_type=str(w.get("worker_type") or "employee").capitalize(),
            position=w.get("position") or "Cleaner",
            location=loc,
            languages=langs,
            hours_worked="0h",
            hours_worked_numeric=0.0,
            status="Deleted",
            account_status="deleted",
            approval_status=w.get("approval_status") or "approved",
            is_active=False
        ))

    return AdminWorkerTablePaginatedResponse(
        total_workers=total_count,
        employees_count=0,
        freelancers_count=0,
        page=page,
        limit=limit,
        workers=formatted_workers
    )


@worker_mgmt_router.post(
    "/workers/{worker_id}/restore",
    status_code=status.HTTP_200_OK,
    summary="Restore Deleted Worker"
)
async def restore_worker(
    worker_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    wdoc = await db["users"].find_one({"$and": [query, {"role": "worker"}, {"account_status": "deleted"}]})
    if not wdoc:
        raise HTTPException(status_code=404, detail="Deleted worker not found")

    now = datetime.now(timezone.utc)
    await db["users"].update_one(
        {"_id": wdoc["_id"]},
        {"$set": {
            "account_status": "active",
            "status": "active",
            "is_active": True,
            "updated_at": now
        }}
    )
    email = wdoc.get("email")
    if email:
        await db["admin_workers"].update_one(
            {"email": email},
            {"$set": {"status": "active", "is_active": True, "updated_at": now}}
        )

    return {"message": "Worker restored successfully"}


# ============================================================================
# 4. Bulk CSV Import Template & Upload (Image 3)
# ============================================================================

@worker_mgmt_router.get(
    "/workers/bulk-import/template",
    summary="Download CSV Bulk Import Template (Image 3)"
)
async def download_worker_import_template(
    current_user: UserInDB = Depends(require_manager)
):
    template_content = generate_csv_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import_workers_template.csv"}
    )


@worker_mgmt_router.post(
    "/workers/bulk-import",
    response_model=WorkerBulkImportResult,
    summary="Validate & Bulk Import CSV Worker Data (Image 3 Modal)"
)
async def bulk_import_workers_csv(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for bulk import")

    db = get_database()
    file_bytes = await file.read()
    result = await parse_and_validate_worker_csv(file_bytes, db)
    return result


# ============================================================================
# 5. Worker CSV Export
# ============================================================================

@worker_mgmt_router.get(
    "/workers/export",
    summary="Export Workers Data to CSV"
)
async def export_workers_csv(
    worker_type: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker", "account_status": {"$ne": "deleted"}}
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    workers_raw = await db["users"].find(query).sort("full_name", 1).to_list(length=2000)
    csv_text = export_workers_to_csv(workers_raw)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=workers_export.csv"}
    )


# ============================================================================
# 6. Worker Approvals & Available Worker Listings
# ============================================================================

@worker_mgmt_router.get(
    "/worker-approvals",
    response_model=WorkerApprovalPaginatedResponse,
    summary="List Pending Worker Approvals"
)
async def list_pending_worker_approvals(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = "pending",
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker"}

    if status_filter:
        query["approval_status"] = status_filter

    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
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

        approvals.append(WorkerApprovalResponse(
            id=wid,
            full_name=w.get("full_name", ""),
            email=w.get("email", ""),
            phone=w.get("phone"),
            worker_type=str(w.get("worker_type", "employee")),
            approval_status=w.get("approval_status", "pending"),
            is_approved=w.get("is_approved", False),
            rejection_reason=w.get("rejection_reason"),
            created_at=cat,
            updated_at=uat
        ))

    return WorkerApprovalPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        pending_approvals=approvals
    )


@worker_mgmt_router.post(
    "/workers/{worker_id}/approve",
    response_model=WorkerApprovalResponse,
    summary="Approve Worker Signup"
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

    update_fields = {
        "is_approved": True,
        "approval_status": "approved",
        "is_active": True,
        "account_status": "active",
        "worker_type": w_type,
        "position": w_pos,
        "base_location": w_loc,
        "location": w_loc,
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
        recipient_type="worker",
        user_id=str(user["_id"]),
        player_ids=player_ids
    )

    await ws_manager.broadcast_to_users({
        "type": "account_approved",
        "message": "Your worker account has been approved."
    }, [str(user["_id"])])

    cat = updated.get("created_at") if isinstance(updated.get("created_at"), datetime) else datetime.now(timezone.utc)
    uat = updated.get("updated_at") if isinstance(updated.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return WorkerApprovalResponse(
        id=str(updated["_id"]),
        full_name=updated.get("full_name", ""),
        email=updated.get("email", ""),
        phone=updated.get("phone"),
        worker_type=str(updated.get("worker_type", "employee")),
        approval_status="approved",
        is_approved=True,
        rejection_reason=None,
        created_at=cat,
        updated_at=uat
    )


@worker_mgmt_router.post(
    "/workers/{worker_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject Worker Signup"
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
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "reject_reason": reason_txt,
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
        recipient_type="worker",
        user_id=str(user["_id"]),
        player_ids=player_ids
    )

    await ws_manager.broadcast_to_users({
        "type": "account_rejected",
        "message": reject_msg
    }, [str(user["_id"])])

    return {"message": "Worker signup rejected successfully"}


@worker_mgmt_router.patch(
    "/workers/{worker_id}/approval",
    response_model=WorkerApprovalResponse,
    include_in_schema=False,
    summary="Update Worker Approval Status"
)
async def update_worker_approval_status(
    worker_id: str,
    approval_in: WorkerApprovalUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"_id": worker_id}
    wdoc = await db["users"].find_one(query)
    if not wdoc:
        raise HTTPException(status_code=404, detail="Worker not found")

    new_status = approval_in.approval_status
    if new_status not in ["approved", "rejected", "pending"]:
        raise HTTPException(status_code=400, detail="Invalid approval status")

    update_fields = {
        "approval_status": new_status,
        "is_approved": (new_status == "approved"),
        "rejection_reason": approval_in.rejection_reason if new_status == "rejected" else None,
        "updated_at": datetime.now(timezone.utc)
    }

    await db["users"].update_one(query, {"$set": update_fields})
    updated = await db["users"].find_one(query)
    cat = updated.get("created_at") if isinstance(updated.get("created_at"), datetime) else datetime.now(timezone.utc)
    uat = updated.get("updated_at") if isinstance(updated.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return WorkerApprovalResponse(
        id=str(updated.get("_id")),
        full_name=updated.get("full_name", ""),
        email=updated.get("email", ""),
        phone=updated.get("phone"),
        worker_type=str(updated.get("worker_type", "employee")),
        approval_status=updated.get("approval_status", "approved"),
        is_approved=updated.get("is_approved", True),
        rejection_reason=updated.get("rejection_reason"),
        created_at=cat,
        updated_at=uat
    )


@worker_mgmt_router.get(
    "/workers-list",
    response_model=WorkerListPaginatedResponse,
    summary="List Available Workers"
)
async def list_available_workers(
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker", "is_active": True, "account_status": {"$ne": "deleted"}}

    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("full_name", 1).skip(skip).limit(limit)
    raw_workers = await cursor.to_list(length=limit)

    items = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        items.append(WorkerListItem(
            worker_id=wid,
            name=w.get("full_name", ""),
            profile_picture=w.get("profile_photo"),
            worker_type=str(w.get("worker_type", "employee")),
            email=w.get("email"),
            phone=w.get("phone"),
            status=w.get("account_status", "active"),
            is_signup=True
        ))

    return WorkerListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        workers=items
    )
