import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.extra_services_helper import format_extra_service_response
from app.schemas.extra_services import (
    ExtraServiceApproveRequest, ExtraServiceRejectRequest,
    ExtraServiceResponse, ExtraServicePaginatedResponse
)

router = APIRouter(prefix="/manager/extra-services", tags=["Admin Extra Service Management"])


def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
    return current_user


@router.get(
    "",
    response_model=ExtraServicePaginatedResponse,
    summary="Admin List Extra Service Requests",
    description="Returns a paginated list of extra service requests across all clients for Admin review, with status and search filters."
)
async def list_admin_extra_services(
    status_val: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin List Extra Services Endpoint.
    Lists all client extra service requests.
    """
    db = get_database()
    query = {}

    if status_val and status_val.lower() != "all":
        query["status"] = status_val.lower()

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"title": search_regex},
            {"client_name": search_regex},
            {"description": search_regex}
        ]

    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [format_extra_service_response(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get(
    "/{request_id}",
    response_model=ExtraServiceResponse,
    summary="Admin Get Single Extra Service Request",
    description="Retrieves detailed information for a single extra service request by ID for Admin."
)
async def get_admin_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Get Single Extra Service Detail Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return format_extra_service_response(doc)


@router.post(
    "/{request_id}/reject",
    response_model=ExtraServiceResponse,
    summary="Admin Reject Extra Service Request",
    description="Rejects an extra service request with a specified rejection reason."
)
async def reject_extra_service_request(
    request_id: str,
    reject_in: ExtraServiceRejectRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Reject Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    now = datetime.now(timezone.utc)
    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "rejected",
            "rejection_reason": reject_in.reason,
            "updated_at": now
        }}
    )
    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/approve",
    response_model=ExtraServiceResponse,
    summary="Admin Approve Extra Service & Assign Worker",
    description="Approves an extra service request, assigns worker(s), sets estimated hours, and configures required photo checklist items."
)
async def approve_extra_service_request(
    request_id: str,
    approve_in: ExtraServiceApproveRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Approve Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    assigned_workers = []
    for w_id in approve_in.worker_ids:
        w_query = {"_id": ObjectId(w_id)} if ObjectId.is_valid(w_id) else {"_id": w_id}
        w_user = await db["users"].find_one(w_query)
        if w_user:
            assigned_workers.append({
                "worker_id": str(w_user.get("_id") or w_user.get("id")),
                "name": w_user.get("full_name", "Worker"),
                "profile_picture": w_user.get("profile_photo")
            })
        else:
            assigned_workers.append({
                "worker_id": str(w_id),
                "name": "Assigned Worker",
                "profile_picture": None
            })

    photo_requirements = []
    for p_name in approve_in.required_photos:
        photo_requirements.append({
            "id": f"p_{uuid.uuid4().hex[:6]}",
            "name": p_name,
            "photo_url": None,
            "is_uploaded": False,
            "uploaded_at": None
        })

    now = datetime.now(timezone.utc)
    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "approved",
            "assigned_workers": assigned_workers,
            "required_photos": photo_requirements,
            "estimated_hours": approve_in.estimated_hours,
            "updated_at": now
        }}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/complete-approve",
    response_model=ExtraServiceResponse,
    summary="Admin Final Approve & Credit Working Hours",
    description="Finalizes and approves extra service completion. Calculates worked hours and credits them directly to the assigned worker's total_working_hours in the users collection."
)
async def final_approve_extra_service_completion(
    request_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Admin Final Approve & Credit Working Hours Endpoint.
    Calculates actual hours worked and increments users.total_working_hours for assigned workers.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    now = datetime.now(timezone.utc)

    start_dt = doc.get("actual_start_time")
    finish_dt = doc.get("actual_finish_time") or now
    est_hours = float(doc.get("estimated_hours", 2.0))

    if start_dt and isinstance(start_dt, datetime):
        if finish_dt.tzinfo is None:
            finish_dt = finish_dt.replace(tzinfo=timezone.utc)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        hours_worked = max(0.5, round((finish_dt - start_dt).total_seconds() / 3600.0, 2))
    else:
        hours_worked = est_hours if est_hours > 0 else 2.0

    # Credit working hours to assigned workers in users collection
    workers = doc.get("assigned_workers", [])
    for w in workers:
        w_id = str(w.get("worker_id"))
        w_query = {"_id": ObjectId(w_id)} if ObjectId.is_valid(w_id) else {"_id": w_id}
        await db["users"].update_one(w_query, {"$inc": {"total_working_hours": hours_worked}})

    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "completed",
            "actual_finish_time": finish_dt,
            "hours_credited": hours_worked,
            "updated_at": now
        }}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)
