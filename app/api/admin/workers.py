import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Response
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.security.password import get_password_hash
from app.schemas.user import (
    WorkerApprovalUpdate, WorkerApprovalResponse, WorkerApprovalPaginatedResponse,
    WorkerListItem, WorkerListPaginatedResponse,
    AdminWorkerCreate, AdminWorkerStatusUpdate, AdminWorkerTableItem, AdminWorkerTablePaginatedResponse,
    WorkerBulkImportResult
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_admin
from app.api.admin.worker_csv_utils import generate_csv_template, parse_and_validate_worker_csv, export_workers_to_csv

worker_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Worker Management"])

# ================================
# 1. Admin Workers Overview Table & Stats (Image 1)
# ================================

@worker_mgmt_router.get("/workers", response_model=AdminWorkerTablePaginatedResponse, summary="Get Admin Workers Management Overview Table (Image 1)")
async def get_admin_workers_table(
    page: int = 1,
    limit: int = 10,
    worker_type: Optional[str] = None,  # all, employee, freelancer
    status_filter: Optional[str] = None,  # all, on_shift, active, off_duty, suspended, banned
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {"role": "worker"}

    # Worker type filter
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    # Search filter
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]

    # Overall Summary Counters
    total_workers_cnt = await db["users"].count_documents({"role": "worker"})
    employees_cnt = await db["users"].count_documents({"role": "worker", "worker_type": "employee"})
    freelancers_cnt = await db["users"].count_documents({"role": "worker", "worker_type": "freelancer"})

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

# ================================
# 2. Add New Worker (Image 2 Modal)
# ================================

@worker_mgmt_router.post("/workers", response_model=AdminWorkerTableItem, status_code=status.HTTP_201_CREATED, summary="Add New Worker (Image 2 Modal)")
async def create_new_worker(
    worker_in: AdminWorkerCreate,
    current_user: UserInDB = Depends(require_admin)
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

# ================================
# 3. Suspend / Ban / Activate Worker
# ================================

@worker_mgmt_router.patch("/workers/{worker_id}/status", summary="Ban, Suspend, or Activate Worker")
async def update_worker_status(
    worker_id: str,
    status_in: AdminWorkerStatusUpdate,
    current_user: UserInDB = Depends(require_admin)
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

# ================================
# 4. Bulk CSV Import Template & Upload (Image 3)
# ================================

@worker_mgmt_router.get("/workers/bulk-import/template", summary="Download CSV Bulk Import Template (Image 3)")
async def download_worker_import_template(
    current_user: UserInDB = Depends(require_admin)
):
    template_content = generate_csv_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import_workers_template.csv"}
    )

@worker_mgmt_router.post("/workers/bulk-import", response_model=WorkerBulkImportResult, summary="Validate & Bulk Import CSV Worker Data (Image 3 Modal)")
async def bulk_import_workers_csv(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_admin)
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for bulk import")

    db = get_database()
    file_bytes = await file.read()
    result = await parse_and_validate_worker_csv(file_bytes, db)
    return result

# ================================
# 5. Worker CSV Export
# ================================

@worker_mgmt_router.get("/workers/export", summary="Export Workers Data to CSV")
async def export_workers_csv(
    worker_type: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {"role": "worker"}
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    workers_raw = await db["users"].find(query).sort("full_name", 1).to_list(length=2000)
    csv_text = export_workers_to_csv(workers_raw)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=workers_export.csv"}
    )

# ================================
# 6. Existing Approval & Worker Listing Endpoints
# ================================

@worker_mgmt_router.get("/worker-approvals", response_model=WorkerApprovalPaginatedResponse, summary="List Pending Worker Approvals")
async def list_pending_worker_approvals(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = "pending",
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
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

@worker_mgmt_router.patch("/workers/{worker_id}/approval", response_model=WorkerApprovalResponse, summary="Update Worker Approval Status")
async def update_worker_approval_status(
    worker_id: str,
    approval_in: WorkerApprovalUpdate,
    current_user: UserInDB = Depends(require_admin)
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

@worker_mgmt_router.get("/workers-list", response_model=WorkerListPaginatedResponse, summary="List Available Workers")
async def list_available_workers(
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {"role": "worker", "is_active": True}

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
