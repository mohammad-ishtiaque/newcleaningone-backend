import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.client_list import (
    RoomCreate, RoomUpdate, RoomResponse, RoomPaginatedResponse,
    RequiredPhotoResponse, CleaningTaskResponse, TaskPhotoResponse
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

room_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Room Management"])


async def _format_room_response(doc: dict, db) -> RoomResponse:
    r_id = str(doc.get("_id") or doc.get("id") or doc.get("room_id") or "")
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    lid = str(doc.get("location_id") or "")
    lname = str(doc.get("location_name") or "")
    cid = str(doc.get("client_id") or "")
    cname = str(doc.get("company_name") or "")

    if lid and (not lname or not cid or not cname):
        ldoc = await db["locations"].find_one({"$or": [{"_id": lid}, {"id": lid}]})
        if ldoc:
            if not lname:
                lname = ldoc.get("name", "")
            if not cid:
                cid = ldoc.get("client_id", "")
            if not cname:
                cname = ldoc.get("company_name", "")

    if cid and not cname:
        cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]})
        if cdoc:
            cname = cdoc.get("company_name", "")

    tasks_raw = doc.get("tasks", [])
    tasks = []
    flat_required_photos = []

    if isinstance(tasks_raw, list):
        for t in tasks_raw:
            if isinstance(t, dict):
                t_id = str(t.get("id") or t.get("_id") or uuid.uuid4().hex[:8])
                t_name = t.get("name", "Task")
                t_freq = t.get("frequency_type", "every_visit")
                is_req = bool(t.get("is_photo_req", False))
                t_weekly_days = t.get("weekly_days")
                t_monthly_dates = t.get("monthly_dates")
                t_fixed_date = t.get("fixed_date")
                t_duration_minutes = t.get("duration_minutes")

                raw_photos = t.get("photo") or t.get("photos") or []
                task_photos = []
                if isinstance(raw_photos, list):
                    for p in raw_photos:
                        if isinstance(p, dict):
                            p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                            p_name = p.get("name", "Photo")
                            task_photos.append(TaskPhotoResponse(id=p_id, name=p_name))
                            flat_required_photos.append(RequiredPhotoResponse(id=p_id, name=p_name, frequency_type=t_freq))
                        elif isinstance(p, str):
                            p_id = uuid.uuid4().hex[:8]
                            task_photos.append(TaskPhotoResponse(id=p_id, name=p))
                            flat_required_photos.append(RequiredPhotoResponse(id=p_id, name=p, frequency_type=t_freq))
                        elif hasattr(p, "name"):
                            p_id = str(getattr(p, "id", None) or uuid.uuid4().hex[:8])
                            p_name = getattr(p, "name", "Photo")
                            task_photos.append(TaskPhotoResponse(id=p_id, name=p_name))
                            flat_required_photos.append(RequiredPhotoResponse(id=p_id, name=p_name, frequency_type=t_freq))

                if task_photos and not is_req:
                    is_req = True

                tasks.append(CleaningTaskResponse(
                    id=t_id,
                    name=t_name,
                    frequency_type=t_freq,
                    is_photo_req=is_req,
                    photo=task_photos,
                    total_photos_required=len(task_photos),
                    weekly_days=t_weekly_days,
                    monthly_dates=t_monthly_dates,
                    fixed_date=t_fixed_date,
                    duration_minutes=t_duration_minutes
                ))
            elif isinstance(t, str):
                tasks.append(CleaningTaskResponse(
                    id=uuid.uuid4().hex[:8],
                    name=t,
                    frequency_type="every_visit",
                    is_photo_req=False,
                    photo=[],
                    total_photos_required=0
                ))
            elif hasattr(t, "name"):
                t_id = str(getattr(t, "id", None) or uuid.uuid4().hex[:8])
                t_name = getattr(t, "name", "Task")
                t_freq = getattr(t, "frequency_type", "every_visit")
                is_req = bool(getattr(t, "is_photo_req", False))
                t_weekly_days = getattr(t, "weekly_days", None)
                t_monthly_dates = getattr(t, "monthly_dates", None)
                t_fixed_date = getattr(t, "fixed_date", None)
                t_duration_minutes = getattr(t, "duration_minutes", None)
                raw_photos = getattr(t, "photo", []) or []
                task_photos = []
                for p in raw_photos:
                    p_id = str(getattr(p, "id", None) or uuid.uuid4().hex[:8]) if not isinstance(p, dict) else str(p.get("id") or uuid.uuid4().hex[:8])
                    p_name = getattr(p, "name", "Photo") if not isinstance(p, dict) else p.get("name", "Photo")
                    task_photos.append(TaskPhotoResponse(id=p_id, name=p_name))
                    flat_required_photos.append(RequiredPhotoResponse(id=p_id, name=p_name, frequency_type=t_freq))
                if task_photos and not is_req:
                    is_req = True
                tasks.append(CleaningTaskResponse(
                    id=t_id,
                    name=t_name,
                    frequency_type=t_freq,
                    is_photo_req=is_req,
                    photo=task_photos,
                    total_photos_required=len(task_photos),
                    weekly_days=t_weekly_days,
                    monthly_dates=t_monthly_dates,
                    fixed_date=t_fixed_date,
                    duration_minutes=t_duration_minutes
                ))

    # Backward compatibility with legacy required_photos at room level
    legacy_photos_raw = doc.get("required_photos", [])
    if isinstance(legacy_photos_raw, list) and legacy_photos_raw and not flat_required_photos:
        for p in legacy_photos_raw:
            if isinstance(p, dict):
                p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                flat_required_photos.append(RequiredPhotoResponse(
                    id=p_id,
                    name=p.get("name", "Photo"),
                    frequency_type=p.get("frequency_type", "every_visit")
                ))
            elif isinstance(p, str):
                flat_required_photos.append(RequiredPhotoResponse(
                    id=uuid.uuid4().hex[:8],
                    name=p,
                    frequency_type="every_visit"
                ))
            elif hasattr(p, "name"):
                flat_required_photos.append(RequiredPhotoResponse(
                    id=str(getattr(p, "id", None) or uuid.uuid4().hex[:8]),
                    name=getattr(p, "name", "Photo"),
                    frequency_type=getattr(p, "frequency_type", "every_visit")
                ))

    total_photos_cnt = sum(t.total_photos_required for t in tasks)
    if total_photos_cnt == 0 and flat_required_photos:
        total_photos_cnt = len(flat_required_photos)

    task_cnt = len(tasks) if tasks else (doc.get("task_number") or doc.get("tasks_count", 0))

    return RoomResponse(
        id=r_id,
        room_name=doc.get("room_name") or doc.get("name", "Room"),
        room_type=doc.get("room_type") or doc.get("type", "standard"),
        client_id=cid,
        company_name=cname or "Client Company",
        location_id=lid,
        location_name=lname or "Location Name",
        floor=doc.get("floor", 1),
        duration=doc.get("duration") or doc.get("est_cleaning_duration_minutes", 30),
        monthly_cleaning_frequency=doc.get("monthly_cleaning_frequency", 0),
        required_photos=flat_required_photos,
        photo_number=total_photos_cnt,
        total_photos_required=total_photos_cnt,
        task_number=task_cnt,
        clean_type=doc.get("clean_type") or doc.get("cleaning_type", "standard"),
        tasks=tasks,
        created_at=c_at,
        updated_at=u_at
    )


@room_mgmt_router.post("/locations/{location_id}/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED, summary="Create Room")
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
    company_name = l_doc.get("company_name", "")
    location_name = l_doc.get("name", "")

    room_data = room_in.model_dump(exclude_unset=True)
    room_data.pop("location_id", None)

    # Process tasks to ensure all tasks and photos have backend generated IDs
    processed_tasks = []
    total_photos_count = 0
    if "tasks" in room_data and isinstance(room_data["tasks"], list):
        for t in room_data["tasks"]:
            if isinstance(t, dict):
                t_id = t.get("id") or uuid.uuid4().hex[:8]
                raw_photos = t.get("photo") or []
                processed_photos = []
                for p in raw_photos:
                    if isinstance(p, dict):
                        p_id = p.get("id") or uuid.uuid4().hex[:8]
                        processed_photos.append({
                            "id": p_id,
                            "name": p.get("name", "Photo")
                        })
                is_req = bool(t.get("is_photo_req", False) or len(processed_photos) > 0)
                total_photos_count += len(processed_photos)
                processed_tasks.append({
                    "id": t_id,
                    "name": t.get("name", "Task"),
                    "frequency_type": t.get("frequency_type", "every_visit"),
                    "is_photo_req": is_req,
                    "photo": processed_photos,
                    "weekly_days": t.get("weekly_days"),
                    "monthly_dates": t.get("monthly_dates"),
                    "fixed_date": t.get("fixed_date"),
                    "duration_minutes": t.get("duration_minutes")
                })
        room_data["tasks"] = processed_tasks

    # Process legacy required_photos if present
    if "required_photos" in room_data and isinstance(room_data["required_photos"], list):
        processed_legacy_photos = []
        for p in room_data["required_photos"]:
            if isinstance(p, dict):
                p_id = p.get("id") or uuid.uuid4().hex[:8]
                processed_legacy_photos.append({
                    "id": p_id,
                    "name": p.get("name", "Photo"),
                    "frequency_type": p.get("frequency_type", "every_visit")
                })
        room_data["required_photos"] = processed_legacy_photos
        if total_photos_count == 0:
            total_photos_count = len(processed_legacy_photos)

    doc = {
        "_id": room_id,
        "id": room_id,
        "location_id": location_id,
        "location_name": location_name,
        "client_id": client_id,
        "company_name": company_name,
        **room_data,
        "photo_number": total_photos_count,
        "task_number": len(processed_tasks),
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    await db["rooms"].insert_one(doc)
    await db["locations"].update_one(
        {"$or": [{"_id": location_id}, {"id": location_id}]},
        {"$inc": {"rooms_count": 1, "number_of_rooms": 1}}
    )

    return await _format_room_response(doc, db)


@room_mgmt_router.get("/rooms/{room_id}", response_model=RoomResponse, summary="Get Full Room Details")
async def get_room_details(
    room_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    r_doc = await db["rooms"].find_one({"$or": [{"_id": room_id}, {"id": room_id}, {"room_id": room_id}]})
    if not r_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    return await _format_room_response(r_doc, db)


@room_mgmt_router.get("/locations/{location_id}/rooms", response_model=RoomPaginatedResponse, include_in_schema=False, summary="List Rooms For Location")
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
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"room_name": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    room_items = [await _format_room_response(r, db) for r in raw_rooms]

    return RoomPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=room_items
    )


@room_mgmt_router.patch("/rooms/{room_id}", response_model=RoomResponse, summary="Update Room")
async def update_room(
    room_id: str,
    room_in: RoomUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    r_doc = await db["rooms"].find_one({"$or": [{"_id": room_id}, {"id": room_id}, {"room_id": room_id}]})
    if not r_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    update_data = room_in.model_dump(exclude_unset=True)
    if not update_data:
        return await _format_room_response(r_doc, db)

    update_data["updated_at"] = datetime.now(timezone.utc)

    # Process tasks to ensure all tasks and photos have IDs
    if "tasks" in update_data and isinstance(update_data["tasks"], list):
        processed_tasks = []
        total_photos_count = 0
        for t in update_data["tasks"]:
            if isinstance(t, dict):
                t_id = t.get("id") or uuid.uuid4().hex[:8]
                raw_photos = t.get("photo") or []
                processed_photos = []
                for p in raw_photos:
                    if isinstance(p, dict):
                        p_id = p.get("id") or uuid.uuid4().hex[:8]
                        processed_photos.append({
                            "id": p_id,
                            "name": p.get("name", "Photo")
                        })
                is_req = bool(t.get("is_photo_req", False) or len(processed_photos) > 0)
                total_photos_count += len(processed_photos)
                processed_tasks.append({
                    "id": t_id,
                    "name": t.get("name", "Task"),
                    "frequency_type": t.get("frequency_type", "every_visit"),
                    "is_photo_req": is_req,
                    "photo": processed_photos,
                    "weekly_days": t.get("weekly_days"),
                    "monthly_dates": t.get("monthly_dates"),
                    "fixed_date": t.get("fixed_date"),
                    "duration_minutes": t.get("duration_minutes")
                })
        update_data["tasks"] = processed_tasks
        update_data["task_number"] = len(processed_tasks)
        update_data["photo_number"] = total_photos_count

    if "required_photos" in update_data and isinstance(update_data["required_photos"], list):
        for p in update_data["required_photos"]:
            if isinstance(p, dict) and not p.get("id"):
                p["id"] = uuid.uuid4().hex[:8]

    await db["rooms"].update_one(
        {"_id": r_doc["_id"]},
        {"$set": update_data}
    )

    updated_doc = await db["rooms"].find_one({"_id": r_doc["_id"]})
    return await _format_room_response(updated_doc, db)


@room_mgmt_router.delete("/rooms/{room_id}", status_code=status.HTTP_200_OK, summary="Delete Room")
async def delete_room(
    room_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    r_doc = await db["rooms"].find_one({"$or": [{"_id": room_id}, {"id": room_id}, {"room_id": room_id}]})
    if not r_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    await db["rooms"].delete_one({"_id": r_doc["_id"]})

    if r_doc.get("location_id"):
        await db["locations"].update_one(
            {"$or": [{"_id": r_doc["location_id"]}, {"id": r_doc["location_id"]}]},
            {"$inc": {"rooms_count": -1, "number_of_rooms": -1}}
        )

    return {"message": "Room deleted successfully", "room_id": room_id}
