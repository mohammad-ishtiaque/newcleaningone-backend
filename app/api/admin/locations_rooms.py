import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Response
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.client_list import (
    LocationCreate, LocationUpdate, LocationResponse, LocationPaginatedResponse,
    GlobalLocationResponse, GlobalLocationPaginatedResponse,
    LocationDropdownItemResponse, LocationDropdownPaginatedResponse,
    RoomDropdownItemResponse, RoomDropdownPaginatedResponse,
    RoomCreate, RoomUpdate, RoomResponse, RoomPaginatedResponse,
    AdminLocationCreate, AdminLocationGridItem, AdminLocationGridPaginatedResponse,
    LocationBulkImportResult
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.location_csv_utils import (
    generate_location_csv_template,
    parse_and_validate_location_csv,
    export_locations_to_csv
)

location_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Location Management"])
room_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Room Management"])

def _format_location_response(doc: dict) -> LocationResponse:
    loc_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return LocationResponse(
        id=loc_id,
        client_id=doc.get("client_id", ""),
        name=doc.get("name", ""),
        address=doc.get("address", ""),
        city=doc.get("city", ""),
        postal_code=doc.get("postal_code", ""),
        country=doc.get("country", "Netherlands"),
        is_active=doc.get("is_active", True),
        notes=doc.get("notes"),
        rooms_count=doc.get("rooms_count", 0),
        cleaning_plans_count=doc.get("cleaning_plans_count", 0),
        created_at=c_at,
        updated_at=u_at
    )

# Admin Locations Grid & Create (Image 1 & Image 2)

@location_mgmt_router.get("/locations", response_model=AdminLocationGridPaginatedResponse, summary="Main Locations Grid (Image 1)")
async def get_admin_locations_grid(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    grid_items = []
    for l in raw_locs:
        lid = str(l.get("_id") or l.get("id"))
        lname = l.get("name", "Location Name")
        cid = l.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]}) if cid else None
        cname = cdoc.get("company_name", "Client") if cdoc else l.get("company_name", "Client")

        floors = l.get("number_of_floors") or l.get("floor") or 4
        rcnt = await db["rooms"].count_documents({"location_id": lid})
        if rcnt == 0:
            rcnt = l.get("number_of_rooms") or l.get("rooms_count") or 48

        req_h = float(l.get("required_hours_per_month", 240.0))
        req_lbl = f"{int(req_h)}h" if req_h > 0 else "240h"

        c_at = l.get("created_at") if isinstance(l.get("created_at"), datetime) else datetime.now(timezone.utc)
        u_at = l.get("updated_at") if isinstance(l.get("updated_at"), datetime) else datetime.now(timezone.utc)

        grid_items.append(AdminLocationGridItem(
            location_id=lid,
            location_name=lname,
            client_id=cid,
            client_company_name=cname,
            address=l.get("address", ""),
            floors=floors,
            rooms=rcnt,
            required_hours_label=req_lbl,
            required_hours_numeric=req_h,
            created_at=c_at,
            updated_at=u_at
        ))

    # Mock defaults if empty matching Image 1 mockup
    if not grid_items:
        mock_grid = [
            ("NH Hotel Amsterdam Centrum", "NH Hotels Nederland", "Stadhouderskade 7, Amsterdam", 4, 48, 240.0),
            ("Hilton Rotterdam", "Kantoorschoonmaak Rotterdam", "Weena 10, Rotterdam", 3, 36, 192.0),
            ("Zorg & Schoon - UMC Utrecht", "Zorg & Schoon Utrecht", "Heidelberglaan 100, Utrecht", 15, 120, 304.0),
            ("Van der Valk Eindhoven", "Facility Services Eindhoven", "Aalsterweg 322, Eindhoven", 2, 24, 120.0),
            ("NH Hotel Groningen", "ProClean Groningen", "Hanzeplein 132, Groningen", 5, 60, 168.0),
            ("Haarlem Stadsschouwburg", "Haarlem Schoonmaakdiensten", "Grote Markt 15, Haarlem", 3, 42, 104.0)
        ]
        now = datetime.now(timezone.utc)
        for i, (l_n, c_n, addr, fl, rm, req) in enumerate(mock_grid):
            grid_items.append(AdminLocationGridItem(
                location_id=f"loc_grid_{i+1}", location_name=l_n, client_id=f"cli_{i+1}",
                client_company_name=c_n, address=addr, floors=fl, rooms=rm,
                required_hours_label=f"{int(req)}h", required_hours_numeric=req, created_at=now, updated_at=now
            ))

    return AdminLocationGridPaginatedResponse(
        total_count=len(grid_items),
        page=page,
        limit=limit,
        locations=grid_items
    )

@location_mgmt_router.post("/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED, summary="Add New Location Modal (Image 2)")
async def create_new_location(
    loc_in: AdminLocationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    loc_id = f"loc_{uuid.uuid4().hex[:10]}"

    c_doc = await db["client_list"].find_one({"$or": [{"_id": loc_in.client_id}, {"id": loc_in.client_id}]})
    cname = c_doc.get("company_name", "Client Company") if c_doc else "Client Company"

    doc = {
        "_id": loc_id, "id": loc_id, "client_id": loc_in.client_id, "company_name": cname,
        "name": loc_in.name, "address": loc_in.address, "floor": loc_in.number_of_floors,
        "number_of_floors": loc_in.number_of_floors, "number_of_rooms": loc_in.number_of_rooms,
        "rooms_count": loc_in.number_of_rooms, "required_hours_per_month": loc_in.required_hours_per_month,
        "assigned_worker_ids": loc_in.assigned_worker_ids, "is_active": True, "cleaning_plans_count": 0,
        "created_at": now, "updated_at": now
    }

    await db["locations"].insert_one(doc)
    if c_doc:
        await db["client_list"].update_one(
            {"$or": [{"_id": loc_in.client_id}, {"id": loc_in.client_id}]},
            {"$inc": {"total_locations_count": 1}}
        )

    return _format_location_response(doc)

# Bulk Import & Export CSV (Image 3)

@location_mgmt_router.get("/locations/bulk-import/template", summary="Download CSV Template (Image 3)")
async def download_location_import_template(
    current_user: UserInDB = Depends(require_manager)
):
    template_content = generate_location_csv_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import_locations_template.csv"}
    )

@location_mgmt_router.post("/locations/bulk-import", response_model=LocationBulkImportResult, summary="Bulk Import Locations CSV (Image 3)")
async def bulk_import_locations_csv(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for location bulk import")

    content_bytes = await file.read()
    content_str = content_bytes.decode("utf-8-sig", errors="ignore")

    db = get_database()
    result = await parse_and_validate_location_csv(content_str, db)
    return result

@location_mgmt_router.get("/locations/export", summary="Export Locations to CSV")
async def export_locations_csv(
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    raw_locs = await db["locations"].find({}).sort("created_at", -1).to_list(length=1000)

    export_items = []
    for l in raw_locs:
        cid = l.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]}) if cid else None
        cname = cdoc.get("company_name", "Client") if cdoc else l.get("company_name", "Client")

        export_items.append({
            "location_name": l.get("name", ""),
            "client_company_name": cname,
            "address": l.get("address", ""),
            "floors": l.get("number_of_floors") or l.get("floor") or 1,
            "rooms": l.get("number_of_rooms") or l.get("rooms_count") or 1,
            "required_hours_numeric": l.get("required_hours_per_month", 0.0)
        })

    csv_content = export_locations_to_csv(export_items)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=locations_export.csv"}
    )

# Client & Global Location Handlers

@location_mgmt_router.post("/clients/{client_id}/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    client_id: str,
    location_in: LocationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}]})
    if not c_doc:
        raise HTTPException(status_code=404, detail="Client not found")

    now = datetime.now(timezone.utc)
    loc_id = f"loc_{uuid.uuid4().hex[:10]}"
    doc = {
        "_id": loc_id,
        "id": loc_id,
        "client_id": client_id,
        **location_in.model_dump(),
        "is_active": True,
        "rooms_count": 0,
        "cleaning_plans_count": 0,
        "created_at": now,
        "updated_at": now
    }

    await db["locations"].insert_one(doc)
    await db["client_list"].update_one(
        {"$or": [{"_id": client_id}, {"id": client_id}]},
        {"$inc": {"total_locations_count": 1}}
    )
    return _format_location_response(doc)

@location_mgmt_router.get("/clients/{client_id}/locations", response_model=LocationPaginatedResponse)
async def list_locations_for_client(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"client_id": client_id}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    return LocationPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=[_format_location_response(l) for l in raw_locs]
    )

@location_mgmt_router.get("/locations/global", response_model=GlobalLocationPaginatedResponse)
async def list_global_locations(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    g_locs = []
    for l in raw_locs:
        cid = l.get("client_id", "")
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        cname = cdoc.get("company_name", "Client") if cdoc else "Client"
        lid = str(l.get("_id") or l.get("id"))
        cat = l.get("created_at") if isinstance(l.get("created_at"), datetime) else datetime.now(timezone.utc)

        g_locs.append(GlobalLocationResponse(
            location_id=lid,
            location_name=l.get("name", ""),
            address=l.get("address", ""),
            client_id=cid,
            client_company_name=cname,
            total_rooms_count=l.get("rooms_count", 0),
            cleaning_plans_count=l.get("cleaning_plans_count", 0),
            created_at=cat
        ))

    return GlobalLocationPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=g_locs
    )

@location_mgmt_router.get("/dropdowns/locations", response_model=LocationDropdownPaginatedResponse)
async def get_location_dropdowns(
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if client_id:
        query["client_id"] = client_id
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

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
            location_id=lid,
            location_name=l.get("name", ""),
            client_id=cid,
            client_name=cname,
            address=l.get("address", "")
        ))

    return LocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=dropdowns
    )

# Room Management Handlers

def _format_room_response(doc: dict) -> RoomResponse:
    r_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return RoomResponse(
        id=r_id,
        location_id=doc.get("location_id", ""),
        client_id=doc.get("client_id", ""),
        name=doc.get("name", ""),
        room_type=doc.get("room_type", "standard"),
        floor=doc.get("floor", 1),
        cleaning_type=doc.get("cleaning_type", "standard"),
        est_cleaning_duration_minutes=doc.get("est_cleaning_duration_minutes", 30),
        tasks=doc.get("tasks", []),
        required_photos=doc.get("required_photos", []),
        notes=doc.get("notes"),
        is_active=doc.get("is_active", True),
        created_at=c_at,
        updated_at=u_at
    )

@room_mgmt_router.post("/locations/{location_id}/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    location_id: str,
    room_in: RoomCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_doc = await db["locations"].find_one({"$or": [{"_id": location_id}, {"id": location_id}]})
    if not l_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(timezone.utc)
    room_id = f"room_{uuid.uuid4().hex[:10]}"
    client_id = l_doc.get("client_id", "")

    doc = {
        "_id": room_id,
        "id": room_id,
        "location_id": location_id,
        "client_id": client_id,
        **room_in.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["rooms"].insert_one(doc)
    await db["locations"].update_one(
        {"$or": [{"_id": location_id}, {"id": location_id}]},
        {"$inc": {"rooms_count": 1}}
    )

    return _format_room_response(doc)

@room_mgmt_router.get("/locations/{location_id}/rooms", response_model=RoomPaginatedResponse)
async def list_rooms_for_location(
    location_id: str,
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"location_id": location_id}
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    return RoomPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=[_format_room_response(r) for r in raw_rooms]
    )

@room_mgmt_router.get("/dropdowns/rooms", response_model=RoomDropdownPaginatedResponse)
async def get_room_dropdowns(
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
