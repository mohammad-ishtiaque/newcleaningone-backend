import uuid
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form, Path, Request
from typing import Optional, Dict, Any
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.schemas.shift import (
    ShiftChecklistResponse, SubmitTaskPhotoRequest, SubmitTaskPhotoResponse
)
from app.api.worker_shift_utils import (
    resolve_shift_execution, calculate_cleaning_plan_progress
)
from app.api.worker_shift_checklist_utils import build_shift_checklist_response
from app.api.worker_shifts_legacy_execution import legacy_execution_router

execution_router = APIRouter()
execution_router.include_router(legacy_execution_router)

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@execution_router.get(
    "/{shift_id}/checklist",
    response_model=ShiftChecklistResponse,
    summary="Get Shift Tasks & Required Photos Checklist",
    description="""
### Get Shift Tasks & Required Photos Checklist
Returns the full hierarchical breakdown of all rooms, tasks, and task-specific required photos for this shift / extra service.
Includes photo requirement IDs, names, current review status (`not_uploaded`, `pending_review`, `approved`, `rejected`), rejection reasons, submitted URLs, and summary counters.
"""
)
@execution_router.get(
    "/{shift_id}/tasks-and-photos",
    response_model=ShiftChecklistResponse,
    summary="Get Shift Tasks & Required Photos Checklist (Alias)",
    include_in_schema=False
)
async def get_shift_checklist(
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    shift_doc, _ = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")
    return build_shift_checklist_response(shift_doc)


@execution_router.post(
    "/{shift_id}/rooms/{room_id}/start",
    summary="Start Cleaning Room",
    description="Transitions room status to 'in_progress' and initializes task checklist for worker. Idempotent if already started."
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

    room_name_str = target_room.get("room_name") or target_room.get("name", "Room")

    # Idempotent check: If already started or completed
    if target_room.get("status") in ["in_progress", "completed", "photo_submitted"]:
        return {
            "shift_id": str(shift_doc.get("id") or shift_doc.get("_id")),
            "room_id": room_id,
            "room_name": room_name_str,
            "status": target_room.get("status"),
            "message": f"Room cleaning is already {target_room.get('status')}."
        }

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
    "/{shift_id}/photos/{photo_id}/submit",
    response_model=SubmitTaskPhotoResponse,
    summary="Submit Photo for Manager Review",
    description="""
### Submit Photo for Manager Review
Allows a worker to submit photo proof for a specific required photo ID (`photo_id`).
The backend automatically resolves the linked room, task, photo name, and shift metadata.

**Supported Input Modes**:
- **Multipart Form Upload**: Send `file` containing raw image bytes.
- **Direct Image URL**: Send `photo_url` string.
- **Optional Note/Comment**: Send `comment` detailing the cleaned area.

**Lifecycle**:
1. Photo enters `photo_reviews` collection with `pending_review` status.
2. Managers receive real-time push notification and WebSocket alerts.
3. Upon manager approval, the task, room, and shift completion statuses automatically update.
"""
)
@execution_router.post(
    "/{shift_id}/submit-photo/{photo_id}",
    response_model=SubmitTaskPhotoResponse,
    summary="Submit Photo for Manager Review (Alias)",
    include_in_schema=False
)
async def submit_photo_for_review(
    request: Request,
    shift_id: str = Path(..., description="Target Shift / Execution ID", json_schema_extra={"example": "exec_plan_1129452756_2026-08-26"}),
    photo_id: str = Path(..., description="Target Required Photo ID from checklist", json_schema_extra={"example": "a2763ebc"}),
    file: Optional[UploadFile] = File(None, description="Image file to upload for proof review"),
    photo_url: Optional[str] = Form(None, description="Direct URL of photo (if not uploading raw file)", json_schema_extra={"example": "https://s3.example.com/uploads/floor_clean.jpg"}),
    comment: Optional[str] = Form(None, description="Optional worker note/comment", json_schema_extra={"example": "Floor mopped and corners cleaned"}),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(current_user.id or getattr(current_user, "_id", None))

    content_type = request.headers.get("content-type", "")
    target_photo_id = photo_id
    target_photo_url = photo_url
    target_comment = comment

    # Handle application/json payloads seamlessly
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                target_photo_id = body.get("photo_id") or target_photo_id
                target_photo_url = target_photo_url or body.get("photo_url") or body.get("after_photo_url")
                target_comment = target_comment or body.get("comment") or body.get("notes")
        except Exception:
            pass

    # Handle raw file upload
    if file and hasattr(file, "filename") and file.filename:
        s3 = S3Service()
        file_bytes = await file.read()
        uploaded_url = await s3.upload_file(file_bytes, file.filename, file.content_type or "image/jpeg")
        target_photo_url = uploaded_url or f"/uploads/photo_reviews/{file.filename}"

    if not target_photo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Required photo ID ('photo_id') must be provided in path parameter or request body."
        )

    if not target_photo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide an image file ('file') or direct photo URL ('photo_url') for review."
        )

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        raise HTTPException(status_code=404, detail="Shift not found")

    # Locate the target task and photo automatically from shift checklist
    target_room = None
    target_task = None
    target_photo = None
    is_additional = False

    rooms = shift_doc.get("rooms", [])
    # 1. Search through rooms
    for r in rooms:
        for t in r.get("tasks", []):
            req_photos = t.get("photo", []) or t.get("required_photos", [])
            for p in req_photos:
                if str(p.get("id")) == str(target_photo_id) or str(p.get("photo_id")) == str(target_photo_id):
                    target_room = r
                    target_task = t
                    target_photo = p
                    break
            if target_photo:
                break
        if target_photo:
            break

    # 2. If not found in rooms, check additional tasks
    if not target_photo:
        for at in shift_doc.get("additional_tasks", []):
            req_photos = at.get("photo", []) or at.get("required_photos", [])
            for p in req_photos:
                if str(p.get("id")) == str(target_photo_id) or str(p.get("photo_id")) == str(target_photo_id):
                    target_task = at
                    target_photo = p
                    is_additional = True
                    break
            if target_photo:
                break

    # 3. Check extra_services required_photos root
    if not target_photo and shift_doc.get("required_photos"):
        for p in shift_doc.get("required_photos", []):
            if str(p.get("id")) == str(target_photo_id) or str(p.get("photo_id")) == str(target_photo_id):
                target_photo = p
                break

    if not target_photo:
        raise HTTPException(
            status_code=404,
            detail=f"Required photo ID '{target_photo_id}' not found in shift tasks or checklist"
        )

    now = datetime.now(timezone.utc)
    review_id = f"RV-{uuid.uuid4().hex[:6].upper()}"
    target_photo_name = target_photo.get("name") or target_photo.get("photo_name") or "Required Photo"
    target_room_id = str(target_room.get("room_id") or target_room.get("id") or "") if target_room else "additional"
    target_room_name = (target_room.get("room_name") or target_room.get("name") or "Room") if target_room else "Additional Tasks"
    target_task_id = str(target_task.get("id") or "") if target_task else None
    target_task_name = str(target_task.get("name") or "Task") if target_task else None

    review_doc = {
        "_id": review_id,
        "review_id": review_id,
        "shift_id": shift_id,
        "room_id": target_room_id,
        "task_id": target_task_id,
        "photo_id": target_photo_id,
        "photo_name": target_photo_name,
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
            "room_id": target_room_id,
            "name": target_room_name
        },
        "photo_url": target_photo_url,
        "after_photo_url": target_photo_url,
        "notes": target_comment,
        "status": "pending_review",
        "date_submitted": now,
        "updated_at": now
    }

    photo_entry = {
        "photo_id": target_photo_id,
        "photo_name": target_photo_name,
        "photo_url": target_photo_url,
        "status": "pending_review",
        "review_id": review_id,
        "submitted_at": now,
        "notes": target_comment,
        "rejection_reason": None
    }

    # Update submitted_photos in target task or room
    if target_task:
        if "submitted_photos" not in target_task or not isinstance(target_task["submitted_photos"], list):
            target_task["submitted_photos"] = []
        existing_idx = next((i for i, sp in enumerate(target_task["submitted_photos"]) if str(sp.get("photo_id")) == str(target_photo_id)), None)
        if existing_idx is not None:
            target_task["submitted_photos"][existing_idx] = photo_entry
        else:
            target_task["submitted_photos"].append(photo_entry)

    if target_room:
        if "submitted_photos" not in target_room or not isinstance(target_room["submitted_photos"], list):
            target_room["submitted_photos"] = []
        existing_idx = next((i for i, sp in enumerate(target_room["submitted_photos"]) if str(sp.get("photo_id")) == str(target_photo_id)), None)
        if existing_idx is not None:
            target_room["submitted_photos"][existing_idx] = photo_entry
        else:
            target_room["submitted_photos"].append(photo_entry)
        if target_room.get("status") == "pending":
            target_room["status"] = "in_progress"

    doc_id = shift_doc.get("_id")
    update_data: Dict[str, Any] = {
        "rooms": rooms,
        "updated_at": now
    }
    if shift_doc.get("additional_tasks"):
        update_data["additional_tasks"] = shift_doc["additional_tasks"]

    await asyncio.gather(
        db["photo_reviews"].insert_one(review_doc),
        db[coll_name].update_one({"_id": doc_id}, {"$set": update_data})
    )

    # Trigger Push Notification to Managers & Broadcast WebSocket asynchronously
    async def _notify_photo_review():
        try:
            from app.services.notification_service import NotificationService
            from app.api.chat import ws_manager
            notif_service = NotificationService()
            await notif_service.create_notification(
                title="New Photo Submitted for Review",
                message=f"{getattr(current_user, 'full_name', 'Worker')} submitted photo for '{target_photo_name}' in {target_room_name}.",
                notification_type="photo_review",
                recipient_type="admin",
                data={
                    "review_id": review_id,
                    "shift_id": shift_id,
                    "photo_id": target_photo_id,
                    "photo_url": target_photo_url
                }
            )

            admin_cursor = db["users"].find({"role": {"$in": ["admin", "manager"]}})
            admin_ids = [str(u.get("_id") or u.get("id")) async for u in admin_cursor]
            await ws_manager.broadcast_to_users({
                "type": "new_photo_review",
                "review_id": review_id,
                "shift_id": shift_id,
                "room_name": target_room_name,
                "photo_name": target_photo_name,
                "photo_url": target_photo_url
            }, admin_ids)
        except Exception as e:
            print(f"Error notifying manager of photo submission: {e}")

    asyncio.create_task(_notify_photo_review())

    return SubmitTaskPhotoResponse(
        review_id=review_id,
        photo_id=target_photo_id,
        status="pending_review",
        message="Photo submitted successfully for manager review",
        photo_url=target_photo_url,
        photo_name=target_photo_name,
        task_id=target_task_id,
        task_name=target_task_name,
        room_id=target_room_id,
        room_name=target_room_name
    )


@execution_router.post(
    "/{shift_id}/submit-task-photo",
    response_model=SubmitTaskPhotoResponse,
    summary="Submit Photo for Manager Review (Compatibility)",
    include_in_schema=False
)
async def submit_task_photo_compatibility(
    request: Request,
    shift_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    body = await request.json()
    photo_id = body.get("photo_id")
    if not photo_id:
        raise HTTPException(status_code=400, detail="photo_id is required in body")
    return await submit_photo_for_review(
        request=request,
        shift_id=shift_id,
        photo_id=photo_id,
        file=None,
        photo_url=body.get("photo_url"),
        comment=body.get("comment"),
        current_user=current_user
    )

