import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.schemas.extra_services import (
    ExtraServiceCreate, ExtraServiceUpdate, ExtraServiceApproveRequest, ExtraServiceRejectRequest,
    ExtraServiceResponse, ExtraServicePaginatedResponse, ExtraServiceWorkerDetail,
    ExtraServiceTaskItem, ExtraServicePhotoRequirement, ClientInfo, LocationInfo, RoomInfo
)

router = APIRouter(tags=["Extra Services"])

# Role Dependencies
def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _format_extra_service_response(doc: dict) -> ExtraServiceResponse:
    doc_id = str(doc.get("_id") or doc.get("id"))
    doc["id"] = doc_id

    # Format dates if needed
    if "created_at" not in doc or not isinstance(doc["created_at"], datetime):
        doc["created_at"] = datetime.now(timezone.utc)
    if "updated_at" not in doc or not isinstance(doc["updated_at"], datetime):
        doc["updated_at"] = datetime.now(timezone.utc)

    # Format tasks list
    raw_tasks = doc.get("tasks", [])
    tasks_res = []
    for t in raw_tasks:
        if isinstance(t, str):
            tasks_res.append(ExtraServiceTaskItem(id=f"t_{uuid.uuid4().hex[:6]}", name=t, is_completed=False))
        elif isinstance(t, dict):
            tasks_res.append(ExtraServiceTaskItem(**t))

    # Format photos list
    raw_photos = doc.get("required_photos", [])
    photos_res = []
    for p in raw_photos:
        if isinstance(p, str):
            photos_res.append(ExtraServicePhotoRequirement(id=f"p_{uuid.uuid4().hex[:6]}", name=p, is_uploaded=False))
        elif isinstance(p, dict):
            photos_res.append(ExtraServicePhotoRequirement(**p))

    # Format assigned workers
    raw_workers = doc.get("assigned_workers", [])
    workers_res = []
    for w in raw_workers:
        if isinstance(w, dict):
            workers_res.append(ExtraServiceWorkerDetail(
                worker_id=str(w.get("worker_id") or w.get("id")),
                name=w.get("name", "Worker"),
                profile_picture=w.get("profile_picture")
            ))

    c_id = doc.get("client_id") or "client_default"
    c_name = doc.get("client_name") or "Client"
    doc["client"] = ClientInfo(id=str(c_id), name=str(c_name))

    loc_id = doc.get("location_id") or "loc_default"
    loc_name = doc.get("location_name") or "Main Location"
    doc["location"] = LocationInfo(id=str(loc_id), name=str(loc_name))

    r_id = doc.get("room_id") or "room_default"
    r_name = doc.get("room_name") or doc.get("title") or "Room"
    doc["room"] = RoomInfo(id=str(r_id), name=str(r_name))

    doc["tasks"] = tasks_res
    doc["required_photos"] = photos_res
    doc["assigned_workers"] = workers_res

    return ExtraServiceResponse(**doc)


# ==========================================
# 1. CLIENT ENDPOINTS (/client/extra-services)
# ==========================================

@router.post("/client/extra-services", response_model=ExtraServiceResponse, status_code=status.HTTP_201_CREATED, summary="Client Create Extra Service Request")
async def create_client_extra_service(
    service_in: ExtraServiceCreate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)
    now = datetime.now(timezone.utc)
    service_id = f"es_{uuid.uuid4().hex[:10]}"

    client_name = getattr(current_user, "full_name", None) or "Client"
    c_doc = await db["client_list"].find_one({"_id": client_id})
    if c_doc:
        client_name = c_doc.get("company_name", client_name)

    location_name = None
    if service_in.location_id and c_doc and "locations" in c_doc:
        target_loc = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(service_in.location_id)), None)
        if target_loc:
            location_name = target_loc.get("name")

    # Format tasks list
    task_items = []
    for task_name in service_in.task_list:
        task_items.append({
            "id": f"t_{uuid.uuid4().hex[:6]}",
            "name": task_name,
            "is_completed": False,
            "completed_at": None
        })

    # Standardize priority format (e.g., "High Priority")
    prio_str = service_in.priority
    if "high" in prio_str.lower():
        prio_str = "High Priority"
    elif "medium" in prio_str.lower():
        prio_str = "Medium Priority"
    elif "low" in prio_str.lower():
        prio_str = "Low Priority"

    room_name = None
    if service_in.room_id:
        r_doc = await db["rooms"].find_one({"$or": [{"_id": service_in.room_id}, {"id": service_in.room_id}]})
        if r_doc:
            room_name = r_doc.get("room_name") or r_doc.get("custom_room_name")
        else:
            room_name = f"Room {service_in.room_id}"

    doc = {
        "_id": service_id,
        "id": service_id,
        "title": service_in.title,
        "preferred_date": service_in.preferred_date,
        "priority": prio_str,
        "description": service_in.description,
        "status": "under_review",
        "client_id": client_id,
        "client_name": client_name,
        "location_id": service_in.location_id,
        "location_name": location_name,
        "room_id": service_in.room_id,
        "room_name": room_name,
        "date_submitted": now.strftime("%b %d, %Y"),
        "rejection_reason": None,
        "assigned_workers": [],
        "tasks": task_items,
        "required_photos": [],
        "estimated_hours": 0.0,
        "actual_start_time": None,
        "actual_finish_time": None,
        "hours_credited": None,
        "created_at": now,
        "updated_at": now
    }

    await db["extra_services"].insert_one(doc)
    return _format_extra_service_response(doc)


@router.get("/client/extra-services", response_model=ExtraServicePaginatedResponse, summary="Client List Extra Service Requests")
async def list_client_extra_services(
    status_val: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(current_user.id or current_user.mongo_id)
    query = {"client_id": client_id}

    if status_val and status_val.lower() != "all":
        query["status"] = status_val.lower()
    if priority and priority.lower() != "all":
        query["priority"] = {"$regex": priority, "$options": "i"}

    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [_format_extra_service_response(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get("/client/extra-services/{request_id}", response_model=ExtraServiceResponse, summary="Client Get Single Extra Service Request")
async def get_client_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return _format_extra_service_response(doc)


@router.patch("/client/extra-services/{request_id}", response_model=ExtraServiceResponse, summary="Client Update Extra Service Request")
async def update_client_extra_service(
    request_id: str,
    service_in: ExtraServiceUpdate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    if doc.get("status") not in ["pending", "under_review"]:
        raise HTTPException(status_code=400, detail="Cannot update request after it has been reviewed or approved")

    update_fields = {"updated_at": datetime.now(timezone.utc)}
    if service_in.title is not None:
        update_fields["title"] = service_in.title
    if service_in.preferred_date is not None:
        update_fields["preferred_date"] = service_in.preferred_date
    if service_in.priority is not None:
        update_fields["priority"] = service_in.priority
    if service_in.description is not None:
        update_fields["description"] = service_in.description
    if service_in.location_id is not None:
        update_fields["location_id"] = service_in.location_id
    if service_in.task_list is not None:
        update_fields["tasks"] = [{"id": f"t_{uuid.uuid4().hex[:6]}", "name": t, "is_completed": False, "completed_at": None} for t in service_in.task_list]

    await db["extra_services"].update_one({"$or": [{"_id": request_id}, {"id": request_id}]}, {"$set": update_fields})
    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return _format_extra_service_response(updated_doc)


@router.delete("/client/extra-services/{request_id}", summary="Client Delete Extra Service Request")
async def delete_client_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    if doc.get("status") not in ["pending", "under_review"]:
        raise HTTPException(status_code=400, detail="Cannot delete request after it has been reviewed or approved")

    await db["extra_services"].delete_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return {"message": "Extra service request cancelled successfully"}


# ==========================================
# 2. ADMIN ENDPOINTS (/admin/extra-services)
# ==========================================

@router.get("/admin/extra-services", response_model=ExtraServicePaginatedResponse, summary="Admin List Extra Service Requests")
async def list_admin_extra_services(
    status_val: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
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

    requests_res = [_format_extra_service_response(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get("/admin/extra-services/{request_id}", response_model=ExtraServiceResponse, summary="Admin Get Single Extra Service Request")
async def get_admin_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return _format_extra_service_response(doc)


@router.post("/admin/extra-services/{request_id}/reject", response_model=ExtraServiceResponse, summary="Admin Reject Extra Service Request")
async def reject_extra_service_request(
    request_id: str,
    reject_in: ExtraServiceRejectRequest,
    current_user: UserInDB = Depends(require_admin)
):
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
    return _format_extra_service_response(updated_doc)


@router.post("/admin/extra-services/{request_id}/approve", response_model=ExtraServiceResponse, summary="Admin Approve Extra Service & Assign Worker")
async def approve_extra_service_request(
    request_id: str,
    approve_in: ExtraServiceApproveRequest,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    # Fetch assigned workers info
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

    # Photo requirements
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
    return _format_extra_service_response(updated_doc)


@router.post("/admin/extra-services/{request_id}/complete-approve", response_model=ExtraServiceResponse, summary="Admin Final Approve & Credit Working Hours")
async def final_approve_extra_service_completion(
    request_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    now = datetime.now(timezone.utc)

    # Calculate actual hours worked
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

    # Credit working hours to assigned workers
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
    return _format_extra_service_response(updated_doc)


# ==========================================
# 3. WORKER ENDPOINTS (/worker/extra-services)
# ==========================================

@router.get("/worker/extra-services", response_model=ExtraServicePaginatedResponse, summary="Worker List Assigned Extra Services")
async def list_worker_extra_services(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or current_user.mongo_id)

    query = {"assigned_workers.worker_id": worker_id}
    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [_format_extra_service_response(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get("/worker/extra-services/{request_id}", response_model=ExtraServiceResponse, summary="Worker Get Extra Service Details")
async def get_worker_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return _format_extra_service_response(doc)


@router.post("/worker/extra-services/{request_id}/start", response_model=ExtraServiceResponse, summary="Worker Start Extra Service")
async def start_worker_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
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
    return _format_extra_service_response(updated_doc)


@router.post("/worker/extra-services/{request_id}/tasks/{task_id}/toggle", response_model=ExtraServiceResponse, summary="Worker Toggle Task Completion")
async def toggle_extra_service_task(
    request_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_worker)
):
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
    return _format_extra_service_response(updated_doc)


@router.post("/worker/extra-services/{request_id}/photos", response_model=ExtraServiceResponse, summary="Worker Upload Required Photo")
async def upload_extra_service_photo(
    request_id: str,
    file: UploadFile = File(...),
    photo_requirement_id: Optional[str] = Form(None),
    current_user: UserInDB = Depends(require_worker)
):
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

    if photo_requirement_id:
        for p in photos:
            if str(p.get("id")) == str(photo_requirement_id):
                p["photo_url"] = url
                p["is_uploaded"] = True
                p["uploaded_at"] = now
    else:
        # Fill first unuploaded photo or append
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

    # Insert into photo_reviews collection for Admin Photo Review Queue
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
        {"$set": {"required_photos": photos, "updated_at": now}}
    )

    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return _format_extra_service_response(updated_doc)


@router.post("/worker/extra-services/{request_id}/submit", response_model=ExtraServiceResponse, summary="Worker Submit Extra Service for Completion")
async def submit_worker_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    # Validate that all tasks are completed
    tasks = doc.get("tasks", [])
    incomplete_tasks = [t for t in tasks if not t.get("is_completed")]
    if incomplete_tasks:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit extra service. {len(incomplete_tasks)} task(s) are incomplete."
        )

    # Validate that all required photos are uploaded
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
    return _format_extra_service_response(updated_doc)
