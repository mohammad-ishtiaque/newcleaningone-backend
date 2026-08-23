from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.user import (
    WorkerApprovalResponse, WorkerApprovalPaginatedResponse,
    WorkerApproveRequest, WorkerRejectRequest
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

worker_approvals_router = APIRouter(prefix="/manager", tags=["Manager Worker Management"])

@worker_approvals_router.get(
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


@worker_approvals_router.post(
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


@worker_approvals_router.post(
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
        "reject_reason": reason_txt,
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "created_at": now
    })

    return {"message": "Worker signup rejected", "worker_id": worker_id, "rejection_reason": reason_txt}
