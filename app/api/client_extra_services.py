import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional, List
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.extra_services_helper import format_extra_service_response, format_extra_service_list_item
from app.schemas.extra_services import (
    ExtraServiceCreate, ExtraServiceUpdate, ExtraServiceResponse, ExtraServicePaginatedResponse,
    ClientRoomDropdownItem, ClientRoomDropdownPaginatedResponse,
    ClientLocationDropdownItem, ClientLocationDropdownPaginatedResponse
)
from app.schemas.client_list import CleaningTaskResponse, TaskPhotoResponse
from app.services.client_helper import resolve_client_id_aliases

router = APIRouter(prefix="/client/extra-services", tags=["Client Extra Service Management"])


import re
from typing import Optional, List, Union

def _parse_duration_to_minutes(dur: Optional[Union[str, int, float]]) -> Optional[int]:
    if dur is None:
        return None
    if isinstance(dur, (int, float)):
        return int(dur)
    dur_str = str(dur).strip().lower()
    if not dur_str:
        return None
    if dur_str.isdigit():
        return int(dur_str)
    if ":" in dur_str:
        parts = dur_str.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
    total_m = 0
    h_match = re.search(r"(\d+)\s*(?:h|hr|hour)", dur_str)
    if h_match:
        total_m += int(h_match.group(1)) * 60
    m_match = re.search(r"(\d+)\s*(?:m|min)", dur_str)
    if m_match:
        total_m += int(m_match.group(1))
    return total_m if total_m > 0 else None

def _calculate_end_time_str(start_time_str: str, duration_mins: int) -> str:
    from app.core.timezone_utils import parse_time_to_minutes
    start_mins = parse_time_to_minutes(start_time_str)
    end_mins = (start_mins + duration_mins) % 1440
    end_h = end_mins // 60
    end_m = end_mins % 60
    meridiem = "AM" if end_h < 12 else "PM"
    display_h = end_h if 1 <= end_h <= 12 else (12 if end_h == 0 or end_h == 12 else end_h - 12)
    return f"{display_h:02d}:{end_m:02d} {meridiem}"

def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client and current_user.role != "client":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


@router.post(
    "",
    response_model=ExtraServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Client Create Extra Service Request",
    description="""
### Client Create Extra Service Request
Submits a new request for additional cleaning beyond regular schedule with hierarchical task and photo requirements, start time, and estimated duration.

#### Supported Field Values & Options:
- **`start_time`**: e.g. `"08:00 AM"` or `"02:30 PM"`
- **`duration`**: e.g. `"1h 30m"`, `"2 hours"`, `"45 mins"`
- **`duration_minutes`**: Duration in minutes (e.g. `90`)
- **`end_time`**: Optional end time e.g. `"09:30 AM"` (automatically calculated if `start_time` and `duration` provided)
- **`priority`**: `"High Priority"`, `"Medium Priority"`, `"Low Priority"` (also accepts `"high"`, `"medium"`, `"low"`)
- **`preferred_date`**: Target service date (`YYYY-MM-DD`, e.g. `"2026-07-10"`)
- **`tasks[].frequency_type`**: `"every_visit"`, `"weekly"`, `"monthly"`, `"yearly"`
- **`tasks[].is_photo_req`**: `true` | `false` (automatically enabled if `photo` list is provided)
- **`tasks[].photo`**: List of required photo items connected to this task
- **`location_id`**: Associated facility / office location ID
- **`room_id`**: Optional specific room ID
"""
)
async def create_client_extra_service(
    service_in: ExtraServiceCreate,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Create Extra Service Request Endpoint.
    Submits a new request with status 'under_review' for Manager/Admin review.
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")
    now = datetime.now(timezone.utc)
    service_id = f"es_{uuid.uuid4().hex[:10]}"

    client_name = getattr(current_user, "company_name", None) or getattr(current_user, "full_name", None) or "Client"
    c_doc = await db["client_list"].find_one({"$or": [{"_id": client_id}, {"id": client_id}]})
    if c_doc:
        client_name = c_doc.get("company_name", client_name)

    location_name = None
    if service_in.location_id:
        l_doc = await db["locations"].find_one({"$or": [{"_id": service_in.location_id}, {"id": service_in.location_id}]})
        if l_doc:
            location_name = l_doc.get("name")
        elif c_doc and "locations" in c_doc:
            target_loc = next((l for l in c_doc["locations"] if str(l.get("id") or l.get("_id")) == str(service_in.location_id)), None)
            if target_loc:
                location_name = target_loc.get("name")

    # Process hierarchical tasks and task photos
    task_items = []
    for t in (service_in.tasks or []):
        t_dict = t.model_dump() if hasattr(t, "model_dump") else dict(t)
        if not t_dict.get("id"):
            t_dict["id"] = f"t_{uuid.uuid4().hex[:6]}"

        raw_photos = t_dict.get("photo") or []
        processed_photos = []
        for p in raw_photos:
            p_dict = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else {"name": str(p)})
            if not p_dict.get("id"):
                p_dict["id"] = f"p_{uuid.uuid4().hex[:6]}"
            processed_photos.append(p_dict)
        t_dict["photo"] = processed_photos
        if processed_photos and not t_dict.get("is_photo_req"):
            t_dict["is_photo_req"] = True
        t_dict["is_completed"] = False
        t_dict["completed_at"] = None
        task_items.append(t_dict)

    # Normalize Priority
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

    # Duration & Time handling
    start_time_val = service_in.start_time
    dur_mins = service_in.duration_minutes
    if dur_mins is None and service_in.duration:
        dur_mins = _parse_duration_to_minutes(service_in.duration)

    dur_str = service_in.duration
    if not dur_str and dur_mins:
        hours = dur_mins // 60
        mins = dur_mins % 60
        dur_str = f"{hours}h {mins}m" if mins else f"{hours}h"

    end_time_val = service_in.end_time
    if start_time_val and dur_mins and not end_time_val:
        end_time_val = _calculate_end_time_str(start_time_val, dur_mins)

    est_hours = float(dur_mins / 60.0) if dur_mins else 0.0

    doc = {
        "_id": service_id,
        "id": service_id,
        "title": service_in.title,
        "preferred_date": service_in.preferred_date,
        "start_time": start_time_val,
        "duration_minutes": dur_mins,
        "duration": dur_str,
        "end_time": end_time_val,
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
        "estimated_hours": est_hours,
        "actual_start_time": None,
        "actual_finish_time": None,
        "hours_credited": None,
        "created_at": now,
        "updated_at": now
    }

    await db["extra_services"].insert_one(doc)
    return format_extra_service_response(doc)


@router.get(
    "/rooms-dropdown",
    response_model=ClientRoomDropdownPaginatedResponse,
    summary="Client Get Room Dropdown List",
    description="""
### Client Room Dropdowns (Paginated)
Retrieves a paginated list of rooms belonging to the authenticated client for Extra Service request creation.

#### Query Parameters:
- **`location_id`** (`str`, *Optional*): Filter rooms by specific location ID.
- **`search`** (`str`, *Optional*): Search rooms by name, type, or location name.
- **`page`** (`int`, *Optional*, default: `1`): Page number.
- **`limit`** (`int`, *Optional*, default: `10`): Items per page.
"""
)
@router.get(
    "/dropdowns/rooms",
    response_model=ClientRoomDropdownPaginatedResponse,
    summary="Client Get Room Dropdown List (Alias)",
    include_in_schema=False
)
async def get_client_room_dropdowns(
    location_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Room Dropdowns Endpoint.
    """
    db = get_database()
    client_aliases = await resolve_client_id_aliases(current_user, db)

    # Discover location IDs and map location names belonging to this client
    loc_ids = []
    loc_name_map = {}
    loc_cursor = db["locations"].find({
        "$or": [
            {"client_id": {"$in": client_aliases}},
            {"client_ids": {"$in": client_aliases}},
            {"_id": {"$in": client_aliases}},
            {"id": {"$in": client_aliases}}
        ]
    })
    loc_docs = await loc_cursor.to_list(length=1000)
    for l in loc_docs:
        lid = str(l.get("_id") or l.get("id"))
        if lid:
            loc_ids.append(lid)
            loc_name_map[lid] = l.get("name", "Location")

    c_docs = await db["client_list"].find({"$or": [{"_id": {"$in": client_aliases}}, {"id": {"$in": client_aliases}}, {"user_id": {"$in": client_aliases}}]}).to_list(length=10)
    for c_doc in c_docs:
        if c_doc and "locations" in c_doc and isinstance(c_doc["locations"], list):
            for l in c_doc["locations"]:
                if isinstance(l, dict):
                    lid = str(l.get("id") or l.get("_id") or "")
                    if lid:
                        if lid not in loc_ids:
                            loc_ids.append(lid)
                        if lid not in loc_name_map:
                            loc_name_map[lid] = l.get("name", "Location")

    client_filter_clauses = [
        {"client_id": {"$in": client_aliases}},
        {"client_ids": {"$in": client_aliases}}
    ]
    if loc_ids:
        client_filter_clauses.append({"location_id": {"$in": loc_ids}})

    query_parts = [{"$or": client_filter_clauses}]

    if location_id:
        query_parts.append({"location_id": location_id})

    if search:
        search_filter = [
            {"room_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"custom_room_name": {"$regex": search, "$options": "i"}},
            {"room_type": {"$regex": search, "$options": "i"}},
            {"type": {"$regex": search, "$options": "i"}},
            {"location_name": {"$regex": search, "$options": "i"}}
        ]
        query_parts.append({"$or": search_filter})

    if len(query_parts) == 1:
        final_query = query_parts[0]
    else:
        final_query = {"$and": query_parts}

    total_count = await db["rooms"].count_documents(final_query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(final_query).sort("room_name", 1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    rooms_list = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id") or r.get("room_id") or "")
        rname = r.get("room_name") or r.get("name") or r.get("custom_room_name") or "Room"
        rtype = r.get("room_type") or r.get("type") or "standard"
        r_loc_id = str(r.get("location_id") or "")
        r_loc_name = r.get("location_name") or loc_name_map.get(r_loc_id, "")

        raw_tasks = r.get("tasks", []) or []
        formatted_tasks = []
        total_photos = 0

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

        if total_photos == 0:
            req_photos = r.get("required_photos", []) or []
            total_photos = len(req_photos) or r.get("photo_number", 0) or r.get("required_photos_count", 0)

        task_num = len(formatted_tasks) or len(raw_tasks) or r.get("task_number", 0) or r.get("tasks_count", 0)

        rooms_list.append(ClientRoomDropdownItem(
            id=rid,
            room_name=rname,
            room_type=rtype,
            location_id=r_loc_id or None,
            location_name=r_loc_name or None,
            floor=r.get("floor", 1),
            duration=r.get("duration", 30),
            cleaning_type=r.get("clean_type") or r.get("cleaning_type", "standard"),
            monthly_cleaning_frequency=r.get("monthly_cleaning_frequency", 4),
            photo_number=total_photos,
            task_number=task_num,
            tasks=formatted_tasks
        ))

    end_idx = skip + limit
    return ClientRoomDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        rooms=rooms_list
    )


@router.get(
    "/locations-dropdown",
    response_model=ClientLocationDropdownPaginatedResponse,
    summary="Client Get Location Dropdown List",
    description="""
### Client Location Dropdowns (Paginated)
Retrieves a paginated list of locations / facilities belonging to the authenticated client for Extra Service request creation.

#### Query Parameters:
- **`search`** (`str`, *Optional*): Search locations by name, address, city, or postal code.
- **`page`** (`int`, *Optional*, default: `1`): Page number.
- **`limit`** (`int`, *Optional*, default: `10`): Items per page.
"""
)
@router.get(
    "/dropdowns/locations",
    response_model=ClientLocationDropdownPaginatedResponse,
    summary="Client Get Location Dropdown List (Alias)",
    include_in_schema=False
)
async def get_client_location_dropdowns(
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Location Dropdowns Endpoint.
    """
    db = get_database()
    client_aliases = await resolve_client_id_aliases(current_user, db)

    # Base query for locations of this client
    query_parts = [{
        "$or": [
            {"client_id": {"$in": client_aliases}},
            {"client_ids": {"$in": client_aliases}},
            {"_id": {"$in": client_aliases}},
            {"id": {"$in": client_aliases}}
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

    # Fetch locations from locations collection
    total_count = await db["locations"].count_documents(final_query)
    skip = (page - 1) * limit
    cursor = db["locations"].find(final_query).sort("name", 1).skip(skip).limit(limit)
    raw_locs = await cursor.to_list(length=limit)

    # Fallback to embedded locations in client_list if collection is empty
    if not raw_locs and total_count == 0:
        c_docs = await db["client_list"].find({"$or": [{"_id": {"$in": client_aliases}}, {"id": {"$in": client_aliases}}, {"user_id": {"$in": client_aliases}}]}).to_list(length=10)
        embedded_locs = []
        for c_doc in c_docs:
            if "locations" in c_doc and isinstance(c_doc["locations"], list):
                embedded_locs.extend(c_doc["locations"])
        if embedded_locs:
            if search:
                s_lower = search.lower()
                embedded_locs = [
                    l for l in embedded_locs
                    if s_lower in str(l.get("name", "")).lower() or s_lower in str(l.get("address", "")).lower()
                ]
            total_count = len(embedded_locs)
            raw_locs = embedded_locs[skip:skip + limit]

    loc_ids = [str(l.get("_id") or l.get("id")) for l in raw_locs if l.get("_id") or l.get("id")]

    # Count rooms for each location in batch
    room_counts = {}
    if loc_ids:
        pipeline = [
            {"$match": {"location_id": {"$in": loc_ids}}},
            {"$group": {"_id": "$location_id", "count": {"$sum": 1}}}
        ]
        agg_res = await db["rooms"].aggregate(pipeline).to_list(length=len(loc_ids))
        for item in agg_res:
            room_counts[str(item["_id"])] = item.get("count", 0)

    locations_list = []
    for l in raw_locs:
        lid = str(l.get("_id") or l.get("id") or "")
        lname = l.get("name") or l.get("location_name") or "Main Location"
        addr = l.get("address") or l.get("street") or ""
        city = l.get("city") or ""
        p_code = l.get("postal_code") or l.get("zip") or ""
        total_rooms = room_counts.get(lid, l.get("total_rooms_count") or l.get("rooms_count", 0))
        plans_cnt = l.get("cleaning_plans_count", 0)

        locations_list.append(ClientLocationDropdownItem(
            id=lid,
            name=lname,
            address=addr or None,
            city=city or None,
            postal_code=p_code or None,
            total_rooms_count=total_rooms,
            cleaning_plans_count=plans_cnt
        ))

    end_idx = skip + limit
    return ClientLocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=(end_idx < total_count),
        locations=locations_list
    )


@router.get(
    "",
    response_model=ExtraServicePaginatedResponse,
    summary="Client List Extra Service Requests",
    description="""
### Client List Extra Service Requests (Paginated)
Returns a paginated list of extra service requests created by the client, with optional status and priority filters.
"""
)
async def list_client_extra_services(
    status_val: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client List Extra Services Endpoint.
    """
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "client_1")
    query = {"client_id": client_id}

    if status_val and status_val.lower() != "all":
        query["status"] = status_val.lower()
    if priority and priority.lower() != "all":
        query["priority"] = {"$regex": priority, "$options": "i"}

    total_count = await db["extra_services"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["extra_services"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_docs = await cursor.to_list(length=limit)

    requests_res = [format_extra_service_list_item(d) for d in raw_docs]
    return ExtraServicePaginatedResponse(total_count=total_count, page=page, limit=limit, requests=requests_res)


@router.get(
    "/{request_id}",
    response_model=ExtraServiceResponse,
    summary="Client Get Single Extra Service Request",
    description="Retrieves single extra service request details by request ID for the client."
)
async def get_client_extra_service_detail(
    request_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Single Extra Service Detail Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")
    return format_extra_service_response(doc)


@router.patch(
    "/{request_id}",
    response_model=ExtraServiceResponse,
    summary="Client Update Extra Service Request",
    description="""
### Client Update Extra Service Request
Updates an existing extra service request if it is still in pending or under_review status.
Allows updating title, preferred_date, priority, description, location_id, room_id, and hierarchical tasks with photos.
"""
)
async def update_client_extra_service(
    request_id: str,
    service_in: ExtraServiceUpdate,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Update Extra Service Endpoint.
    """
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
        prio_str = service_in.priority
        if "high" in prio_str.lower():
            prio_str = "High Priority"
        elif "medium" in prio_str.lower():
            prio_str = "Medium Priority"
        elif "low" in prio_str.lower():
            prio_str = "Low Priority"
        update_fields["priority"] = prio_str
    if service_in.description is not None:
        update_fields["description"] = service_in.description
    if service_in.location_id is not None:
        update_fields["location_id"] = service_in.location_id
    if service_in.room_id is not None:
        update_fields["room_id"] = service_in.room_id

    if service_in.start_time is not None:
        update_fields["start_time"] = service_in.start_time
    if service_in.duration is not None or service_in.duration_minutes is not None:
        d_min = service_in.duration_minutes
        if d_min is None and service_in.duration:
            d_min = _parse_duration_to_minutes(service_in.duration)
        update_fields["duration_minutes"] = d_min
        d_str = service_in.duration
        if not d_str and d_min:
            h = d_min // 60
            m = d_min % 60
            d_str = f"{h}h {m}m" if m else f"{h}h"
        update_fields["duration"] = d_str
        if d_min:
            update_fields["estimated_hours"] = float(d_min / 60.0)
    if service_in.end_time is not None:
        update_fields["end_time"] = service_in.end_time
    elif service_in.start_time or "duration_minutes" in update_fields:
        cur_start = update_fields.get("start_time") or doc.get("start_time")
        cur_dur = update_fields.get("duration_minutes") or doc.get("duration_minutes")
        if cur_start and cur_dur:
            update_fields["end_time"] = _calculate_end_time_str(cur_start, cur_dur)

    if service_in.tasks is not None:
        processed_tasks = []
        for t in service_in.tasks:
            t_dict = t.model_dump() if hasattr(t, "model_dump") else dict(t)
            if not t_dict.get("id"):
                t_dict["id"] = f"t_{uuid.uuid4().hex[:6]}"
            raw_photos = t_dict.get("photo") or []
            processed_photos = []
            for p in raw_photos:
                p_dict = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else {"name": str(p)})
                if not p_dict.get("id"):
                    p_dict["id"] = f"p_{uuid.uuid4().hex[:6]}"
                processed_photos.append(p_dict)
            t_dict["photo"] = processed_photos
            if processed_photos and not t_dict.get("is_photo_req"):
                t_dict["is_photo_req"] = True
            t_dict["is_completed"] = False
            t_dict["completed_at"] = None
            processed_tasks.append(t_dict)
        update_fields["tasks"] = processed_tasks

    await db["extra_services"].update_one({"$or": [{"_id": request_id}, {"id": request_id}]}, {"$set": update_fields})
    updated_doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return format_extra_service_response(updated_doc)


@router.delete(
    "/{request_id}",
    summary="Client Delete Extra Service Request",
    description="Cancels and deletes an extra service request if it is still under review."
)
async def delete_client_extra_service(
    request_id: str,
    current_user: UserInDB = Depends(require_client)
):
    """
    Client Delete Extra Service Endpoint.
    """
    db = get_database()
    doc = await db["extra_services"].find_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Extra service request not found")

    if doc.get("status") not in ["pending", "under_review"]:
        raise HTTPException(status_code=400, detail="Cannot delete request after it has been reviewed or approved")

    await db["extra_services"].delete_one({"$or": [{"_id": request_id}, {"id": request_id}]})
    return {"message": "Extra service request cancelled successfully"}
