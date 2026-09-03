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
    LocationBulkImportResult,
    ClientGridDropdownPaginatedResponse, ClientGridDropdownItem,
    RequiredPhotoResponse, CleaningTaskResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.location_csv_utils import (
    generate_location_csv_template,
    parse_and_validate_location_csv,
    export_locations_to_csv
)
from app.api.admin.rooms import room_mgmt_router, _format_room_response
from app.services.client_helper import resolve_client_id_aliases, get_or_sync_client_doc

location_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Location Management"])

def _format_location_response(doc: dict, rooms_count: Optional[int] = None) -> LocationResponse:
    loc_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)
    rcnt = rooms_count if rooms_count is not None else (doc.get("rooms_count", 0) or doc.get("number_of_rooms", 0))

    return LocationResponse(
        id=loc_id,
        client_id=doc.get("client_id", ""),
        name=doc.get("name", ""),
        type=doc.get("type", "office"),
        address=doc.get("address", ""),
        city=doc.get("city", ""),
        postal_code=doc.get("postal_code", ""),
        country=doc.get("country", "Netherlands"),
        floor=doc.get("floor") or doc.get("number_of_floors") or 1,
        number_of_rooms=rcnt,
        rooms_count=rcnt,
        description=doc.get("description", ""),
        image_url=doc.get("image_url"),
        is_active=doc.get("is_active", True),
        notes=doc.get("notes"),
        cleaning_plans_count=doc.get("cleaning_plans_count", 0),
        created_at=c_at,
        updated_at=u_at
    )

# Admin Locations Grid & Create (Image 1 & Image 2)

@location_mgmt_router.get("/dropdowns/clients", response_model=ClientGridDropdownPaginatedResponse, summary="List All Clients")
async def list_clients_for_locations(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    is_signup: Optional[bool] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    conditions = [{"status": {"$ne": "deleted"}}]
    
    if is_signup is not None:
        if is_signup:
            conditions.append({"is_signup": True})
        else:
            conditions.append({"$or": [{"is_signup": False}, {"is_signup": {"$exists": False}}]})

    if search:
        conditions.append({
            "$or": [
                {"company_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}},
                {"industry": {"$regex": search, "$options": "i"}}
            ]
        })

    query = {"$and": conditions} if len(conditions) > 1 else conditions[0]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    clients = []
    for c in raw_clients:
        client_is_signup = c.get("is_signup")
        if client_is_signup is None:
            user_doc = await db["users"].find_one({"email": c.get("email"), "role": "client"})
            client_is_signup = bool(user_doc and user_doc.get("is_approved", True))

        clients.append(ClientGridDropdownItem(
            id=str(c.get("_id") or c.get("id")),
            primary_contact_name=c.get("primary_contact_name", ""),
            company_name=c.get("company_name", ""),
            is_signup=bool(client_is_signup)
        ))

    return ClientGridDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients
    )



# Client & Global Location Handlers

@location_mgmt_router.post("/clients/{client_id}/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED, summary="Create Client Location")
async def create_location(
    client_id: str,
    location_in: LocationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_doc = await get_or_sync_client_doc(client_id, db)
    if not c_doc:
        raise HTTPException(status_code=404, detail="Client not found")

    resolved_client_id = str(c_doc.get("_id") or c_doc.get("id") or client_id)
    c_aliases = await resolve_client_id_aliases(client_id, db)
    if resolved_client_id not in c_aliases:
        c_aliases.append(resolved_client_id)

    now = datetime.now(timezone.utc)
    loc_id = f"loc_{uuid.uuid4().hex[:10]}"
    doc = {
        "_id": loc_id,
        "id": loc_id,
        "client_id": resolved_client_id,
        "client_ids": c_aliases,
        "company_name": c_doc.get("company_name", "Client"),
        **location_in.model_dump(),
        "is_active": True,
        "rooms_count": 0,
        "number_of_rooms": 0,
        "cleaning_plans_count": 0,
        "created_at": now,
        "updated_at": now
    }

    await db["locations"].insert_one(doc)
    await db["client_list"].update_one(
        {"$or": [{"_id": resolved_client_id}, {"id": resolved_client_id}, {"user_id": client_id}]},
        {"$inc": {"total_locations_count": 1}}
    )
    return _format_location_response(doc, rooms_count=0)

@location_mgmt_router.get("/locations", response_model=AdminLocationGridPaginatedResponse, summary="Global Locations Grid Page")
async def get_global_locations_grid(
    location_id: Optional[str] = None,
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query_parts = []
    if location_id:
        query_parts.append({"$or": [{"_id": location_id}, {"id": location_id}]})
    if client_id:
        c_aliases = await resolve_client_id_aliases(client_id, db)
        query_parts.append({"$or": [{"client_id": {"$in": c_aliases}}, {"client_ids": {"$in": c_aliases}}]})
    if search:
        query_parts.append({"$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
            {"company_name": {"$regex": search, "$options": "i"}}
        ]})

    query = {"$and": query_parts} if len(query_parts) > 1 else (query_parts[0] if query_parts else {})

    total_count = await db["locations"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    loc_ids = [str(l.get("_id") or l.get("id")) for l in raw_locs]
    client_ids = [str(l.get("client_id")) for l in raw_locs if l.get("client_id")]

    # Batch resolve clients
    client_map = {}
    if client_ids:
        cli_or = [{"_id": {"$in": client_ids}}, {"id": {"$in": client_ids}}]
        cli_oids = [ObjectId(x) for x in client_ids if ObjectId.is_valid(x)]
        if cli_oids:
            cli_or.append({"_id": {"$in": cli_oids}})
        async for c in db["client_list"].find({"$or": cli_or}):
            cname = c.get("company_name", "Client")
            for k in (c.get("_id"), c.get("id")):
                if k:
                    client_map[str(k)] = cname

    # Batch count rooms per location using single aggregation
    room_count_map = {}
    if loc_ids:
        pipeline = [
            {"$match": {"location_id": {"$in": loc_ids}}},
            {"$group": {"_id": "$location_id", "count": {"$sum": 1}}}
        ]
        async for doc in db["rooms"].aggregate(pipeline):
            room_count_map[str(doc["_id"])] = doc["count"]

    grid_items = []
    for l in raw_locs:
        lid = str(l.get("_id") or l.get("id"))
        lname = l.get("name", "Location Name")
        cid_val = l.get("client_id", "")
        cname = client_map.get(cid_val) or l.get("company_name", "Client")

        floors = l.get("number_of_floors") or l.get("floor") or 1
        rcnt = room_count_map.get(lid, 0)

        req_h = float(l.get("required_hours_per_month", 0.0))
        req_lbl = f"{int(req_h)}h" if req_h > 0 else "0h"

        c_at = l.get("created_at") if isinstance(l.get("created_at"), datetime) else datetime.now(timezone.utc)
        u_at = l.get("updated_at") if isinstance(l.get("updated_at"), datetime) else datetime.now(timezone.utc)

        grid_items.append(AdminLocationGridItem(
            id=lid,
            name=lname,
            location_id=lid,
            location_name=lname,
            client_id=cid_val,
            company_name=cname,
            client_company_name=cname,
            address=l.get("address", ""),
            floors=floors,
            rooms=rcnt,
            required_hours_label=req_lbl,
            required_hours_numeric=req_h,
            created_at=c_at,
            updated_at=u_at
        ))

    return AdminLocationGridPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=grid_items
    )

@location_mgmt_router.get("/locations/export", summary="Export Locations to CSV")
async def export_locations_csv(
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    raw_locs = await db["locations"].find({}).sort("created_at", -1).to_list(length=1000)

    # Pre-fetch all clients into lookup map
    client_map = {}
    async for c in db["client_list"].find({}):
        cname = c.get("company_name", "Client")
        for k in (c.get("_id"), c.get("id")):
            if k:
                client_map[str(k)] = cname

    export_items = []
    for l in raw_locs:
        cid = str(l.get("client_id") or "")
        cname = client_map.get(cid) or l.get("company_name", "Client")

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

@location_mgmt_router.post("/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False, summary="Add New Location Modal (Image 2)")
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

@location_mgmt_router.get("/locations/{location_id}", response_model=LocationResponse, summary="Get Full Location Details")
async def get_location_details(
    location_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    loc_doc = await db["locations"].find_one(query)
    if not loc_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    lid = str(loc_doc.get("_id") or loc_doc.get("id"))
    rcnt = await db["rooms"].count_documents({"location_id": lid})
    return _format_location_response(loc_doc, rooms_count=rcnt)

@location_mgmt_router.patch("/locations/{location_id}", response_model=LocationResponse, summary="Update Location")
async def update_location(
    location_id: str,
    location_in: LocationUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    loc_doc = await db["locations"].find_one(query)
    if not loc_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    fields = location_in.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)

    await db["locations"].update_one(query, {"$set": fields})
    updated = await db["locations"].find_one(query)
    lid = str(updated.get("_id") or updated.get("id"))
    rcnt = await db["rooms"].count_documents({"location_id": lid})
    return _format_location_response(updated, rooms_count=rcnt)

@location_mgmt_router.delete("/locations/{location_id}", status_code=status.HTTP_200_OK, summary="Delete Location")
async def delete_location(
    location_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    loc_doc = await db["locations"].find_one(query)
    if not loc_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    lid = str(loc_doc.get("_id") or loc_doc.get("id"))
    cid = loc_doc.get("client_id")

    await db["locations"].delete_one({"_id": loc_doc["_id"]})
    await db["rooms"].delete_many({"location_id": lid})

    if cid:
        await db["client_list"].update_one(
            {"$or": [{"_id": cid}, {"id": cid}]},
            {"$inc": {"total_locations_count": -1}}
        )

    return {"message": "Location deleted successfully"}

@location_mgmt_router.get("/dropdowns/locations", response_model=LocationDropdownPaginatedResponse)
async def get_location_dropdowns(
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
        
        cname = cdoc.get("primary_contact_name", "Unknown") if cdoc else "Unknown"
        comp_name = cdoc.get("company_name", "") if cdoc else ""
        lid = str(l.get("_id") or l.get("id"))

        dropdowns.append(LocationDropdownItemResponse(
            id=lid,
            name=l.get("name", ""),
            client_id=cid,
            company_name=comp_name or cname,
            address=l.get("address", "")
        ))

    return LocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=dropdowns
    )
