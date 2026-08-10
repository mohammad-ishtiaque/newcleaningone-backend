import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.shift import (
    ShiftDraftCreate, ShiftDraftResponse, ShiftDraftUpdate, ShiftDraftPaginatedResponse,
    WorkerDropdownItem, WorkerDropdownPaginatedResponse,
    ShiftAssignRequest, ShiftWorkerDetail, ShiftResponse, ShiftPaginatedResponse, ShiftUpdate,
    PhotoReviewPaginatedResponse, PhotoReviewItem, PhotoReviewRejectRequest
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

shift_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Shift Management"])

def _format_shift_response(doc: dict) -> ShiftResponse:
    s_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    raw_workers = doc.get("workers", [])
    formatted_workers = []
    for w in raw_workers:
        formatted_workers.append(ShiftWorkerDetail(
            worker_id=str(w.get("worker_id")),
            name=w.get("name", "Worker"),
            profile_picture=w.get("profile_picture"),
            worker_type=w.get("worker_type", "employee"),
            shift_role=w.get("shift_role", "cleaning_specialist")
        ))

    return ShiftResponse(
        id=s_id,
        draft_id=doc.get("draft_id"),
        client_id=doc.get("client_id", ""),
        client_name=doc.get("client_name", "Client"),
        location_id=doc.get("location_id", ""),
        location_name=doc.get("location_name", "Location"),
        date=doc.get("date", ""),
        start_time=doc.get("start_time", "08:00"),
        end_time=doc.get("end_time", "16:00"),
        shift_notes=doc.get("shift_notes"),
        cleaning_plan_id=doc.get("cleaning_plan_id"),
        rooms=doc.get("rooms", []),
        total_tasks_count=doc.get("total_tasks_count", 0),
        total_photo_required=doc.get("total_photo_required", 0),
        overall_progress_percentage=doc.get("overall_progress_percentage", 0.0),
        completed_rooms_count=doc.get("completed_rooms_count", 0),
        in_progress_rooms_count=doc.get("in_progress_rooms_count", 0),
        pending_rooms_count=doc.get("pending_rooms_count", 0),
        status=doc.get("status", "published"),
        workers=formatted_workers,
        created_at=c_at,
        updated_at=u_at
    )

# ================================
# Shift Draft Management
# ================================

@shift_mgmt_router.post("/shifts/drafts", response_model=ShiftDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_shift_draft(
    draft_in: ShiftDraftCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    cdoc = await db["client_list"].find_one({"$or": [{"_id": draft_in.client_id}, {"id": draft_in.client_id}]})
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    ldoc = await db["locations"].find_one({"$or": [{"_id": draft_in.location_id}, {"id": draft_in.location_id}]})
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(timezone.utc)
    draft_id = f"draft_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": draft_id,
        "id": draft_id,
        "client_id": draft_in.client_id,
        "client_name": cdoc.get("company_name", "Client"),
        "location_id": draft_in.location_id,
        "location_name": ldoc.get("name", "Location"),
        "date": draft_in.date,
        "start_time": draft_in.start_time,
        "end_time": draft_in.end_time,
        "shift_notes": draft_in.shift_notes,
        "cleaning_plan_id": draft_in.cleaning_plan_id,
        "rooms": [],
        "total_tasks_count": 0,
        "total_photo_required": 0,
        "status": "draft",
        "created_at": now,
        "updated_at": now
    }

    await db["shift_drafts"].insert_one(doc)

    return ShiftDraftResponse(
        id=draft_id,
        client_id=draft_in.client_id,
        client_name=cdoc.get("company_name", "Client"),
        location_id=draft_in.location_id,
        location_name=ldoc.get("name", "Location"),
        date=draft_in.date,
        start_time=draft_in.start_time,
        end_time=draft_in.end_time,
        shift_notes=draft_in.shift_notes,
        cleaning_plan_id=draft_in.cleaning_plan_id,
        rooms=[],
        total_tasks_count=0,
        total_photo_required=0,
        status="draft",
        created_at=now,
        updated_at=now
    )

@shift_mgmt_router.get("/shifts/drafts", response_model=ShiftDraftPaginatedResponse)
async def list_shift_drafts(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    total_count = await db["shift_drafts"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["shift_drafts"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_drafts = await cursor.to_list(length=limit)

    draft_list = []
    for d in raw_drafts:
        cat = d.get("created_at") if isinstance(d.get("created_at"), datetime) else datetime.now(timezone.utc)
        uat = d.get("updated_at") if isinstance(d.get("updated_at"), datetime) else datetime.now(timezone.utc)
        draft_list.append(ShiftDraftResponse(
            id=str(d.get("_id") or d.get("id")),
            client_id=d.get("client_id", ""),
            client_name=d.get("client_name", "Client"),
            location_id=d.get("location_id", ""),
            location_name=d.get("location_name", "Location"),
            date=d.get("date", ""),
            start_time=d.get("start_time", "08:00"),
            end_time=d.get("end_time", "16:00"),
            shift_notes=d.get("shift_notes"),
            cleaning_plan_id=d.get("cleaning_plan_id"),
            rooms=[],
            total_tasks_count=0,
            total_photo_required=0,
            status=d.get("status", "draft"),
            created_at=cat,
            updated_at=uat
        ))

    return ShiftDraftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        drafts=draft_list
    )

# ================================
# Shift Assignment & Published Shifts
# ================================

@shift_mgmt_router.post("/shifts/assign", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED)
async def assign_workers_and_publish_shift(
    assign_in: ShiftAssignRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    draft_query = {"$or": [{"_id": assign_in.draft_id}, {"id": assign_in.draft_id}]}
    draft_doc = await db["shift_drafts"].find_one(draft_query)
    if not draft_doc:
        raise HTTPException(status_code=404, detail="Shift draft not found")

    w_ids = assign_in.worker_ids or []
    assigned_workers = []

    for wid in w_ids:
        u_query = {"_id": ObjectId(wid)} if ObjectId.is_valid(wid) else {"_id": wid}
        w_user = await db["users"].find_one(u_query)
        if w_user:
            assigned_workers.append({
                "worker_id": str(w_user.get("_id")),
                "name": w_user.get("full_name", "Worker"),
                "profile_picture": w_user.get("profile_photo"),
                "worker_type": str(w_user.get("worker_type", "employee")),
                "shift_role": "cleaning_specialist"
            })

    now = datetime.now(timezone.utc)
    shift_id = f"shift_{uuid.uuid4().hex[:10]}"

    shift_doc = {
        "_id": shift_id,
        "id": shift_id,
        "draft_id": assign_in.draft_id,
        "client_id": draft_doc.get("client_id"),
        "client_name": draft_doc.get("client_name"),
        "location_id": draft_doc.get("location_id"),
        "location_name": draft_doc.get("location_name"),
        "date": draft_doc.get("date"),
        "start_time": draft_doc.get("start_time"),
        "end_time": draft_doc.get("end_time"),
        "shift_notes": draft_doc.get("shift_notes"),
        "cleaning_plan_id": draft_doc.get("cleaning_plan_id"),
        "rooms": draft_doc.get("rooms", []),
        "status": "published",
        "workers": assigned_workers,
        "created_at": now,
        "updated_at": now
    }

    await db["shifts"].insert_one(shift_doc)
    await db["shift_drafts"].delete_one(draft_query)

    return _format_shift_response(shift_doc)

@shift_mgmt_router.get("/shifts", response_model=ShiftPaginatedResponse)
async def list_published_shifts(
    page: int = 1,
    limit: int = 10,
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    date: Optional[str] = None,
    status_filter: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    if location_id:
        query["location_id"] = location_id
    if date:
        query["date"] = date
    if status_filter:
        query["status"] = status_filter

    total_count = await db["shifts"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["shifts"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_shifts = await cursor.to_list(length=limit)

    return ShiftPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        shifts=[_format_shift_response(s) for s in raw_shifts]
    )

@shift_mgmt_router.get("/shifts/{shift_id}", response_model=ShiftResponse)
async def get_shift_by_id(
    shift_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": shift_id}, {"id": shift_id}]}
    doc = await db["shifts"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Shift not found")
    return _format_shift_response(doc)
