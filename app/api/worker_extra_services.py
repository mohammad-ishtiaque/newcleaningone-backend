import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.services.extra_services_helper import format_extra_service_response
from app.schemas.extra_services import ExtraServiceResponse, ExtraServicePaginatedResponse

router = APIRouter(prefix="/worker/extra-services", tags=["Worker Extra Service Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@router.get(
    "",
    response_model=ExtraServicePaginatedResponse,
    summary="Worker List Assigned Extra Services",
    description="Returns a paginated list of extra service requests assigned to the logged-in worker."
)
async def list_worker_extra_services(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker List Assigned Extra Services Endpoint.
    """
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "worker_1")

    query = {"assigned_workers.worker_id": worker_id}
    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [format_extra_service_response(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get(
    "/{request_id}",
    response_model=ExtraServiceResponse,
    summary="Worker Get Extra Service Details",
    description="Retrieves extra service details, task checklist items, and photo requirements for worker."
)
async def get_worker_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Single Extra Service Detail Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return format_extra_service_response(doc)


@router.post(
    "/{request_id}/start",
    response_model=ExtraServiceResponse,
    summary="Worker Start Extra Service",
    description="Marks the extra service as in_progress and records actual_start_time."
)
async def start_worker_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Start Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    now = datetime.now(timezone.utc)
    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "in_progress",
            "actual_start_time": now,
            "updated_at": now
        }}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/tasks/{task_id}/toggle",
    response_model=ExtraServiceResponse,
    summary="Worker Toggle Task Completion",
    description="Toggles the completion status of a specific task item within the extra service."
)
async def toggle_extra_service_task(
    request_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Toggle Task Completion Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    tasks = doc.get("tasks", [])
    now = datetime.now(timezone.utc)
    for t in tasks:
        if str(t.get("id")) == str(task_id):
            curr = t.get("is_completed", False)
            t["is_completed"] = not curr
            t["completed_at"] = now if not curr else None

    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {"tasks": tasks, "updated_at": now}}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/photos",
    response_model=ExtraServiceResponse,
    summary="Worker Upload Required Photo",
    description="Uploads a photo for extra service photo requirement and submits it to Admin Photo Review Queue."
)
async def upload_extra_service_photo(
    request_id: str,
    file: UploadFile = File(...),
    photo_requirement_id: Optional[str] = Form(None),
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Upload Required Photo Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    s3_service = S3Service()
    file_bytes = await file.read()
    url = await s3_service.upload_file(
        file_bytes,
        file_name=file.filename or "extra_service_photo.jpg",
        content_type=file.content_type or "image/jpeg"
    )

    now = datetime.now(timezone.utc)
    photos = doc.get("required_photos", [])
    tasks = doc.get("tasks", [])

    if photo_requirement_id:
        # Check in task-level photos
        for t in tasks:
            for p in t.get("photo", []):
                if str(p.get("id")) == str(photo_requirement_id):
                    p["photo_url"] = url
                    p["is_uploaded"] = True
                    p["uploaded_at"] = now
        # Check in standalone required_photos
        for p in photos:
            if str(p.get("id")) == str(photo_requirement_id):
                p["photo_url"] = url
                p["is_uploaded"] = True
                p["uploaded_at"] = now
    else:
        # First try to find unuploaded photo in tasks
        matched = False
        for t in tasks:
            for p in t.get("photo", []):
                if not p.get("is_uploaded"):
                    p["photo_url"] = url
                    p["is_uploaded"] = True
                    p["uploaded_at"] = now
                    matched = True
                    break
            if matched:
                break

        if not matched:
            target_p = next((p for p in photos if not p.get("is_uploaded")), None)
            if target_p:
                target_p["photo_url"] = url
                target_p["is_uploaded"] = True
                target_p["uploaded_at"] = now
            else:
                photos.append({
                    "id": f"p_{uuid.uuid4().hex[:6]}",
                    "name": file.filename or "Uploaded Photo",
                    "photo_url": url,
                    "is_uploaded": True,
                    "uploaded_at": now
                })

    review_id = f"RV-{uuid.uuid4().hex[:6].upper()}"
    await db["photo_reviews"].insert_one({
        "_id": review_id,
        "review_id": review_id,
        "shift_id": str(doc.get("_id") or doc.get("id")),
        "service_kind": "extra_service",
        "cleaner": {
            "worker_id": str(current_user.id or current_user.mongo_id),
            "name": getattr(current_user, "full_name", "Worker"),
            "profile_picture": getattr(current_user, "profile_photo", None)
        },
        "client": {
            "client_id": doc.get("client_id", ""),
            "name": doc.get("client_name", "Client")
        },
        "location": {
            "location_id": str(doc.get("location_id") or "loc_default"),
            "name": doc.get("location_name") or "Main Office Suite"
        },
        "room": {
            "room_id": str(doc.get("room_id") or "room_default"),
            "name": doc.get("room_name") or doc.get("title") or "Room"
        },
        "photo_url": url,
        "photo_name": file.filename or "Required Extra Service Photo",
        "ai_score": 95.0,
        "ai_confidence": "high",
        "status": "pending_review",
        "rejection_reason": None,
        "date_submitted": now
    })

    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {"required_photos": photos, "tasks": tasks, "updated_at": now}}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.post(
    "/{request_id}/submit",
    response_model=ExtraServiceResponse,
    summary="Worker Submit Extra Service for Completion",
    description="Submits extra service for Admin final approval after completing all tasks and photo requirements."
)
async def submit_worker_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    """
    Worker Submit Extra Service Endpoint.
    Validates that tasks and photo requirements are complete before submission.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    tasks = doc.get("tasks", [])
    incomplete_tasks = [t for t in tasks if not t.get("is_completed")]
    if incomplete_tasks:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit extra service. {len(incomplete_tasks)} task(s) are incomplete."
        )

    photos = doc.get("required_photos", [])
    unuploaded_photos = [p for p in photos if not p.get("is_uploaded")]
    if unuploaded_photos:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit extra service. {len(unuploaded_photos)} required photo(s) have not been uploaded."
        )

    now = datetime.now(timezone.utc)
    await db["extra_services"].update_one(
        {"$or": [{"_id": request_id}, {"id": request_id}]},
        {"$set": {
            "status": "submitted_for_completion",
            "actual_finish_time": now,
            "updated_at": now
        }}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)
