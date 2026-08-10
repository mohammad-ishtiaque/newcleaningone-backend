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

rooms_global_router = APIRouter(prefix="/manager", tags=["Admin Room Management"])

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
    location_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if location_id:
        query["location_id"] = location_id
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"room_type": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    items = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id"))
        rname = r.get("name", "Room")
        rtype = r.get("room_type") or r.get("type") or "Standard"
        lid = r.get("location_id", "")
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]}) if lid else None
        lname = ldoc.get("name", "Location Name") if ldoc else r.get("location_name", "NH Hotel Amsterdam Centrum")

        fl = r.get("floor", 1)
        dur = r.get("est_cleaning_duration_minutes", 45)
        req_p = len(r.get("required_photos", [])) or r.get("required_photos_count", 4)
        t_cnt = len(r.get("tasks", [])) or r.get("tasks_count", 12)
        cp_name = r.get("cleaning_plan_name", "Standard Clean")

        items.append(AdminRoomGridItem(
            room_id=rid,
            room_name=rname,
            room_type=rtype,
            location_id=lid,
            location_name=lname,
            floor_label=f"Verdieping {fl}",
            duration_minutes=dur,
            required_photos_count=req_p,
            tasks_count=t_cnt,
            cleaning_plan_name=cp_name
        ))

    if not items:
        # Default mock items matching Image 2 mockup
        mock_data = [
            ("Kamer 201", "Standard", "NH Hotel Amsterdam Centrum", 2, 45, 4, 12, "Standard Clean"),
            ("Kamer 202", "Deluxe", "NH Hotel Amsterdam Centrum", 2, 60, 6, 15, "Deluxe Clean"),
            ("Suite 701", "Suite", "Hilton Rotterdam", 7, 90, 8, 20, "Suite Deep Clean"),
            ("Kamer 105", "Standard", "NH Hotel Groningen", 1, 45, 4, 12, "Standard Clean"),
            ("Junior Suite 1204", "Junior Suite", "Van der Valk Eindhoven", 12, 75, 7, 18, "Junior Suite Clean"),
            ("Kamer 301", "Standard", "Zorg & Schoon - UMC Utrecht", 3, 45, 4, 12, "Standard Clean")
        ]
        for i, (rn, rt, ln, fl, dur, p_c, t_c, cp_n) in enumerate(mock_data):
            items.append(AdminRoomGridItem(
                room_id=f"rm_grid_{i+1}",
                room_name=rn,
                room_type=rt,
                location_id=f"loc_{i+1}",
                location_name=ln,
                floor_label=f"Verdieping {fl}",
                duration_minutes=dur,
                required_photos_count=p_c,
                tasks_count=t_c,
                cleaning_plan_name=cp_n
            ))

    return AdminRoomGridPaginatedResponse(
        total_count=len(items),
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
    lname = l_doc.get("name", "Location") if l_doc else "NH Hotel Amsterdam Centrum"
    cid = l_doc.get("client_id", "") if l_doc else ""

    now = datetime.now(timezone.utc)
    room_id = f"room_{uuid.uuid4().hex[:10]}"

    doc = {
        "_id": room_id,
        "id": room_id,
        "location_id": room_in.location_id,
        "client_id": cid,
        "name": room_in.name,
        "room_type": room_in.room_type,
        "floor": room_in.floor,
        "est_cleaning_duration_minutes": room_in.est_cleaning_duration_minutes,
        "required_photos_count": room_in.required_photos_count,
        "tasks_count": room_in.tasks_count,
        "cleaning_plan_name": room_in.cleaning_plan_name or "Standard Clean",
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["rooms"].insert_one(doc)
    if l_doc:
        await db["locations"].update_one(
            {"$or": [{"_id": room_in.location_id}, {"id": room_in.location_id}]},
            {"$inc": {"rooms_count": 1}}
        )

    return AdminRoomGridItem(
        room_id=room_id,
        room_name=room_in.name,
        room_type=room_in.room_type,
        location_id=room_in.location_id,
        location_name=lname,
        floor_label=f"Verdieping {room_in.floor}",
        duration_minutes=room_in.est_cleaning_duration_minutes,
        required_photos_count=room_in.required_photos_count,
        tasks_count=room_in.tasks_count,
        cleaning_plan_name=room_in.cleaning_plan_name or "Standard Clean"
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
    rname = rdoc.get("name", "Kamer 202")
    rtype = rdoc.get("room_type") or rdoc.get("type") or "Deluxe"

    lid = rdoc.get("location_id", "")
    ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]}) if lid else None
    lname = ldoc.get("name", "NH Hotel Amsterdam Centrum") if ldoc else "NH Hotel Amsterdam Centrum"

    fl = rdoc.get("floor", 2)
    dur = rdoc.get("est_cleaning_duration_minutes", 60)
    req_p = len(rdoc.get("required_photos", [])) or rdoc.get("required_photos_count", 6)
    t_cnt = len(rdoc.get("tasks", [])) or rdoc.get("tasks_count", 15)
    cp_name = rdoc.get("cleaning_plan_name", "Deluxe Clean")

    code = f"R{rid[-3:].upper()}" if len(rid) >= 3 else "R002"

    return RoomDrawerDetailResponse(
        room_id=rid,
        room_code=code,
        room_name=rname,
        room_type=rtype,
        floor_label=f"Verdieping {fl}",
        location_name=lname,
        cleaning_plan_name=cp_name,
        duration_minutes=dur,
        required_photos_count=req_p,
        tasks_count=t_cnt
    )
