import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.client_list import (
    AdminRoomCreate, AdminRoomGridItem, AdminRoomGridPaginatedResponse,
    RoomDrawerDetailResponse, AdminRoomLocationDropdownItem, AdminRoomLocationDropdownResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

rooms_global_router = APIRouter(prefix="/manager", tags=["Manager Room Management"])

@rooms_global_router.get("/dropdowns/locations", response_model=AdminRoomLocationDropdownResponse, summary="Get Room Locations Dropdown")
async def get_room_locations_dropdown(
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    
    cursor = db["locations"].find(query).sort("name", 1)
    raw_locs = await cursor.to_list(length=None)
    
    locations = []
    for l in raw_locs:
        locations.append(AdminRoomLocationDropdownItem(
            id=str(l.get("_id") or l.get("id")),
            name=l.get("name", ""),
            total_rooms=l.get("rooms_count", 0)
        ))
        
    return AdminRoomLocationDropdownResponse(locations=locations)


@rooms_global_router.get("/rooms", response_model=AdminRoomGridPaginatedResponse, summary="Global Rooms Grid Page (Image 2)")
async def get_global_rooms_grid(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    room_id: Optional[str] = None,
    location_id: Optional[str] = None,
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    
    if room_id:
        query["$or"] = [{"_id": room_id}, {"id": room_id}, {"room_id": room_id}]

    if location_id:
        query["location_id"] = location_id

    if client_id:
        loc_docs = await db["locations"].find({"client_id": client_id}).to_list(length=1000)
        loc_ids = [str(l.get("_id") or l.get("id")) for l in loc_docs]
        if "location_id" in query:
            pass
        elif loc_ids:
            if "$or" in query:
                existing_or = query.pop("$or")
                query["$and"] = [{"$or": existing_or}, {"$or": [{"client_id": client_id}, {"location_id": {"$in": loc_ids}}]}]
            else:
                query["$or"] = [{"client_id": client_id}, {"location_id": {"$in": loc_ids}}]
        else:
            query["client_id"] = client_id

    if search:
        search_filter = [
            {"name": {"$regex": search, "$options": "i"}},
            {"room_name": {"$regex": search, "$options": "i"}},
            {"room_type": {"$regex": search, "$options": "i"}}
        ]
        if "$and" in query:
            query["$and"].append({"$or": search_filter})
        elif "$or" in query:
            existing_or = query.pop("$or")
            query["$and"] = [{"$or": existing_or}, {"$or": search_filter}]
        else:
            query["$or"] = search_filter

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    loc_ids = [str(r.get("location_id")) for r in raw_rooms if r.get("location_id")]
    client_ids = [str(r.get("client_id")) for r in raw_rooms if r.get("client_id")]

    locations_map = {}
    if loc_ids:
        loc_or = [{"_id": {"$in": loc_ids}}, {"id": {"$in": loc_ids}}]
        async for l in db["locations"].find({"$or": loc_or}):
            for k in (l.get("_id"), l.get("id")):
                if k:
                    locations_map[str(k)] = l
            if l.get("client_id"):
                client_ids.append(str(l["client_id"]))

    clients_map = {}
    if client_ids:
        cli_or = [{"_id": {"$in": client_ids}}, {"id": {"$in": client_ids}}]
        async for c in db["client_list"].find({"$or": cli_or}):
            for k in (c.get("_id"), c.get("id")):
                if k:
                    clients_map[str(k)] = c.get("company_name", "Client")

    items = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
        rname = r.get("room_name") or r.get("name") or "Room"
        rtype = r.get("room_type") or r.get("type") or "standard"
        lid = str(r.get("location_id") or "")
        ldoc = locations_map.get(lid, {})
        lname = str(r.get("location_name") or ldoc.get("name") or "")
        cid = str(r.get("client_id") or ldoc.get("client_id") or "")
        cname = str(r.get("company_name") or clients_map.get(cid) or ldoc.get("company_name") or "")

        freq = r.get("monthly_cleaning_frequency", 4)
        tasks_raw = r.get("tasks", [])
        photo_cnt = 0
        if isinstance(tasks_raw, list) and tasks_raw:
            for t in tasks_raw:
                if isinstance(t, dict):
                    photo_cnt += len(t.get("photo", []))
        if photo_cnt == 0:
            photo_cnt = len(r.get("required_photos", [])) if r.get("required_photos") else (r.get("photo_number") or r.get("required_photos_count", 0))

        t_cnt = len(tasks_raw) if tasks_raw else (r.get("task_number") or r.get("tasks_count", 0))
        ctype = r.get("clean_type") or r.get("cleaning_type") or "standard"
        u_at = r.get("updated_at") if isinstance(r.get("updated_at"), datetime) else (r.get("created_at") if isinstance(r.get("created_at"), datetime) else datetime.now(timezone.utc))

        items.append(AdminRoomGridItem(
            room_id=rid,
            room_name=rname,
            room_type=rtype,
            client_id=cid,
            company_name=cname or "Client Company",
            location_id=lid,
            location_name=lname or "Location Name",
            monthly_cleaning_frequency=freq,
            photo_number=photo_cnt,
            total_photos_required=photo_cnt,
            task_number=t_cnt,
            clean_type=ctype,
            updated_at=u_at
        ))

    return AdminRoomGridPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=items
    )

@rooms_global_router.post("/rooms", response_model=AdminRoomGridItem, status_code=status.HTTP_201_CREATED, summary="Add New Room Modal API (Text Prompt 1 + Image 2)", include_in_schema=False)
async def create_admin_room(
    room_in: AdminRoomCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_doc = await db["locations"].find_one({"$or": [{"_id": room_in.location_id}, {"id": room_in.location_id}]})
    lname = l_doc.get("name", "Location") if l_doc else "Location"
    cid = l_doc.get("client_id", "") if l_doc else ""
    cname = l_doc.get("company_name", "") if l_doc else ""

    now = datetime.now(timezone.utc)
    room_id = f"room_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": room_id,
        "id": room_id,
        "location_id": room_in.location_id,
        "location_name": lname,
        "client_id": cid,
        "company_name": cname,
        "name": room_in.name,
        "room_name": room_in.name,
        "room_type": room_in.room_type,
        "floor": room_in.floor,
        "est_cleaning_duration_minutes": room_in.est_cleaning_duration_minutes,
        "monthly_cleaning_frequency": room_in.monthly_cleaning_frequency,
        "required_photos_count": room_in.required_photos_count,
        "tasks_count": room_in.tasks_count,
        "cleaning_plan_name": room_in.cleaning_plan_name or "Standard Clean",
        "clean_type": room_in.cleaning_plan_name or "standard",
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["rooms"].insert_one(doc)
    if l_doc:
        await db["locations"].update_one(
            {"$or": [{"_id": room_in.location_id}, {"id": room_in.location_id}]},
            {"$inc": {"rooms_count": 1, "number_of_rooms": 1}}
        )

    return AdminRoomGridItem(
        room_id=room_id,
        room_name=room_in.name,
        room_type=room_in.room_type,
        client_id=cid,
        company_name=cname or "Client Company",
        location_id=room_in.location_id,
        location_name=lname,
        monthly_cleaning_frequency=room_in.monthly_cleaning_frequency,
        photo_number=room_in.required_photos_count,
        task_number=room_in.tasks_count,
        clean_type=room_in.cleaning_plan_name or "standard",
        updated_at=now
    )

@rooms_global_router.get("/rooms/{room_id}/drawer", response_model=RoomDrawerDetailResponse, summary="Room Details Drawer API (Image 3)", include_in_schema=False)
async def get_room_drawer_details(
    room_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    r_query = {"$or": [{"_id": room_id}, {"id": room_id}]}
    rdoc = await db["rooms"].find_one(r_query)
    if not rdoc:
        raise HTTPException(status_code=404, detail="Room not found")

    rid = str(rdoc.get("_id") or rdoc.get("id"))
    rname = rdoc.get("name") or rdoc.get("room_name") or "Room"
    rtype = rdoc.get("room_type") or rdoc.get("type") or "Standard"

    lid = rdoc.get("location_id", "")
    ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]}) if lid else None
    lname = ldoc.get("name", "Location") if ldoc else "Location"

    fl = rdoc.get("floor", 1)
    dur = rdoc.get("est_cleaning_duration_minutes") or rdoc.get("duration", 30)
    tasks_raw = rdoc.get("tasks", [])
    photo_cnt = 0
    if isinstance(tasks_raw, list) and tasks_raw:
        for t in tasks_raw:
            if isinstance(t, dict):
                photo_cnt += len(t.get("photo", []))
    if photo_cnt == 0:
        photo_cnt = len(rdoc.get("required_photos", [])) or rdoc.get("required_photos_count", 0)
    t_cnt = len(tasks_raw) or rdoc.get("tasks_count", 0)
    cp_name = rdoc.get("cleaning_plan_name") or "Standard Clean"

    code = f"R{rid[-3:].upper()}" if len(rid) >= 3 else "R01"

    return RoomDrawerDetailResponse(
        room_id=rid,
        room_code=code,
        room_name=rname,
        room_type=rtype,
        floor_label=f"Floor {fl}",
        location_name=lname,
        cleaning_plan_name=cp_name,
        duration_minutes=dur,
        required_photos_count=photo_cnt,
        tasks_count=t_cnt
    )
