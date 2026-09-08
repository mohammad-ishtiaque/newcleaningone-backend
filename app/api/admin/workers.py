import uuid
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Response, Form
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import resolve_hourly_rate
from app.api.admin.workers_docs import (
    AVAILABLE_WORKERS_DESCRIPTION,
    CREATE_WORKER_DESCRIPTION,
    UPDATE_WORKER_DESCRIPTION,
)
from app.security.password import get_password_hash, generate_temporary_password
from app.schemas.user import (
    WorkerApprovalUpdate, WorkerApprovalResponse, WorkerApprovalPaginatedResponse,
    WorkerApproveRequest, WorkerRejectRequest,
    WorkerListItem, WorkerListPaginatedResponse,
    AdminWorkerCreate, AdminWorkerUpdate, AdminWorkerStatusUpdate, AdminWorkerTableItem, AdminWorkerTablePaginatedResponse,
    WorkerBulkImportResult, WorkerDocumentUploadResponse, WorkerDocumentItem
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.worker_csv_utils import generate_csv_template, parse_and_validate_worker_csv, export_workers_to_csv
from app.api.admin.worker_approvals import _build_worker_documents
from app.services.s3_service import S3Service

worker_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Worker Management"])


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
    base_approved_filter = {
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }
    query = dict(base_approved_filter)

    # Worker type filter
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    # Search filter
    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}},
            {"base_location": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]
        query = {"$and": [query, {"$or": search_filter}]}

    # Status filter
    if status_filter and status_filter.lower() != "all":
        if status_filter.lower() == "active":
            query["is_active"] = True
            query["account_status"] = "active"
        elif status_filter.lower() == "on_shift":
            query["account_status"] = "on_shift"
        elif status_filter.lower() == "off_duty":
            query["account_status"] = "off_duty"
        elif status_filter.lower() in ["suspended", "banned"]:
            query["account_status"] = status_filter.lower()

    skip = (page - 1) * limit

    # Worker type counters and paginated query in parallel
    total_workers_cnt, employees_cnt, freelancers_cnt, total_filtered, page_workers = await asyncio.gather(
        db["users"].count_documents(base_approved_filter),
        db["users"].count_documents({**base_approved_filter, "worker_type": "employee"}),
        db["users"].count_documents({**base_approved_filter, "worker_type": "freelancer"}),
        db["users"].count_documents(query),
        db["users"].find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    )

    # Compute live total hours worked ONLY for the current page workers from shift_executions
    worker_ids_str = [str(w["_id"]) for w in page_workers]
    hours_map = {}
    if worker_ids_str:
        exec_cursor = db["shift_executions"].find(
            {
                "$or": [
                    {"assigned_workers.worker_id": {"$in": worker_ids_str}},
                    {"workers.worker_id": {"$in": worker_ids_str}},
                    {"worker_ids": {"$in": worker_ids_str}}
                ]
            },
            projection={"assigned_workers": 1, "workers": 1}
        )
        async for ex in exec_cursor:
            w_list = ex.get("assigned_workers") or ex.get("workers") or []
            for w_rec in w_list:
                wid = str(w_rec.get("worker_id") or "")
                if wid in worker_ids_str:
                    hw = float(w_rec.get("hours_worked") or 0.0)
                    hours_map[wid] = hours_map.get(wid, 0.0) + hw

    formatted_workers = []
    for w in page_workers:
        wid = str(w["_id"])
        loc = w.get("location") or w.get("base_location") or "Amsterdam-Centrum"
        langs = w.get("languages") or ["Nederlands", "English"]
        hw_total = hours_map.get(wid, 0.0)

        hours_int = int(hw_total)
        mins_int = int((hw_total - hours_int) * 60)
        formatted_hw = f"{hours_int}h {mins_int}m" if mins_int > 0 else f"{hours_int}h"

        w_acct_status = w.get("account_status", "active").lower()
        w_is_active = w.get("is_active", True)
        if not w_is_active or w_acct_status in ["suspended", "banned"]:
            w_status_label = w_acct_status.capitalize() if w_acct_status in ["suspended", "banned"] else "Inactive"
        else:
            w_status_label = "Active"

        hourly_r = resolve_hourly_rate(w)

        formatted_workers.append(AdminWorkerTableItem(
            worker_id=wid,
            full_name=w.get("full_name") or "Worker",
            name=w.get("full_name") or "Worker",
            email=w.get("email"),
            phone=w.get("phone"),
            profile_photo=w.get("profile_photo"),
            worker_type=str(w.get("worker_type") or "employee").capitalize(),
            position=w.get("position") or "Cleaner",
            location=loc,
            hourly_rate=hourly_r,
            languages=langs,
            hours_worked=formatted_hw,
            hours_worked_numeric=round(hw_total, 1),
            status=w_status_label,
            account_status=w_acct_status,
            approval_status=w.get("approval_status") or "approved",
            is_active=w_is_active
        ))

    return AdminWorkerTablePaginatedResponse(
        total_workers=total_workers_cnt,
        employees_count=employees_cnt,
        freelancers_count=freelancers_cnt,
        page=page,
        limit=limit,
        workers=formatted_workers
    )


# ============================================================================
# 2. Add New Worker & Update Worker (Image 2 Modal)
# ============================================================================

@worker_mgmt_router.post(
    "/workers",
    response_model=AdminWorkerTableItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add New Worker (Image 2 Modal)",
    description=CREATE_WORKER_DESCRIPTION
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
    temp_pwd = generate_temporary_password()
    hashed_pwd = get_password_hash(temp_pwd)

    # Schema validated and defaulted this already (see AdminWorkerCreate.hourly_rate).
    hourly_r = worker_in.hourly_rate

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
        "hourly_rate": hourly_r,
        "languages": worker_in.languages or ["Nederlands", "English"],
        "account_status": worker_in.status.lower(),
        "approval_status": "approved",
        "is_approved": True,
        "is_admin_created": True,
        "is_verified": True,
        "is_temporary_password": True,
        "temporary_password_created_at": now,
        "is_active": worker_in.status.lower() == "active",
        "national_id": worker_in.national_id,
        "certificates": worker_in.certificates or [],
        "id_card_front": worker_in.national_id_front,
        "id_card_back": worker_in.national_id_back,
        "employee_contract_pdf": worker_in.employee_contract_pdf,
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
            "hourly_rate": hourly_r,
            "languages": worker_in.languages or ["Nederlands", "English"],
            "created_at": now
        }},
        upsert=True
    )

    # Send credentials email asynchronously in background
    from app.services.email_service import EmailService
    asyncio.create_task(EmailService.send_credentials_email(
        to_email=email_clean,
        full_name=worker_in.full_name,
        role="Worker",
        password=temp_pwd
    ))

    return AdminWorkerTableItem(
        worker_id=wid,
        full_name=worker_in.full_name,
        name=worker_in.full_name,
        email=email_clean,
        temporary_password=temp_pwd,
        profile_photo=None,
        worker_type=worker_in.worker_type.capitalize(),
        position=worker_in.position or "Cleaner",
        location=worker_in.base_location or "Amsterdam-Centrum",
        hourly_rate=hourly_r,
        languages=worker_in.languages or ["Nederlands", "English"],
        hours_worked="0h",
        hours_worked_numeric=0.0,
        status="Active" if worker_in.status.lower() == "active" else worker_in.status.capitalize(),
        account_status=worker_in.status.lower(),
        approval_status="approved",
        is_active=worker_in.status.lower() == "active"
    )


@worker_mgmt_router.patch(
    "/workers/{worker_id}",
    response_model=AdminWorkerTableItem,
    summary="Update Worker Details (Manager)",
    description=UPDATE_WORKER_DESCRIPTION
)
async def update_worker_details(
    worker_id: str,
    update_in: AdminWorkerUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = datetime.now(timezone.utc)
    set_fields = {"updated_at": now}

    if update_in.full_name is not None:
        set_fields["full_name"] = update_in.full_name
    if update_in.email is not None:
        email_clean = update_in.email.lower().strip()
        existing = await db["users"].find_one({"email": email_clean, "_id": {"$ne": user_doc["_id"]}})
        if existing:
            raise HTTPException(status_code=400, detail="Another user with this email already exists")
        set_fields["email"] = email_clean
    if update_in.phone is not None:
        set_fields["phone"] = update_in.phone
    if update_in.worker_type is not None:
        set_fields["worker_type"] = update_in.worker_type.lower()
    if update_in.position is not None:
        set_fields["position"] = update_in.position
    if update_in.base_location is not None:
        set_fields["base_location"] = update_in.base_location
        set_fields["location"] = update_in.base_location
    if update_in.hourly_rate is not None:
        set_fields["hourly_rate"] = update_in.hourly_rate
    if update_in.languages is not None:
        set_fields["languages"] = update_in.languages
    if update_in.status is not None:
        st_clean = update_in.status.lower()
        set_fields["account_status"] = st_clean
        set_fields["is_active"] = (st_clean == "active")
    if update_in.national_id is not None:
        set_fields["national_id"] = update_in.national_id
    if update_in.certificates is not None:
        set_fields["certificates"] = update_in.certificates
    if update_in.national_id_front is not None:
        set_fields["id_card_front"] = update_in.national_id_front
    if update_in.national_id_back is not None:
        set_fields["id_card_back"] = update_in.national_id_back
    if update_in.employee_contract_pdf is not None:
        set_fields["employee_contract_pdf"] = update_in.employee_contract_pdf

    await db["users"].update_one({"_id": user_doc["_id"]}, {"$set": set_fields})
    updated = await db["users"].find_one({"_id": user_doc["_id"]})

    # Sync to admin_workers
    admin_w_set = {}
    if "full_name" in set_fields:
        admin_w_set["name"] = set_fields["full_name"]
    if "phone" in set_fields:
        admin_w_set["phone"] = set_fields["phone"]
    if "worker_type" in set_fields:
        admin_w_set["worker_type"] = set_fields["worker_type"]
    if "position" in set_fields:
        admin_w_set["position"] = set_fields["position"]
    if "base_location" in set_fields:
        admin_w_set["base_location"] = set_fields["base_location"]
    if "hourly_rate" in set_fields:
        admin_w_set["hourly_rate"] = set_fields["hourly_rate"]
    if admin_w_set:
        admin_w_set["updated_at"] = now
        await db["admin_workers"].update_one(
            {"email": updated.get("email")},
            {"$set": admin_w_set},
            upsert=True
        )

    hourly_r = resolve_hourly_rate(updated)

    return AdminWorkerTableItem(
        worker_id=str(updated["_id"]),
        full_name=updated.get("full_name") or "Worker",
        name=updated.get("full_name") or "Worker",
        email=updated.get("email"),
        phone=updated.get("phone"),
        profile_photo=updated.get("profile_photo"),
        worker_type=str(updated.get("worker_type") or "employee").capitalize(),
        position=updated.get("position") or "Cleaner",
        location=updated.get("location") or updated.get("base_location") or "Amsterdam-Centrum",
        hourly_rate=hourly_r,
        languages=updated.get("languages") or ["Nederlands", "English"],
        hours_worked="0h",
        hours_worked_numeric=0.0,
        status="Active" if updated.get("is_active", True) else "Inactive",
        account_status=updated.get("account_status", "active"),
        approval_status=updated.get("approval_status", "approved"),
        is_active=updated.get("is_active", True)
    )


DOCUMENT_FIELD_MAP = {
    "id_card_front": "id_card_front",
    "id_card_back": "id_card_back",
    "employee_contract_pdf": "employee_contract_pdf",
}

@worker_mgmt_router.post(
    "/workers/{worker_id}/documents",
    response_model=WorkerDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Worker Document to S3",
    description=(
        "Uploads a real file (ID card front/back, employee contract, or a certificate) to S3 and "
        "attaches it to the worker's record. This is the endpoint the admin panel's Documents tab "
        "should call - `POST /manager/workers` and `PATCH /manager/workers/{worker_id}` only accept "
        "pre-existing URL strings for these fields, they do not upload files themselves.\n\n"
        "`document_type` must be one of: `id_card_front`, `id_card_back`, `employee_contract_pdf`, `certificate`. "
        "The first three replace that single field; `certificate` appends to the worker's certificates list."
    )
)
async def upload_worker_document(
    worker_id: str,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    if document_type not in ("id_card_front", "id_card_back", "employee_contract_pdf", "certificate"):
        raise HTTPException(status_code=400, detail="document_type must be one of: id_card_front, id_card_back, employee_contract_pdf, certificate")

    db = get_database()
    query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one({"$and": [query, {"role": "worker"}]})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Worker not found")

    s3_service = S3Service()
    file_bytes = await file.read()
    url = await s3_service.upload_file(file_bytes, file.filename or "document", file.content_type or "application/octet-stream")
    if not url:
        raise HTTPException(status_code=502, detail="Upload to S3 failed - check AWS credentials, bucket name, and region")

    now = datetime.now(timezone.utc)
    if document_type == "certificate":
        await db["users"].update_one({"_id": user_doc["_id"]}, {"$push": {"certificates": url}, "$set": {"updated_at": now}})
    else:
        field = DOCUMENT_FIELD_MAP[document_type]
        old_url = user_doc.get(field)
        await db["users"].update_one({"_id": user_doc["_id"]}, {"$set": {field: url, "updated_at": now}})
        # Updating a document replaces the field, but the file it used to point
        # to would otherwise sit in S3 forever as an unreferenced, unbilled-for
        # orphan - clean it up now that the new one is safely saved.
        if old_url and old_url != url:
            try:
                await s3_service.delete_file(old_url)
            except Exception:
                pass

    updated = await db["users"].find_one({"_id": user_doc["_id"]})
    all_documents = _build_worker_documents(updated, updated.get("onboarding_draft") or {})

    doc_name_map = {
        "id_card_front": "ID Card Front",
        "id_card_back": "ID Card Back",
        "employee_contract_pdf": "Employment Contract",
        "certificate": f"Certificate {len(updated.get('certificates') or [])}"
    }

    return WorkerDocumentUploadResponse(
        document=WorkerDocumentItem(name=doc_name_map[document_type], type=document_type, url=url),
        documents=all_documents
    )


# ============================================================================
# 3. Bulk CSV Import Template, Upload & Export
# ============================================================================

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
# 4. Worker Status, Soft Delete & Restore Lifecycle
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


@worker_mgmt_router.get("/workers/export", summary="Export Workers CSV")
async def export_workers_csv(
    worker_type: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {
        "role": "worker",
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }
    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    workers_raw = await db["users"].find(query).sort("full_name", 1).to_list(length=2000)
    csv_text = export_workers_to_csv(workers_raw)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=workers_export.csv"}
    )


from app.api.admin.worker_approvals import worker_approvals_router

worker_mgmt_router.include_router(worker_approvals_router)


@worker_mgmt_router.get(
    "/workers-list",
    response_model=WorkerListPaginatedResponse,
    summary="List Available Workers",
    description=AVAILABLE_WORKERS_DESCRIPTION
)
async def list_available_workers(
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {
        "role": "worker",
        "is_active": True,
        "account_status": {"$ne": "deleted"},
        "$or": [
            {"is_approved": True},
            {"approval_status": "approved"},
            {"is_admin_created": True}
        ]
    }

    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]
        query = {"$and": [query, {"$or": search_filter}]}

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

    has_more = (skip + len(items)) < total_count

    return WorkerListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        workers=items
    )
