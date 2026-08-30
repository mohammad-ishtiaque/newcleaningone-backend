import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.shift import (
    ShiftDraftCreate, ShiftDraftResponse, ShiftDraftPaginatedResponse,
    WorkerDropdownItem, WorkerDropdownPaginatedResponse, ShiftAssignRequest, ShiftResponse
)
from app.schemas.client_list import (
    LocationDropdownItemResponse, LocationDropdownPaginatedResponse,
    RoomDropdownItemResponse, RoomDropdownPaginatedResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.shifts import _format_shift_response

roster_drafts_dropdowns_router = APIRouter()


# ================================
# Shift Creation Step 1: Create Shift Draft
# ================================

@roster_drafts_dropdowns_router.post("/drafts", response_model=ShiftDraftResponse, status_code=status.HTTP_201_CREATED, summary="Step 1: Create Shift Draft")
async def create_roster_shift_draft(
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
        "repeat_shift": draft_in.repeat_shift or "Does not repeat",
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
        repeat_shift=draft_in.repeat_shift or "Does not repeat",
        shift_notes=draft_in.shift_notes,
        cleaning_plan_id=draft_in.cleaning_plan_id,
        rooms=[],
        total_tasks_count=0,
        total_photo_required=0,
        status="draft",
        created_at=now,
        updated_at=now
    )


@roster_drafts_dropdowns_router.get("/drafts", response_model=ShiftDraftPaginatedResponse, summary="List Shift Drafts")
async def list_roster_shift_drafts(
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
            repeat_shift=d.get("repeat_shift", "Does not repeat"),
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
# Shift Creation Step 2: Assign Employees & Publish Shift
# ================================

@roster_drafts_dropdowns_router.post("/shifts/assign", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED, summary="Step 2: Assign Employees & Publish Shift")
async def assign_workers_and_publish_roster_shift(
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
            role_label = "team_leader" if (assign_in.team_leader_id and str(w_user.get("_id")) == assign_in.team_leader_id) else "cleaning_specialist"
            assigned_workers.append({
                "worker_id": str(w_user.get("_id")),
                "name": w_user.get("full_name", "Worker"),
                "profile_picture": w_user.get("profile_photo"),
                "worker_type": str(w_user.get("worker_type", "employee")),
                "shift_role": role_label
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
        "repeat_shift": draft_doc.get("repeat_shift", "Does not repeat"),
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


# ================================
# Roster Dropdowns (Clients, Locations, Rooms, Workers)
# ================================

@roster_drafts_dropdowns_router.get("/dropdowns/clients", summary="Roster Client Dropdown List")
async def get_roster_client_dropdowns(
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"is_active": True}
    if search:
        query["company_name"] = {"$regex": search, "$options": "i"}

    clients = await db["client_list"].find(query).sort("company_name", 1).to_list(length=100)
    return [
        {
            "client_id": str(c.get("_id") or c.get("id")),
            "company_name": c.get("company_name", "Client"),
            "email": c.get("email", "")
        }
        for c in clients
    ]


from app.services.client_helper import resolve_client_id_aliases

@roster_drafts_dropdowns_router.get("/dropdowns/locations", response_model=LocationDropdownPaginatedResponse, summary="Roster Location Dropdown List")
async def get_roster_location_dropdowns(
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query_parts = []
    if client_id:
        c_aliases = await resolve_client_id_aliases(client_id, db)
        query_parts.append({"$or": [{"client_id": {"$in": c_aliases}}, {"client_ids": {"$in": c_aliases}}]})
    if search:
        query_parts.append({"name": {"$regex": search, "$options": "i"}})

    query = {"$and": query_parts} if len(query_parts) > 1 else (query_parts[0] if query_parts else {})

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("name", 1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    dropdowns = []
    for l in raw_locs:
        cid = l.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        cname = cdoc.get("company_name", "Client") if cdoc else "Client"
        lid = str(l.get("_id") or l.get("id"))

        dropdowns.append(LocationDropdownItemResponse(
            id=lid,
            name=l.get("name", ""),
            client_id=cid,
            company_name=cname,
            address=l.get("address", "")
        ))

    return LocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=dropdowns
    )


@roster_drafts_dropdowns_router.get("/dropdowns/rooms", response_model=RoomDropdownPaginatedResponse, summary="Roster Room Dropdown List")
async def get_roster_room_dropdowns(
    location_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if location_id:
        query["location_id"] = location_id
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("name", 1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    dropdowns = []
    for r in raw_rooms:
        lid = r.get("location_id", "")
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        lname = ldoc.get("name", "Location") if ldoc else "Location"
        rid = str(r.get("_id") or r.get("id"))

        dropdowns.append(RoomDropdownItemResponse(
            room_id=rid,
            room_name=r.get("name", ""),
            location_id=lid,
            location_name=lname,
            floor=r.get("floor", 1),
            cleaning_type=r.get("cleaning_type", "standard"),
            duration=r.get("est_cleaning_duration_minutes", 30)
        ))

    return RoomDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=dropdowns
    )


@roster_drafts_dropdowns_router.get("/dropdowns/workers", response_model=WorkerDropdownPaginatedResponse, summary="Roster Employee/Worker List (Step 2 Selection)")
async def get_roster_worker_dropdowns(
    worker_type: Optional[str] = None,  # all, employee, freelancer
    status_filter: Optional[str] = None,  # available, on_shift, off_duty
    search: Optional[str] = None,
    target_date: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
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

    if worker_type and worker_type.lower() != "all":
        query["worker_type"] = worker_type.lower()

    if search:
        search_filter = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}}
        ]
        query = {"$and": [query, {"$or": search_filter}]}

    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("full_name", 1).skip(skip).limit(limit)
    raw_workers = await cursor.to_list(length=limit)

    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Find workers currently on shift today
    running_shifts = await db["shifts"].find({"date": target_date}).to_list(length=300)
    on_shift_worker_ids = set()
    for s in running_shifts:
        for w in s.get("workers", []):
            on_shift_worker_ids.add(str(w.get("worker_id")))

    items = []
    for w in raw_workers:
        wid = str(w.get("_id"))
        w_status = "on_shift" if wid in on_shift_worker_ids else "available"
        if status_filter and status_filter.lower() != w_status:
            continue

        items.append(WorkerDropdownItem(
            worker_id=wid,
            name=w.get("full_name", "Worker"),
            profile_picture=w.get("profile_photo"),
            status=w_status,
            worker_type=str(w.get("worker_type", "employee")),
            position=w.get("position")
        ))

    return WorkerDropdownPaginatedResponse(
        total_count=len(items),
        page=page,
        limit=limit,
        workers=items
    )
