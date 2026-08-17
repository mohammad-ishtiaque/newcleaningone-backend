import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.user import (
    AdminCreate, UserResponse, AdminWorkerCreate, AdminWorkerUpdate,
    AdminWorkerResponse, AdminWorkerPaginatedResponse, WorkerApprovalUpdate,
    WorkerApprovalResponse, WorkerApprovalPaginatedResponse, WorkerCountResponse
)
from app.models.user import RoleEnum, UserInDB, WorkerTypeEnum
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.security.password import get_password_hash
from app.api.admin.profile_company import require_manager

router = APIRouter(prefix="/manager", tags=["Manager Overview & User Management"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

# ================================
# Admin Dashboard / Overview Summary
# ================================

@router.get("/overview")
async def get_admin_overview_stats(current_user: UserInDB = Depends(require_manager)):
    db = get_database()
    total_workers = await db["users"].count_documents({"role": "worker"})
    total_clients = await db["client_list"].count_documents({})
    total_shifts = await db["shifts"].count_documents({})
    pending_approvals = await db["users"].count_documents({"role": "worker", "approval_status": "pending"})

    return {
        "total_workers": total_workers,
        "total_clients": total_clients,
        "total_shifts": total_shifts,
        "pending_approvals": pending_approvals,
        "server_time": datetime.now(timezone.utc)
    }

# ================================
# Super Admin Creation
# ================================



# ================================
# Admin Worker CRUD
# ================================

@router.post("/workers", response_model=AdminWorkerResponse, status_code=status.HTTP_201_CREATED)
async def create_worker_by_admin(
    worker_in: AdminWorkerCreate,
    current_user: UserInDB = Depends(require_manager),
    user_repo: UserRepository = Depends(UserRepository)
):
    existing = await user_repo.get_by_email(worker_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    now = datetime.now(timezone.utc)
    hashed_pwd = get_password_hash(worker_in.password)

    w_type = worker_in.worker_type if isinstance(worker_in.worker_type, str) else worker_in.worker_type.value

    worker_doc = {
        "full_name": worker_in.full_name,
        "email": worker_in.email,
        "phone": worker_in.phone,
        "hashed_password": hashed_pwd,
        "role": RoleEnum.worker.value,
        "worker_type": w_type,
        "position": worker_in.position,
        "location": worker_in.location,
        "base_location": worker_in.location,
        "languages": worker_in.languages or [],
        "employee_contract_pdf": worker_in.employee_contract_pdf,
        "is_active": True,
        "is_verified": True,
        "is_admin_created": True,
        "is_approved": True,
        "approval_status": "approved",
        "created_at": now,
        "updated_at": now
    }

    db = get_database()
    res = await db["users"].insert_one(worker_doc)
    worker_doc["_id"] = str(res.inserted_id)

    return AdminWorkerResponse(
        id=str(res.inserted_id),
        full_name=worker_doc["full_name"],
        email=worker_doc["email"],
        phone=worker_doc["phone"],
        role=worker_doc["role"],
        worker_type=worker_doc["worker_type"],
        position=worker_doc.get("position"),
        location=worker_doc.get("location"),
        languages=worker_doc.get("languages", []),
        employee_contract_pdf=worker_doc.get("employee_contract_pdf"),
        is_active=worker_doc["is_active"],
        is_verified=worker_doc["is_verified"],
        is_admin_created=True,
        approval_status="approved",
        created_at=now,
        updated_at=now
    )

@router.get("/workers", response_model=AdminWorkerPaginatedResponse)
async def list_workers(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    worker_type: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "worker"}

    if worker_type:
        query["worker_type"] = worker_type

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

    worker_list = []
    for w in raw_workers:
        w_id = str(w.get("_id"))
        w_type = w.get("worker_type", "full_time")
        if isinstance(w_type, WorkerTypeEnum):
            w_type = w_type.value

        worker_list.append(AdminWorkerResponse(
            id=w_id,
            full_name=w.get("full_name", ""),
            email=w.get("email", ""),
            phone=w.get("phone"),
            role=w.get("role", "worker"),
            worker_type=str(w_type),
            position=w.get("position"),
            location=w.get("location"),
            languages=w.get("languages", []),
            employee_contract_pdf=w.get("employee_contract_pdf"),
            is_active=w.get("is_active", True),
            is_verified=w.get("is_verified", True),
            is_admin_created=w.get("is_admin_created", False),
            approval_status=w.get("approval_status", "approved"),
            created_at=w.get("created_at", datetime.now(timezone.utc)),
            updated_at=w.get("updated_at", datetime.now(timezone.utc))
        ))

    return AdminWorkerPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        workers=worker_list
    )

@router.get("/workers-count", response_model=WorkerCountResponse)
async def get_worker_counts(current_user: UserInDB = Depends(require_manager)):
    db = get_database()
    total_workers = await db["users"].count_documents({"role": "worker"})
    approved_workers = await db["users"].count_documents({"role": "worker", "approval_status": "approved"})
    pending_workers = await db["users"].count_documents({"role": "worker", "approval_status": "pending"})
    rejected_workers = await db["users"].count_documents({"role": "worker", "approval_status": "rejected"})

    return WorkerCountResponse(
        total_workers=total_workers,
        approved_workers=approved_workers,
        pending_workers=pending_workers,
        rejected_workers=rejected_workers
    )
