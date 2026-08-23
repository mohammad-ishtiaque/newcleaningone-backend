import uuid
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.schemas.shift import (
    ShiftResponse, ShiftExecutionStateResponse, PhotoUploadResponse, ShiftRoomDetail
)
from app.api.worker_shift_utils import (
    resolve_shift_execution, calculate_cleaning_plan_progress
)

execution_router = APIRouter()

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@execution_router.post(
    "/{shift_id}/rooms/{room_id}/start",
    summary="Start Cleaning Room",
    description="Transitions room status to 'in_progress' and initializes task checklist for worker."
)
async def start_cleaning_room(
    shift_id: str,
    room_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id) or str(r.get("id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    now = datetime.now(timezone.utc)
    target_room["status"] = "in_progress"
    target_room["approval_status"] = "none"
    target_room["started_at"] = now

    progress = calculate_cleaning_plan_progress(shift_doc)
    doc_id = shift_doc.get("_id")

    await db[coll_name].update_one(
        {"_id": doc_id},
        {"$set": {
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "status": "in_progress",
            "updated_at": now
        }}
    )

    room_name_str = target_room.get("room_name") or target_room.get("name", "Room")
    try:
        from app.services.shift_ws_service import broadcast_room_status_event
        await broadcast_room_status_event(
            db=db,
            shift_doc=shift_doc,
            room_id=room_id,
            room_name=room_name_str,
            status_val="in_progress",
            started_at=now
        )
    except Exception:
        pass

    return {
        "shift_id": str(shift_doc.get("id") or shift_doc.get("_id")),
        "room_id": room_id,
        "room_name": room_name_str,
        "status": "in_progress",
        "message": "Room status updated to in_progress. Cleaning started."
    }


@execution_router.post(
    "/{shift_id}/rooms/{room_id}/tasks/{task_id}/toggle",
    response_model=ShiftExecutionStateResponse,
    summary="Toggle Room Task Completion State",
    description="Toggles task completion state for a room. Updates room status and recalculates item-based shift progress."
)
async def toggle_room_task_completion(
    shift_id: str,
    room_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id) or str(r.get("id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    tasks = target_room.get("tasks", [])
    target_task = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if not target_task:
        raise HTTPException(status_code=404, detail=f"Task ID '{task_id}' not found in room '{room_id}'")

    now = datetime.now(timezone.utc)
    new_state = not target_task.get("is_completed", False)
    target_task["is_completed"] = new_state
    target_task["completed_at"] = now if new_state else None

    completed_tasks = sum(1 for t in tasks if t.get("is_completed"))
    target_room["completed_tasks_count"] = completed_tasks
    if len(tasks) > 0 and all(t.get("is_completed") for t in tasks):
        target_room["status"] = "photo_submitted"
    elif target_room.get("status") in ["pending", None] and completed_tasks > 0:
        target_room["status"] = "in_progress"

    progress = calculate_cleaning_plan_progress(shift_doc)
    doc_id = shift_doc.get("_id")

    await db[coll_name].update_one(
        {"_id": doc_id},
        {"$set": {
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "completed_tasks_count": progress["completed_tasks_count"],
            "updated_at": now
        }}
    )

    try:
        from app.services.shift_ws_service import broadcast_task_toggled_event
        await broadcast_task_toggled_event(
            db=db,
            shift_doc=shift_doc,
            room_id=room_id,
            task_id=task_id,
            is_completed=new_state,
            overall_progress_percentage=progress["overall_progress_percentage"],
            completed_tasks_count=completed_tasks,
            total_tasks_count=total_tasks
        )
    except Exception:
        pass

    updated_shift = await db[coll_name].find_one({"_id": doc_id})
    return ShiftExecutionStateResponse(
        shift_id=str(updated_shift.get("id") or updated_shift.get("_id")),
        client_id=str(updated_shift.get("client_id", "")),
        client_name=updated_shift.get("client_name", "Client"),
        location_id=str(updated_shift.get("location_id", "")),
        location_name=updated_shift.get("location_name", "Location"),
        status=updated_shift.get("status", "in_progress"),
        overall_progress_percentage=progress["overall_progress_percentage"],
        total_rooms_count=progress["total_rooms_count"],
        completed_rooms_count=progress["completed_rooms_count"],
        in_progress_rooms_count=progress["in_progress_rooms_count"],
        pending_rooms_count=progress["pending_rooms_count"],
        rooms=[ShiftRoomDetail(**r) for r in updated_shift.get("rooms", [])]
    )


@execution_router.post(
    "/{shift_id}/rooms/{room_id}/photos",
    response_model=PhotoUploadResponse,
    summary="Worker Submit Room Photo (Before & After) with Custom PyTorch AI Analysis"
)
async def upload_worker_room_photo(
    shift_id: str,
    room_id: str,
    photo_type: str = Form("after"),
    photo_id: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))
    worker_name = getattr(current_user, "full_name", "Worker")

    os.makedirs("uploads/photo_reviews", exist_ok=True)
    file_ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{shift_id}_{room_id}_{photo_type}_{uuid.uuid4().hex[:6]}.{file_ext}"
    file_path = os.path.join("uploads/photo_reviews", filename)

    contents = await file.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    photo_url = f"/uploads/photo_reviews/{filename}"
    now = datetime.now(timezone.utc)

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    c_name = shift_doc.get("client_name", "Client") if shift_doc else "Client"
    l_name = shift_doc.get("location_name", "Location") if shift_doc else "Location"
    r_name = f"Room {room_id}"
    if shift_doc:
        for r in shift_doc.get("rooms", []):
            if str(r.get("id") or r.get("room_id") or r.get("name")) == room_id:
                r_name = r.get("room_name") or r.get("name", r_name)
                break

    rev_id = f"RV-{uuid.uuid4().hex[:6].upper()}"
    review_query = {"shift_id": shift_id, "room.room_id": room_id}
    if photo_id:
        review_query["photo_id"] = photo_id
    existing_rev = await db["photo_reviews"].find_one(review_query)

    before_path = None
    after_path = file_path
    if photo_type == "before":
        before_path = file_path
        after_path = existing_rev.get("after_photo_path") if existing_rev else file_path
    elif existing_rev:
        before_path = existing_rev.get("before_photo_path")

    from app.services.ai_vision_engine import analyze_photo_quality
    ai_score, ai_conf, breakdown = await analyze_photo_quality(after_photo_path=after_path, before_photo_path=before_path)

    review_doc = {
        "review_id": existing_rev.get("review_id", rev_id) if existing_rev else rev_id,
        "shift_id": shift_id,
        "photo_id": photo_id,
        "task_id": task_id,
        "cleaner": {
            "worker_id": worker_id,
            "name": worker_name,
            "profile_picture": getattr(current_user, "profile_photo", None)
        },
        "client": {
            "client_id": str(shift_doc.get("client_id", "c_1")) if shift_doc else "c_1",
            "name": c_name
        },
        "location": {
            "location_id": str(shift_doc.get("location_id", "l_1")) if shift_doc else "l_1",
            "name": l_name
        },
        "room": {
            "room_id": room_id,
            "name": r_name
        },
        "before_photo_url": photo_url if photo_type == "before" else (existing_rev.get("before_photo_url") if existing_rev else None),
        "before_photo_path": before_path,
        "after_photo_url": photo_url if photo_type == "after" else (existing_rev.get("after_photo_url") if existing_rev else photo_url),
        "after_photo_path": after_path,
        "photo_url": photo_url,
        "photo_name": f"{r_name} {photo_type.capitalize()} Photo",
        "ai_score": ai_score,
        "ai_confidence": ai_conf,
        "ai_feature_breakdown": breakdown,
        "status": "pending_review",
        "date_submitted": now,
        "updated_at": now
    }

    await db["photo_reviews"].update_one(
        {"review_id": review_doc["review_id"]},
        {"$set": review_doc},
        upsert=True
    )

    # Also register photo into shift execution room's submitted_photos and specific task
    if shift_doc and coll_name:
        rooms = shift_doc.get("rooms", [])
        target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id) or str(r.get("id")) == str(room_id)), None)
        if target_room:
            if "submitted_photos" not in target_room or not isinstance(target_room["submitted_photos"], list):
                target_room["submitted_photos"] = []
            
            photo_record = {
                "photo_id": photo_id or str(uuid.uuid4()),
                "task_id": task_id,
                "photo_url": photo_url,
                "photo_type": photo_type,
                "submitted_at": now,
                "review_id": review_doc["review_id"],
                "status": "pending_review"
            }
            target_room["submitted_photos"].append(photo_record)

            # Check if this photo corresponds to a specific task
            r_tasks = target_room.get("tasks", [])
            for t in r_tasks:
                matches_task = (task_id and str(t.get("id")) == str(task_id))
                matches_photo = False
                if photo_id:
                    task_photos = t.get("photo", [])
                    if any(str(p.get("id")) == str(photo_id) for p in task_photos):
                        matches_photo = True

                if matches_task or matches_photo:
                    if "submitted_photos" not in t or not isinstance(t["submitted_photos"], list):
                        t["submitted_photos"] = []
                    t["submitted_photos"].append(photo_record)

                    req_count = len(t.get("photo", [])) or t.get("total_photos_required", 1)
                    if len(t["submitted_photos"]) >= req_count:
                        t["is_completed"] = True
                        t["completed_at"] = now

            # If all tasks in room are now completed, mark room photo_submitted
            all_tasks_done = len(r_tasks) > 0 and all(t.get("is_completed") for t in r_tasks)
            if all_tasks_done:
                target_room["status"] = "photo_submitted"
            elif target_room.get("status") in ["pending", None]:
                target_room["status"] = "in_progress"

            progress = calculate_cleaning_plan_progress(shift_doc)
            doc_id = shift_doc.get("_id")
            await db[coll_name].update_one(
                {"_id": doc_id},
                {"$set": {
                    "rooms": rooms,
                    "overall_progress_percentage": progress["overall_progress_percentage"],
                    "completed_rooms_count": progress["completed_rooms_count"],
                    "in_progress_rooms_count": progress["in_progress_rooms_count"],
                    "pending_rooms_count": progress["pending_rooms_count"],
                    "completed_tasks_count": progress["completed_tasks_count"],
                    "updated_at": now
                }}
            )

    try:
        from app.services.shift_ws_service import broadcast_photo_submitted_event
        await broadcast_photo_submitted_event(
            db=db,
            shift_doc=shift_doc,
            room_id=room_id,
            review_id=review_doc["review_id"],
            photo_url=photo_url,
            photo_type=photo_type,
            ai_score=ai_score
        )
    except Exception:
        pass

    return PhotoUploadResponse(
        review_id=review_doc["review_id"],
        shift_id=shift_id,
        room_id=room_id,
        photo_url=photo_url,
        status="pending_review",
        message=f"{photo_type.capitalize()} photo submitted successfully. Custom PyTorch AI Score: {ai_score}% ({ai_conf.capitalize()} confidence)"
    )


@execution_router.post(
    "/{shift_id}/rooms/{room_id}/complete",
    summary="Complete Room & Upload Proof",
    description="Validates that all tasks are completed before submitting room for Manager review."
)
async def complete_room_and_upload_proof(
    shift_id: str,
    room_id: str,
    after_photo: Optional[UploadFile] = File(None),
    before_photo: Optional[UploadFile] = File(None),
    notes: Optional[str] = Form(None),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id) or str(r.get("id")) == str(room_id)), None)
    if not target_room:
        raise HTTPException(status_code=404, detail=f"Room ID '{room_id}' not found in shift")

    # Validate that all tasks in the room are checked off
    tasks = target_room.get("tasks", [])
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.get("is_completed"))

    if total_tasks > 0 and completed_tasks < total_tasks:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete room clean. All checklist tasks ({completed_tasks}/{total_tasks} completed) must be finished before submitting for review."
        )

    s3_service = S3Service()
    after_url = "/uploads/photo_reviews/sample_after.jpg"
    before_url = None

    if after_photo and hasattr(after_photo, "filename") and after_photo.filename:
        after_bytes = await after_photo.read()
        after_url = await s3_service.upload_file(after_bytes, after_photo.filename, after_photo.content_type)

    if before_photo and hasattr(before_photo, "filename") and before_photo.filename:
        before_bytes = await before_photo.read()
        before_url = await s3_service.upload_file(before_bytes, before_photo.filename, before_photo.content_type)

    now = datetime.now(timezone.utc)
    review_id = f"RV-{uuid.uuid4().hex[:6].upper()}"

    review_doc = {
        "_id": review_id,
        "review_id": review_id,
        "shift_id": shift_id,
        "cleaner": {
            "worker_id": worker_id,
            "name": getattr(current_user, "full_name", "Worker"),
            "profile_picture": getattr(current_user, "profile_photo", None)
        },
        "client": {
            "client_id": str(shift_doc.get("client_id", "")),
            "name": shift_doc.get("client_name", "Client")
        },
        "location": {
            "location_id": str(shift_doc.get("location_id", "")),
            "name": shift_doc.get("location_name", "Location")
        },
        "room": {
            "room_id": room_id,
            "name": target_room.get("room_name") or target_room.get("name", "Room")
        },
        "photo_url": after_url,
        "after_photo_url": after_url,
        "before_photo_url": before_url,
        "notes": notes,
        "status": "pending_review",
        "date_submitted": now
    }

    await db["photo_reviews"].insert_one(review_doc)

    photo_entry = {
        "photo_id": str(uuid.uuid4()),
        "photo_url": after_url,
        "before_photo_url": before_url,
        "submitted_at": now,
        "review_id": review_id,
        "notes": notes,
        "status": "pending_review"
    }

    if "submitted_photos" not in target_room or not isinstance(target_room["submitted_photos"], list):
        target_room["submitted_photos"] = []
    target_room["submitted_photos"].append(photo_entry)

    target_room["status"] = "photo_submitted"
    target_room["approval_status"] = "pending"
    target_room["notes"] = notes

    progress = calculate_cleaning_plan_progress(shift_doc)
    doc_id = shift_doc.get("_id")

    await db[coll_name].update_one(
        {"_id": doc_id},
        {"$set": {
            "rooms": rooms,
            "overall_progress_percentage": progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "updated_at": now
        }}
    )

    room_name_str = target_room.get("room_name") or target_room.get("name", "Room")
    try:
        from app.services.shift_ws_service import broadcast_room_completed_event
        await broadcast_room_completed_event(
            db=db,
            shift_doc=shift_doc,
            room_id=room_id,
            room_name=room_name_str,
            review_id=review_id,
            overall_progress_percentage=progress["overall_progress_percentage"]
        )
    except Exception:
        pass

    return {
        "review_id": review_id,
        "shift_id": shift_id,
        "room_id": room_id,
        "room_name": room_name_str,
        "status": "photo_submitted",
        "approval_status": "pending",
        "photo_url": after_url,
        "message": "Room clean proof uploaded successfully and submitted for Manager review."
    }


@execution_router.post(
    "/{shift_id}/complete",
    response_model=ShiftResponse,
    summary="Mark Entire Shift Completed",
    description="Worker marks shift complete after all assigned rooms are cleaned and approved.",
    include_in_schema=False
)
async def mark_shift_completed(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    rooms = shift_doc.get("rooms", [])
    incomplete_rooms = [r.get("room_name") or r.get("name", r.get("room_id")) for r in rooms if r.get("status") != "completed"]
    if incomplete_rooms:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete shift. The following rooms are not yet approved/completed: {', '.join(incomplete_rooms)}"
        )

    now = datetime.now(timezone.utc)
    doc_id = shift_doc.get("_id")

    await db[coll_name].update_one(
        {"_id": doc_id},
        {"$set": {
            "status": "completed",
            "overall_progress_percentage": 100.0,
            "updated_at": now
        }}
    )

    try:
        from app.services.shift_ws_service import broadcast_shift_completed_event
        await broadcast_shift_completed_event(db=db, shift_doc=shift_doc)
    except Exception:
        pass

    updated_shift = await db[coll_name].find_one({"_id": doc_id})
    updated_shift["id"] = str(updated_shift.get("_id") or updated_shift.get("id"))
    progress = calculate_cleaning_plan_progress(updated_shift)
    updated_shift["overall_progress_percentage"] = 100.0
    updated_shift["completed_rooms_count"] = progress["completed_rooms_count"]
    updated_shift["in_progress_rooms_count"] = progress["in_progress_rooms_count"]
    updated_shift["pending_rooms_count"] = progress["pending_rooms_count"]

    return ShiftResponse(**updated_shift)
