import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional, Union
from app.core.database import get_database
from app.schemas.client_list import (
    CleaningPlanCreate, CleaningPlanUpdate, CleaningPlanResponse, CleaningPlanPaginatedResponse,
    GlobalCleaningPlanCreate, GlobalCleaningPlanUpdate, GlobalCleaningPlanResponse,
    GlobalCleaningPlanPaginatedResponse, GlobalCleaningPlanListItemResponse,
    CleaningPlanRoomDetail, CleaningPlanWorkerDetail,
    ManagerCleaningPlanCreate, ManagerCleaningPlanUpdate,
    ManagerCleaningPlanDetailResponse, ManagerCleaningPlanListItemResponse,
    ManagerCleaningPlanPaginatedResponse,
    CleaningTaskCreate, CleaningTaskResponse,
    RequiredPhotoCreate, RequiredPhotoResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

cleaning_plan_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Cleaning Plan Management"])


# ==========================================
# Helpers for Dynamic Aggregations
# ==========================================

async def _resolve_rooms_data(room_ids: List[str], db) -> List[CleaningPlanRoomDetail]:
    if not room_ids:
        return []
    cursor = db["rooms"].find({"$or": [{"_id": {"$in": room_ids}}, {"id": {"$in": room_ids}}, {"room_id": {"$in": room_ids}}]})
    raw_rooms = await cursor.to_list(length=len(room_ids) * 2)

    room_details = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id"))
        r_name = r.get("room_name") or r.get("name", "Room")
        r_type = r.get("room_type") or r.get("type", "standard")
        flr = r.get("floor", 1)
        dur = r.get("duration") or r.get("est_cleaning_duration_minutes", 30)
        freq = r.get("monthly_cleaning_frequency", 4)
        c_type = r.get("clean_type") or r.get("cleaning_type", "standard")

        # Format tasks
        raw_tasks = r.get("tasks", [])
        tasks = []
        for t in raw_tasks:
            if isinstance(t, dict):
                t_id = str(t.get("id") or t.get("_id") or uuid.uuid4().hex[:8])
                tasks.append(CleaningTaskResponse(
                    id=t_id,
                    name=t.get("name", "Task"),
                    frequency_type=t.get("frequency_type", "every_visit")
                ))
            elif isinstance(t, str):
                tasks.append(CleaningTaskResponse(
                    id=uuid.uuid4().hex[:8],
                    name=t,
                    frequency_type="every_visit"
                ))

        # Format photos
        raw_photos = r.get("required_photos", [])
        photos = []
        for p in raw_photos:
            if isinstance(p, dict):
                p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                photos.append(RequiredPhotoResponse(
                    id=p_id,
                    name=p.get("name", "Photo"),
                    frequency_type=p.get("frequency_type", "every_visit")
                ))
            elif isinstance(p, str):
                photos.append(RequiredPhotoResponse(
                    id=uuid.uuid4().hex[:8],
                    name=p,
                    frequency_type="every_visit"
                ))

        room_details.append(CleaningPlanRoomDetail(
            room_id=rid,
            room_name=r_name,
            room_type=r_type,
            floor=flr,
            duration=dur,
            monthly_cleaning_frequency=freq,
            clean_type=c_type,
            tasks=tasks,
            required_photos=photos
        ))

    return room_details


async def _resolve_workers_data(worker_ids: List[str], db) -> List[CleaningPlanWorkerDetail]:
    if not worker_ids:
        return []
    cursor = db["users"].find({"$or": [{"_id": {"$in": worker_ids}}, {"id": {"$in": worker_ids}}]})
    raw_workers = await cursor.to_list(length=len(worker_ids) * 2)

    workers = []
    for w in raw_workers:
        wid = str(w.get("_id") or w.get("id"))
        w_name = w.get("full_name") or w.get("name") or "Worker"
        workers.append(CleaningPlanWorkerDetail(
            worker_id=wid,
            name=w_name,
            email=w.get("email"),
            role=w.get("role", "worker"),
            phone=w.get("phone"),
            profile_picture=w.get("profile_picture")
        ))
    return workers


async def _format_manager_cleaning_plan_detail(doc: dict, db) -> ManagerCleaningPlanDetailResponse:
    pid = str(doc.get("_id") or doc.get("id"))
    title = doc.get("title") or doc.get("plan_name", "Cleaning Plan")
    desc = doc.get("description", "")
    cid = doc.get("client_id", "")
    cname = doc.get("company_name") or doc.get("client_name", "")
    lid = doc.get("location_id", "")
    lname = doc.get("location_name", "")

    # Backfill client & location names if missing
    if not cname and cid:
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        if cdoc:
            cname = cdoc.get("company_name", "")
    if not lname and lid:
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        if ldoc:
            lname = ldoc.get("name", "")
            if not cname:
                cname = ldoc.get("company_name", "")

    room_ids = doc.get("room_ids", [])
    worker_ids = doc.get("worker_ids", [])

    # Fetch room and worker details
    rooms_data = await _resolve_rooms_data(room_ids, db)
    workers_data = await _resolve_workers_data(worker_ids, db)

    # Format additional tasks & photos
    add_tasks_raw = doc.get("additional_tasks", [])
    add_tasks = []
    for t in add_tasks_raw:
        if isinstance(t, dict):
            t_id = str(t.get("id") or t.get("_id") or uuid.uuid4().hex[:8])
            add_tasks.append(CleaningTaskResponse(
                id=t_id,
                name=t.get("name", "Task"),
                frequency_type=t.get("frequency_type", "every_visit")
            ))
        elif isinstance(t, str):
            add_tasks.append(CleaningTaskResponse(
                id=uuid.uuid4().hex[:8],
                name=t,
                frequency_type="every_visit"
            ))

    add_photos_raw = doc.get("additional_required_photos", [])
    add_photos = []
    for p in add_photos_raw:
        if isinstance(p, dict):
            p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
            add_photos.append(RequiredPhotoResponse(
                id=p_id,
                name=p.get("name", "Photo"),
                frequency_type=p.get("frequency_type", "every_visit")
            ))
        elif isinstance(p, str):
            add_photos.append(RequiredPhotoResponse(
                id=uuid.uuid4().hex[:8],
                name=p,
                frequency_type="every_visit"
            ))

    # Aggregated tasks and photos
    all_tasks = []
    for r in rooms_data:
        all_tasks.extend(r.tasks)
    all_tasks.extend(add_tasks)

    all_photos = []
    for r in rooms_data:
        all_photos.extend(r.required_photos)
    all_photos.extend(add_photos)

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ManagerCleaningPlanDetailResponse(
        id=pid,
        title=title,
        description=desc,
        client_id=cid,
        company_name=cname or "Client Company",
        location_id=lid,
        location_name=lname or "Location Name",
        room_ids=room_ids,
        rooms=rooms_data,
        rooms_count=len(rooms_data),
        worker_ids=worker_ids,
        workers=workers_data,
        workers_count=len(workers_data),
        additional_tasks=add_tasks,
        additional_required_photos=add_photos,
        all_tasks=all_tasks,
        all_required_photos=all_photos,
        total_tasks_count=len(all_tasks),
        total_photos_count=len(all_photos),
        frequency_type=doc.get("frequency_type") or doc.get("period", "weekly"),
        duration_minutes=doc.get("duration_minutes", 60),
        is_active=doc.get("is_active", True),
        created_at=c_at,
        updated_at=u_at
    )


async def _format_manager_cleaning_plan_list_item(doc: dict, db) -> ManagerCleaningPlanListItemResponse:
    pid = str(doc.get("_id") or doc.get("id"))
    title = doc.get("title") or doc.get("plan_name", "Cleaning Plan")
    cid = doc.get("client_id", "")
    cname = doc.get("company_name") or doc.get("client_name", "")
    lid = doc.get("location_id", "")
    lname = doc.get("location_name", "")

    # Backfill client & location names if missing
    if not cname and cid:
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        if cdoc:
            cname = cdoc.get("company_name", "")
    if not lname and lid:
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        if ldoc:
            lname = ldoc.get("name", "")
            if not cname:
                cname = ldoc.get("company_name", "")

    room_ids = doc.get("room_ids", [])
    worker_ids = doc.get("worker_ids", [])

    # Fetch room names
    room_names = []
    if room_ids:
        cursor_r = db["rooms"].find({"$or": [{"_id": {"$in": room_ids}}, {"id": {"$in": room_ids}}, {"room_id": {"$in": room_ids}}]})
        rooms_found = await cursor_r.to_list(length=len(room_ids) * 2)
        room_names = [r.get("room_name") or r.get("name", "Room") for r in rooms_found]

    # Fetch worker names
    worker_names = []
    if worker_ids:
        cursor_w = db["users"].find({"$or": [{"_id": {"$in": worker_ids}}, {"id": {"$in": worker_ids}}]})
        workers_found = await cursor_w.to_list(length=len(worker_ids) * 2)
        worker_names = [w.get("full_name") or w.get("name", "Worker") for w in workers_found]

    t_cnt = doc.get("total_tasks_count", 0)
    p_cnt = doc.get("total_photos_count", 0)

    # If count not stored in doc, calculate from room_ids + additional
    if t_cnt == 0 or p_cnt == 0:
        rooms_data = await _resolve_rooms_data(room_ids, db)
        add_t = doc.get("additional_tasks", [])
        add_p = doc.get("additional_required_photos", [])
        t_cnt = sum(len(r.tasks) for r in rooms_data) + len(add_t)
        p_cnt = sum(len(r.required_photos) for r in rooms_data) + len(add_p)

    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ManagerCleaningPlanListItemResponse(
        id=pid,
        title=title,
        client_id=cid,
        company_name=cname or "Client Company",
        location_id=lid,
        location_name=lname or "Location Name",
        room_ids=room_ids,
        room_names=room_names,
        rooms_count=len(room_ids),
        worker_ids=worker_ids,
        worker_names=worker_names,
        workers_count=len(worker_ids),
        total_tasks_count=t_cnt,
        total_photos_count=p_cnt,
        frequency_type=doc.get("frequency_type") or doc.get("period", "weekly"),
        duration_minutes=doc.get("duration_minutes", 60),
        is_active=doc.get("is_active", True),
        created_at=c_at,
        updated_at=u_at
    )


# ==========================================
# New Manager Cleaning Plan CRUD Endpoints
# ==========================================

@cleaning_plan_mgmt_router.post(
    "/cleaning-plans",
    response_model=ManagerCleaningPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Cleaning Plan"
)
async def create_manager_cleaning_plan(
    plan_in: ManagerCleaningPlanCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_doc = await db["locations"].find_one({"$or": [{"_id": plan_in.location_id}, {"id": plan_in.location_id}]})
    if not l_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    client_id = l_doc.get("client_id", "")
    company_name = l_doc.get("company_name", "")
    location_name = l_doc.get("name", "")

    if not company_name and client_id:
        c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}]})
        if c_doc:
            company_name = c_doc.get("company_name", "Client Company")

    now = datetime.now(timezone.utc)
    plan_id = f"plan_{uuid.uuid4().hex[:10]}"

    # Resolve room details
    rooms_data = await _resolve_rooms_data(plan_in.room_ids, db)

    add_tasks_dicts = [t.model_dump() for t in (plan_in.additional_tasks or [])]
    add_photos_dicts = [p.model_dump() for p in (plan_in.additional_required_photos or [])]

    total_tasks_count = sum(len(r.tasks) for r in rooms_data) + len(add_tasks_dicts)
    total_photos_count = sum(len(r.required_photos) for r in rooms_data) + len(add_photos_dicts)

    doc = {
        "_id": plan_id,
        "id": plan_id,
        "title": plan_in.title,
        "plan_name": plan_in.title,
        "description": plan_in.description or "",
        "client_id": client_id,
        "company_name": company_name,
        "location_id": plan_in.location_id,
        "location_name": location_name,
        "room_ids": plan_in.room_ids,
        "worker_ids": [],
        "frequency_type": plan_in.frequency_type,
        "period": plan_in.frequency_type,
        "duration_minutes": plan_in.duration_minutes,
        "additional_tasks": add_tasks_dicts,
        "additional_required_photos": add_photos_dicts,
        "total_tasks_count": total_tasks_count,
        "total_photos_count": total_photos_count,
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["cleaning_plans"].insert_one(doc)
    await db["locations"].update_one(
        {"$or": [{"_id": plan_in.location_id}, {"id": plan_in.location_id}]},
        {"$inc": {"cleaning_plans_count": 1}}
    )

    return await _format_manager_cleaning_plan_detail(doc, db)


@cleaning_plan_mgmt_router.get(
    "/cleaning-plans",
    response_model=ManagerCleaningPlanPaginatedResponse,
    summary="List Cleaning Plans"
)
async def list_manager_cleaning_plans(
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    room_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    if location_id:
        query["location_id"] = location_id
    if room_id:
        query["room_ids"] = room_id
    if worker_id:
        query["worker_ids"] = worker_id
    if search:
        search_filter = [
            {"title": {"$regex": search, "$options": "i"}},
            {"plan_name": {"$regex": search, "$options": "i"}},
            {"location_name": {"$regex": search, "$options": "i"}},
            {"company_name": {"$regex": search, "$options": "i"}},
            {"client_name": {"$regex": search, "$options": "i"}}
        ]
        if "$or" in query:
            query = {"$and": [query, {"$or": search_filter}]}
        else:
            query["$or"] = search_filter

    total_count = await db["cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_plans = await cursor.to_list(length=limit)

    plan_items = [await _format_manager_cleaning_plan_list_item(p, db) for p in raw_plans]

    return ManagerCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        plans=plan_items
    )


@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Get Full Cleaning Plan Details"
)
async def get_manager_cleaning_plan_detail(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": plan_id}, {"id": plan_id}]})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    return await _format_manager_cleaning_plan_detail(plan_doc, db)


@cleaning_plan_mgmt_router.patch(
    "/cleaning-plans/{plan_id}",
    response_model=ManagerCleaningPlanDetailResponse,
    summary="Update Cleaning Plan"
)
async def update_manager_cleaning_plan(
    plan_id: str,
    plan_in: ManagerCleaningPlanUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    update_fields = plan_in.model_dump(exclude_unset=True)
    update_fields["updated_at"] = datetime.now(timezone.utc)

    if "title" in update_fields:
        update_fields["plan_name"] = update_fields["title"]
    if "frequency_type" in update_fields:
        update_fields["period"] = update_fields["frequency_type"]

    # Re-calculate counts if room_ids or additional tasks/photos changed
    merged_room_ids = update_fields.get("room_ids", plan_doc.get("room_ids", []))
    merged_add_tasks = update_fields.get("additional_tasks", plan_doc.get("additional_tasks", []))
    merged_add_photos = update_fields.get("additional_required_photos", plan_doc.get("additional_required_photos", []))

    rooms_data = await _resolve_rooms_data(merged_room_ids, db)
    update_fields["total_tasks_count"] = sum(len(r.tasks) for r in rooms_data) + len(merged_add_tasks)
    update_fields["total_photos_count"] = sum(len(r.required_photos) for r in rooms_data) + len(merged_add_photos)

    await db["cleaning_plans"].update_one(query, {"$set": update_fields})
    updated_doc = await db["cleaning_plans"].find_one(query)

    return await _format_manager_cleaning_plan_detail(updated_doc, db)


@cleaning_plan_mgmt_router.delete(
    "/cleaning-plans/{plan_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Cleaning Plan"
)
async def delete_manager_cleaning_plan(
    plan_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": plan_id}, {"id": plan_id}]}
    plan_doc = await db["cleaning_plans"].find_one(query)
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    loc_id = plan_doc.get("location_id")
    await db["cleaning_plans"].delete_one(query)

    if loc_id:
        await db["locations"].update_one(
            {"$or": [{"_id": loc_id}, {"id": loc_id}]},
            {"$inc": {"cleaning_plans_count": -1}}
        )

    return {"message": "Cleaning plan deleted successfully"}


# ==========================================
# Legacy Endpoints (Hidden from OpenAPI Schema)
# ==========================================

def _format_legacy_cleaning_plan_response(doc: dict) -> CleaningPlanResponse:
    cp_id = str(doc.get("_id") or doc.get("id"))
    return CleaningPlanResponse(
        id=cp_id,
        plan_name=doc.get("title") or doc.get("plan_name", "Cleaning Plan"),
        task_count=doc.get("total_tasks_count", 0),
        tasks=[]
    )

@cleaning_plan_mgmt_router.post(
    "/locations/{location_id}/cleaning-plans",
    response_model=CleaningPlanResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False
)
async def create_cleaning_plan(
    location_id: str,
    plan_in: CleaningPlanCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
    if not l_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(timezone.utc)
    plan_id = f"plan_{uuid.uuid4().hex[:10]}"
    client_id = l_doc.get("client_id", "")

    doc = {
        "_id": plan_id,
        "id": plan_id,
        "location_id": location_id,
        "client_id": client_id,
        **plan_in.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["cleaning_plans"].insert_one(doc)
    await db["locations"].update_one(
        {"$or": [{"_id": location_id}, {"id": location_id}]},
        {"$inc": {"cleaning_plans_count": 1}}
    )

    return _format_legacy_cleaning_plan_response(doc)


@cleaning_plan_mgmt_router.get(
    "/locations/{location_id}/cleaning-plans",
    response_model=CleaningPlanPaginatedResponse,
    include_in_schema=False
)
async def list_cleaning_plans_for_location(
    location_id: str,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"location_id": location_id}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}

    total_count = await db["cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_plans = await cursor.to_list(length=limit)

    return CleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        cleaning_plans=[_format_legacy_cleaning_plan_response(p) for p in raw_plans]
    )


@cleaning_plan_mgmt_router.get(
    "/global-cleaning-plans",
    response_model=GlobalCleaningPlanPaginatedResponse,
    include_in_schema=False
)
async def list_global_cleaning_plans(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    if location_id:
        query["location_id"] = location_id
    if search:
        query["title"] = {"$regex": search, "$options": "i"}

    total_count = await db["global_cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["global_cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_plans = await cursor.to_list(length=limit)

    items = []
    for p in raw_plans:
        cid = p.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        cname = cdoc.get("company_name", "Client") if cdoc else "Client"

        lid = p.get("location_id", "")
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        lname = ldoc.get("name", "Location") if ldoc else "Location"

        pid = str(p.get("_id") or p.get("id"))
        cat = p.get("created_at") if isinstance(p.get("created_at"), datetime) else datetime.now(timezone.utc)

        items.append(GlobalCleaningPlanListItemResponse(
            plan_id=pid,
            title=p.get("title", ""),
            client_id=cid,
            client_name=cname,
            location_id=lid,
            location_name=lname,
            rooms_count=p.get("rooms_count", 0),
            total_tasks_count=p.get("total_tasks_count", 0),
            total_photo_requirements=p.get("total_photo_requirements", 0),
            estimated_duration_hours=p.get("estimated_duration_hours", 1.0),
            created_at=cat
        ))

    return GlobalCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        plans=items
    )


@cleaning_plan_mgmt_router.post(
    "/global-cleaning-plans",
    response_model=GlobalCleaningPlanResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False
)
async def create_global_cleaning_plan(
    plan_in: GlobalCleaningPlanCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    cdoc = await db["client_list"].find_one({"$or": [{"_id": plan_in.client_id}, {"id": plan_in.client_id}]})
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    ldoc = await db["locations"].find_one({"$or": [{"_id": plan_in.location_id}, {"id": plan_in.location_id}]})
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(timezone.utc)
    pid = f"gplan_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": pid,
        "id": pid,
        "title": plan_in.title,
        "description": plan_in.description,
        "client_id": plan_in.client_id,
        "location_id": plan_in.location_id,
        "rooms_count": len(plan_in.rooms or []),
        "total_tasks_count": 0,
        "total_photo_requirements": 0,
        "estimated_duration_hours": plan_in.estimated_duration_hours or 1.0,
        "rooms": [r.model_dump() for r in (plan_in.rooms or [])],
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["global_cleaning_plans"].insert_one(doc)

    return GlobalCleaningPlanResponse(
        id=pid,
        title=plan_in.title,
        description=plan_in.description,
        client_id=plan_in.client_id,
        client_name=cdoc.get("company_name", "Client"),
        location_id=plan_in.location_id,
        location_name=ldoc.get("name", "Location"),
        rooms_count=len(plan_in.rooms or []),
        total_tasks_count=0,
        total_photo_requirements=0,
        estimated_duration_hours=plan_in.estimated_duration_hours or 1.0,
        rooms=[],
        created_at=now,
        updated_at=now
    )
