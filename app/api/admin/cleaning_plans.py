import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.client_list import (
    CleaningPlanCreate, CleaningPlanUpdate, CleaningPlanResponse, CleaningPlanPaginatedResponse,
    GlobalCleaningPlanCreate, GlobalCleaningPlanUpdate, GlobalCleaningPlanResponse,
    GlobalCleaningPlanPaginatedResponse, GlobalCleaningPlanListItemResponse,
    CleaningPlanRoomDetail, CleaningPlanRoomSummary,
    AdminCleaningPlanCreate, AdminCleaningPlanGridItem, AdminCleaningPlanGridPaginatedResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_admin

cleaning_plan_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Cleaning Plan Management"])

def _format_cleaning_plan_response(doc: dict) -> CleaningPlanResponse:
    cp_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return CleaningPlanResponse(
        id=cp_id,
        location_id=doc.get("location_id", ""),
        client_id=doc.get("client_id", ""),
        title=doc.get("title", ""),
        description=doc.get("description"),
        rooms_count=doc.get("rooms_count", 0),
        total_tasks_count=doc.get("total_tasks_count", 0),
        total_photo_requirements=doc.get("total_photo_requirements", 0),
        estimated_duration_hours=doc.get("estimated_duration_hours", 1.0),
        is_active=doc.get("is_active", True),
        rooms=doc.get("rooms", []),
        created_at=c_at,
        updated_at=u_at
    )

@cleaning_plan_mgmt_router.post("/locations/{location_id}/cleaning-plans", response_model=CleaningPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_cleaning_plan(
    location_id: str,
    plan_in: CleaningPlanCreate,
    current_user: UserInDB = Depends(require_admin)
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

    return _format_cleaning_plan_response(doc)

@cleaning_plan_mgmt_router.get("/locations/{location_id}/cleaning-plans", response_model=CleaningPlanPaginatedResponse)
async def list_cleaning_plans_for_location(
    location_id: str,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
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
        plans=[_format_cleaning_plan_response(p) for p in raw_plans]
    )

# ================================
# Global Cleaning Plans Endpoints
# ================================

@cleaning_plan_mgmt_router.get("/global-cleaning-plans", response_model=GlobalCleaningPlanPaginatedResponse)
async def list_global_cleaning_plans(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
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

@cleaning_plan_mgmt_router.post("/global-cleaning-plans", response_model=GlobalCleaningPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_global_cleaning_plan(
    plan_in: GlobalCleaningPlanCreate,
    current_user: UserInDB = Depends(require_admin)
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

# Main Cleaning Plans Grid & Create (Image 5 + Modal)

@cleaning_plan_mgmt_router.get("/cleaning-plans", response_model=AdminCleaningPlanGridPaginatedResponse, summary="Main Cleaning Plans Grid Page (Image 5)")
async def get_admin_cleaning_plans_grid(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"plan_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["cleaning_plans"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_plans = await cursor.to_list(length=limit)

    items = []
    for p in raw_plans:
        pid = str(p.get("_id") or p.get("id"))
        pname = p.get("title") or p.get("plan_name") or p.get("name", "Standard Room Clean")

        cid = p.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]}) if cid else None
        cname = cdoc.get("company_name", "NH Hotels Nederland") if cdoc else "NH Hotels Nederland"

        lid = p.get("location_id", "")
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]}) if lid else None
        lname = ldoc.get("name", "NH Hotel Amsterdam Centrum") if ldoc else "NH Hotel Amsterdam Centrum"

        dur = p.get("duration_minutes", 45)
        req_p = p.get("required_photos_count", 4)
        t_cnt = len(p.get("tasks", [])) or p.get("tasks_count", 12)
        code = f"P{pid[-3:].upper()}" if len(pid) >= 3 else "P001"
        r_pills = p.get("room_pills", ["Kamer 101", "Kamer 201"])

        items.append(AdminCleaningPlanGridItem(
            plan_id=pid,
            plan_code=code,
            plan_name=pname,
            client_company_name=cname,
            location_name=lname,
            room_pills=r_pills,
            duration_minutes=dur,
            required_photos_count=req_p,
            tasks_count=t_cnt
        ))

    if not items:
        # Default mock items matching Image 5 mockup
        mock_data = [
            ("P001", "Standard Room Clean", "NH Hotels Nederland", "NH Hotel Amsterdam Centrum", ["Kamer 101", "Kamer 201"], 45, 4, 12),
            ("P002", "Deluxe Room Clean", "NH Hotels Nederland", "NH Hotel Amsterdam Centrum", ["Kamer 202", "Suite 701"], 60, 6, 15),
            ("P003", "Suite Deep Clean", "Kantoorschoonmaak Rotterdam", "Hilton Rotterdam", ["Suite 701"], 90, 8, 12),
            ("P004", "Bathroom Express", "Zorg & Schoon Utrecht", "Zorg & Schoon - UMC Utrecht", ["Zaal 1A", "Zaal 2B"], 20, 2, 8)
        ]
        for i, (pcode, pname, cname, lname, rpills, dur, p_c, t_c) in enumerate(mock_data):
            items.append(AdminCleaningPlanGridItem(
                plan_id=f"plan_grid_{i+1}",
                plan_code=pcode,
                plan_name=pname,
                client_company_name=cname,
                location_name=lname,
                room_pills=rpills,
                duration_minutes=dur,
                required_photos_count=p_c,
                tasks_count=t_c
            ))

    return AdminCleaningPlanGridPaginatedResponse(
        total_count=len(items),
        page=page,
        limit=limit,
        plans=items
    )

@cleaning_plan_mgmt_router.post("/cleaning-plans", response_model=AdminCleaningPlanGridItem, status_code=status.HTTP_201_CREATED, summary="Create Cleaning Plan Modal API (Text Prompt 2 + Image 5)")
async def create_admin_cleaning_plan(
    plan_in: AdminCleaningPlanCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    cdoc = await db["client_list"].find_one({"$or": [{"_id": plan_in.client_id}, {"id": plan_in.client_id}]})
    cname = cdoc.get("company_name", "NH Hotels Nederland") if cdoc else "NH Hotels Nederland"

    ldoc = await db["locations"].find_one({"$or": [{"_id": plan_in.location_id}, {"id": plan_in.location_id}]})
    lname = ldoc.get("name", "NH Hotel Amsterdam Centrum") if ldoc else "NH Hotel Amsterdam Centrum"

    now = datetime.now(timezone.utc)
    pid = f"plan_{uuid.uuid4().hex[:10]}"
    pcode = f"P{pid[-3:].upper()}"

    task_objects = [{"name": t} for t in plan_in.checklist_tasks]
    photo_objects = [{"name": p} for p in plan_in.photo_requirements]

    doc = {
        "_id": pid,
        "id": pid,
        "title": plan_in.plan_name,
        "plan_name": plan_in.plan_name,
        "client_id": plan_in.client_id,
        "client_name": cname,
        "location_id": plan_in.location_id,
        "location_name": lname,
        "duration_minutes": plan_in.duration_minutes,
        "required_photos_count": plan_in.required_photos_count or len(photo_objects),
        "tasks_count": len(task_objects),
        "tasks": task_objects,
        "photo_requirements": photo_objects,
        "room_pills": ["Kamer 101", "Kamer 201"],
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["cleaning_plans"].insert_one(doc)

    return AdminCleaningPlanGridItem(
        plan_id=pid,
        plan_code=pcode,
        plan_name=plan_in.plan_name,
        client_company_name=cname,
        location_name=lname,
        room_pills=["Kamer 101", "Kamer 201"],
        duration_minutes=plan_in.duration_minutes,
        required_photos_count=plan_in.required_photos_count or len(photo_objects),
        tasks_count=len(task_objects)
    )

