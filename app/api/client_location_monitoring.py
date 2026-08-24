import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.client_location_monitoring import (
    ClientLocationShortItem, ClientRoomShortSummaryItem, ClientLocationMonitoringPaginatedResponse,
    ClientLocationDetailResponse, ClientRoomDetailItem,
    ClientLocationRoomsPaginatedResponse, ClientRoomFullDetailResponse
)
from app.schemas.client_list import CleaningTaskResponse, TaskPhotoResponse

router = APIRouter(prefix="/client", tags=["Client Location Monitoring"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client and current_user.role != "client":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


def _format_room_tasks_and_photos(raw_tasks: list):
    formatted_tasks = []
    total_photos = 0
    if isinstance(raw_tasks, list):
        for t in raw_tasks:
            if isinstance(t, dict):
                tid = str(t.get("id") or t.get("_id") or f"t_{uuid.uuid4().hex[:6]}")
                tname = t.get("name", "Task")
                tfreq = t.get("frequency_type", "every_visit")
                is_req = bool(t.get("is_photo_req", False))

                t_photos_raw = t.get("photo") or t.get("photos") or []
                t_photos = []
                if isinstance(t_photos_raw, list):
                    for p in t_photos_raw:
                        if isinstance(p, dict):
                            pid = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                            t_photos.append(TaskPhotoResponse(id=pid, name=p.get("name", "Photo")))
                        elif isinstance(p, str):
                            t_photos.append(TaskPhotoResponse(id=uuid.uuid4().hex[:8], name=p))

                if t_photos and not is_req:
                    is_req = True

                total_photos += len(t_photos)

                formatted_tasks.append(CleaningTaskResponse(
                    id=tid,
                    name=tname,
                    frequency_type=tfreq,
                    is_photo_req=is_req,
                    photo=t_photos,
                    total_photos_required=len(t_photos)
                ))
    return formatted_tasks, total_photos


@router.get(
    "/locations",
    response_model=ClientLocationMonitoringPaginatedResponse,
    summary="Client List Locations (Monitoring)",
    description="""
### Client List Locations with Rooms Summary
Retrieves a paginated list of locations belonging to the authenticated client in summary format, including an embedded summary list of rooms.

#### Query Parameters:
- **`search`** (`str`, *Optional*): Search locations by name, address, city, or postal code.
- **`page`** (`int`, *Optional*, default: `1`): Page number.
- **`limit`** (`int`, *Optional*, default: `10`): Items per page.
"""
)
async def list_client_locations_monitoring(
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client List Locations (Summary Form).
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")

    # Base query for locations of this client
    query_parts = [{
        "$or": [
            {"client_id": client_id},
            {"client_ids": client_id},
            {"id": client_id}
        ]
    }]

    if search:
        search_filter = [
            {"name": {"$regex": search, "$options": "i"}},
            {"location_name": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}},
            {"postal_code": {"$regex": search, "$options": "i"}}
        ]
        query_parts.append({"$or": search_filter})

    if len(query_parts) == 1:
        final_query = query_parts[0]
    else:
        final_query = {"$and": query_parts}

    total_count = await db["locations"].count_documents(final_query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(final_query).sort("name", 1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    # Fallback to embedded locations in client_list if locations collection is empty
    if not raw_locs and total_count == 0:
        c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}]})
        if c_doc and "locations" in c_doc and isinstance(c_doc["locations"], list):
            embedded_locs = c_doc["locations"]
            if search:
                s_lower = search.lower()
                embedded_locs = [
                    l for l in embedded_locs
                    if s_lower in str(l.get("name", "")).lower() or s_lower in str(l.get("address", "")).lower()
                ]
            total_count = len(embedded_locs)
            raw_locs = embedded_locs[skip:skip + limit]

    loc_ids = [str(l.get("_id") or l.get("id")) for l in raw_locs if l.get("_id") or l.get("id")]

    # Fetch rooms for all these locations in batch
    rooms_by_loc = {}
    if loc_ids:
        r_cursor = db["rooms"].find({"location_id": {"$in": loc_ids}}).sort("room_name", 1)
        all_rooms = await r_cursor.to_list(length=2000)
        for r in all_rooms:
            lid = str(r.get("location_id"))
            if lid not in rooms_by_loc:
                rooms_by_loc[lid] = []
            rooms_by_loc[lid].append(r)

    locations_list = []
    for l in raw_locs:
        lid = str(l.get("_id") or l.get("id") or "")
        lname = l.get("name") or l.get("location_name") or "Main Location"
        addr = l.get("address") or l.get("street") or ""
        city = l.get("city") or ""
        p_code = l.get("postal_code") or l.get("zip") or ""
        loc_type = l.get("type", "office")
        c_at = l.get("created_at") if isinstance(l.get("created_at"), datetime) else datetime.now(timezone.utc)
        u_at = l.get("updated_at") if isinstance(l.get("updated_at"), datetime) else datetime.now(timezone.utc)

        raw_rooms_for_loc = rooms_by_loc.get(lid, [])
        rooms_summary = []
        for r in raw_rooms_for_loc:
            rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
            rname = r.get("room_name") or r.get("name") or r.get("custom_room_name") or "Room"
            rtype = r.get("room_type") or r.get("type") or "standard"
            r_tasks = r.get("tasks", []) or []
            _, t_photos_count = _format_room_tasks_and_photos(r_tasks)
            if t_photos_count == 0:
                t_photos_count = len(r.get("required_photos", []) or [])

            rooms_summary.append(ClientRoomShortSummaryItem(
                id=rid,
                room_id=rid,
                room_name=rname,
                room_type=rtype,
                floor=r.get("floor", 1),
                duration=r.get("duration", 30),
                cleaning_type=r.get("clean_type") or r.get("cleaning_type", "standard"),
                monthly_cleaning_frequency=r.get("monthly_cleaning_frequency", 4),
                tasks_count=len(r_tasks),
                photos_count=t_photos_count
            ))

        total_rooms = len(rooms_summary) or l.get("total_rooms_count") or l.get("rooms_count", 0)
        plans_cnt = l.get("cleaning_plans_count", 0)

        locations_list.append(ClientLocationShortItem(
            id=lid,
            location_id=lid,
            name=lname,
            location_name=lname,
            type=loc_type,
            address=addr or None,
            city=city or None,
            postal_code=p_code or None,
            country=l.get("country", "Netherlands"),
            floor=l.get("floor") or l.get("number_of_floors") or 1,
            total_rooms_count=total_rooms,
            cleaning_plans_count=plans_cnt,
            image_url=l.get("image_url"),
            is_active=l.get("is_active", True),
            rooms_summary=rooms_summary,
            created_at=c_at,
            updated_at=u_at
        ))

    end_idx = skip + limit
    return ClientLocationMonitoringPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        locations=locations_list
    )


@router.get(
    "/locations/{location_id}",
    response_model=ClientLocationDetailResponse,
    summary="Client Get Full Location Details",
    description="""
### Client Get Full Location Details
Retrieves complete location details, metadata, facility description, and complete rooms list for the specified location ID.
"""
)
async def get_client_location_detail(
    location_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Single Location Detail Endpoint.
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")
    company_name = getattr(current_user, "company_name", None) or getattr(current_user, "full_name", None) or "Client"

    # Validate that location exists and belongs to this client
    query_loc = {
        "$and": [
            {"$or": [{"_id": location_id}, {"id": location_id}]},
            {"$or": [{"client_id": client_id}, {"client_ids": client_id}, {"id": client_id}]}
        ]
    }
    loc_doc = await db["locations"].find_one(query_loc)

    # Fallback to embedded locations in client_list
    if not loc_doc:
        c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}]})
        if c_doc and "locations" in c_doc and isinstance(c_doc["locations"], list):
            target_l = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(location_id)), None)
            if target_l:
                loc_doc = target_l

    if not loc_doc:
        raise HTTPException(status_code=404, detail="Location not found or does not belong to client")

    lid = str(loc_doc.get("_id") or loc_doc.get("id") or location_id)
    lname = loc_doc.get("name") or loc_doc.get("location_name") or "Main Location"
    c_name = loc_doc.get("company_name") or company_name

    # Fetch all rooms for this location
    r_cursor = db["rooms"].find({"location_id": lid}).sort("room_name", 1)
    raw_rooms = await r_cursor.to_list(length=1000)

    rooms_detail_list = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
        rname = r.get("room_name") or r.get("name") or r.get("custom_room_name") or "Room"
        rtype = r.get("room_type") or r.get("type") or "standard"
        raw_tasks = r.get("tasks", []) or []
        formatted_tasks, total_photos = _format_room_tasks_and_photos(raw_tasks)
        if total_photos == 0:
            total_photos = len(r.get("required_photos", []) or [])

        r_c_at = r.get("created_at") if isinstance(r.get("created_at"), datetime) else datetime.now(timezone.utc)
        r_u_at = r.get("updated_at") if isinstance(r.get("updated_at"), datetime) else datetime.now(timezone.utc)

        rooms_detail_list.append(ClientRoomDetailItem(
            id=rid,
            room_id=rid,
            room_name=rname,
            room_type=rtype,
            location_id=lid,
            location_name=lname,
            floor=r.get("floor", 1),
            duration=r.get("duration", 30),
            cleaning_type=r.get("clean_type") or r.get("cleaning_type", "standard"),
            monthly_cleaning_frequency=r.get("monthly_cleaning_frequency", 4),
            tasks_count=len(formatted_tasks),
            photos_count=total_photos,
            tasks=formatted_tasks,
            created_at=r_c_at,
            updated_at=r_u_at
        ))

    c_at = loc_doc.get("created_at") if isinstance(loc_doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = loc_doc.get("updated_at") if isinstance(loc_doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ClientLocationDetailResponse(
        id=lid,
        location_id=lid,
        name=lname,
        location_name=lname,
        type=loc_doc.get("type", "office"),
        address=loc_doc.get("address") or loc_doc.get("street"),
        city=loc_doc.get("city"),
        postal_code=loc_doc.get("postal_code") or loc_doc.get("zip"),
        country=loc_doc.get("country", "Netherlands"),
        floor=loc_doc.get("floor") or loc_doc.get("number_of_floors") or 1,
        description=loc_doc.get("description"),
        notes=loc_doc.get("notes"),
        image_url=loc_doc.get("image_url"),
        is_active=loc_doc.get("is_active", True),
        total_rooms_count=len(rooms_detail_list),
        cleaning_plans_count=loc_doc.get("cleaning_plans_count", 0),
        client_id=client_id,
        company_name=c_name,
        rooms=rooms_detail_list,
        created_at=c_at,
        updated_at=u_at
    )


@router.get(
    "/locations/{location_id}/rooms",
    response_model=ClientLocationRoomsPaginatedResponse,
    summary="Client List Rooms for Location",
    description="""
### Client List Rooms for Location (Paginated)
Retrieves a paginated list of rooms belonging to a specific location of the client, supporting search and room type filters.
"""
)
async def list_client_location_rooms(
    location_id: str,
    search: Optional[str] = None,
    room_type: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client List Rooms for Location Endpoint.
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")

    # Verify location belongs to client
    query_loc = {
        "$and": [
            {"$or": [{"_id": location_id}, {"id": location_id}]},
            {"$or": [{"client_id": client_id}, {"client_ids": client_id}, {"id": client_id}]}
        ]
    }
    loc_doc = await db["locations"].find_one(query_loc)
    location_name = loc_doc.get("name") if loc_doc else "Location"

    query_parts = [{"location_id": location_id}]
    if room_type and room_type.lower() != "all":
        query_parts.append({"$or": [{"room_type": room_type}, {"type": room_type}]})

    if search:
        search_filter = [
            {"room_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"custom_room_name": {"$regex": search, "$options": "i"}},
            {"room_type": {"$regex": search, "$options": "i"}},
            {"clean_type": {"$regex": search, "$options": "i"}}
        ]
        query_parts.append({"$or": search_filter})

    final_query = {"$and": query_parts} if len(query_parts) > 1 else query_parts[0]

    total_count = await db["rooms"].count_documents(final_query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(final_query).sort("room_name", 1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    rooms_list = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
        rname = r.get("room_name") or r.get("name") or r.get("custom_room_name") or "Room"
        rtype = r.get("room_type") or r.get("type") or "standard"
        raw_tasks = r.get("tasks", []) or []
        formatted_tasks, total_photos = _format_room_tasks_and_photos(raw_tasks)
        if total_photos == 0:
            total_photos = len(r.get("required_photos", []) or [])

        r_c_at = r.get("created_at") if isinstance(r.get("created_at"), datetime) else datetime.now(timezone.utc)
        r_u_at = r.get("updated_at") if isinstance(r.get("updated_at"), datetime) else datetime.now(timezone.utc)

        rooms_list.append(ClientRoomDetailItem(
            id=rid,
            room_id=rid,
            room_name=rname,
            room_type=rtype,
            location_id=location_id,
            location_name=location_name,
            floor=r.get("floor", 1),
            duration=r.get("duration", 30),
            cleaning_type=r.get("clean_type") or r.get("cleaning_type", "standard"),
            monthly_cleaning_frequency=r.get("monthly_cleaning_frequency", 4),
            tasks_count=len(formatted_tasks),
            photos_count=total_photos,
            tasks=formatted_tasks,
            created_at=r_c_at,
            updated_at=r_u_at
        ))

    end_idx = skip + limit
    return ClientLocationRoomsPaginatedResponse(
        location_id=location_id,
        location_name=location_name,
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        rooms=rooms_list
    )


@router.get(
    "/locations/{location_id}/rooms/{room_id}",
    response_model=ClientRoomFullDetailResponse,
    summary="Client Get Full Room Details (via Location)",
    description="""
### Client Get Full Room Details
Retrieves complete details for a single room within a specific location, including full task checklists and task-connected photos.
"""
)
@router.get(
    "/rooms/{room_id}",
    response_model=ClientRoomFullDetailResponse,
    summary="Client Get Full Room Details (Direct)",
    include_in_schema=False
)
async def get_client_room_full_detail(
    room_id: str,
    location_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Full Room Detail Endpoint.
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")
    company_name = getattr(current_user, "company_name", None) or getattr(current_user, "full_name", None) or "Client"

    query_room = {"$or": [{"_id": room_id}, {"id": room_id}, {"room_id": room_id}]}
    room_doc = await db["rooms"].find_one(query_room)
    if not room_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    r_loc_id = str(room_doc.get("location_id") or "")
    if location_id and r_loc_id and r_loc_id != location_id:
        raise HTTPException(status_code=404, detail="Room does not belong to the specified location")

    # Verify that the room or its location belongs to this client
    r_client_id = str(room_doc.get("client_id") or "")
    if r_client_id and r_client_id != client_id:
        raise HTTPException(status_code=403, detail="Access denied to this room")

    # If client_id is not directly on room doc, verify via location
    if not r_client_id and r_loc_id:
        loc_doc = await db["locations"].find_one({"$or": [{"_id": r_loc_id}, {"id": r_loc_id}]})
        if loc_doc:
            l_client_id = str(loc_doc.get("client_id") or "")
            if l_client_id and l_client_id != client_id:
                raise HTTPException(status_code=403, detail="Access denied to this room")

    rid = str(room_doc.get("_id") or room_doc.get("id") or room_doc.get("room_id") or room_id)
    rname = room_doc.get("room_name") or room_doc.get("name") or room_doc.get("custom_room_name") or "Room"
    rtype = room_doc.get("room_type") or room_doc.get("type") or "standard"
    r_loc_name = str(room_doc.get("location_name") or "")

    raw_tasks = room_doc.get("tasks", []) or []
    formatted_tasks, total_photos = _format_room_tasks_and_photos(raw_tasks)
    if total_photos == 0:
        total_photos = len(room_doc.get("required_photos", []) or [])

    r_c_at = room_doc.get("created_at") if isinstance(room_doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    r_u_at = room_doc.get("updated_at") if isinstance(room_doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ClientRoomFullDetailResponse(
        id=rid,
        room_id=rid,
        room_name=rname,
        room_type=rtype,
        location_id=r_loc_id or None,
        location_name=r_loc_name or None,
        client_id=client_id,
        company_name=company_name,
        floor=room_doc.get("floor", 1),
        duration=room_doc.get("duration", 30),
        cleaning_type=room_doc.get("clean_type") or room_doc.get("cleaning_type", "standard"),
        monthly_cleaning_frequency=room_doc.get("monthly_cleaning_frequency", 4),
        photo_number=total_photos,
        task_number=len(formatted_tasks),
        tasks=formatted_tasks,
        created_at=r_c_at,
        updated_at=r_u_at
    )
