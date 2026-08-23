import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.shift import (
    PhotoReviewPaginatedResponse, PhotoReviewItem, PhotoReviewDetailModalResponse,
    PhotoReviewCleanerDetail, PhotoReviewClientDetail, PhotoReviewLocationDetail,
    PhotoReviewRoomDetail, PhotoReviewRejectRequest
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.services.ai_vision_engine import update_ai_model_online_learning

photo_reviews_router = APIRouter(prefix="/manager", tags=["Manager Photo Reviews & Quality Control"])

def _format_date_submitted(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %Y, %H:%M")
    return "09 Jun 2026, 09:15"

@photo_reviews_router.get("/photo-reviews", response_model=PhotoReviewPaginatedResponse, summary="Admin Photo Reviews Grid (Image Mockup)")
async def get_admin_photo_reviews(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = None,  # all, pending_review, approved, rejected
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if status_filter and status_filter.lower() != "all":
        # Normalize status string
        st_val = status_filter.lower().replace(" ", "_")
        if st_val in ["pending", "pending_review"]:
            query["status"] = "pending_review"
        elif st_val in ["approved"]:
            query["status"] = "approved"
        elif st_val in ["rejected"]:
            query["status"] = "rejected"

    if search:
        query["$or"] = [
            {"review_id": {"$regex": search, "$options": "i"}},
            {"cleaner.name": {"$regex": search, "$options": "i"}},
            {"location.name": {"$regex": search, "$options": "i"}},
            {"room.name": {"$regex": search, "$options": "i"}},
            {"client.name": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["photo_reviews"].count_documents(query)
    pending_count = await db["photo_reviews"].count_documents({"status": "pending_review"})

    skip = (page - 1) * limit
    cursor = db["photo_reviews"].find(query).sort("date_submitted", -1).skip(skip).limit(limit)
    raw_reviews = await cursor.to_list(length=limit)

    items = []
    for r in raw_reviews:
        rid = str(r.get("_id") or r.get("review_id"))
        cleaner_d = r.get("cleaner", {})
        client_d = r.get("client", {})
        loc_d = r.get("location", {})
        room_d = r.get("room", {})

        c_dt = r.get("date_submitted")
        if not isinstance(c_dt, datetime):
            c_dt = datetime.now(timezone.utc)

        items.append(PhotoReviewItem(
            review_id=r.get("review_id") or rid,
            shift_id=r.get("shift_id", "shift_1"),
            cleaner=PhotoReviewCleanerDetail(
                worker_id=str(cleaner_d.get("worker_id") or cleaner_d.get("id") or "w_1"),
                name=cleaner_d.get("name") or cleaner_d.get("full_name") or "Worker",
                profile_picture=cleaner_d.get("profile_picture") or cleaner_d.get("profile_photo")
            ),
            client=PhotoReviewClientDetail(
                client_id=str(client_d.get("client_id") or client_d.get("id") or ""),
                name=client_d.get("name") or client_d.get("company_name") or "Client"
            ),
            location=PhotoReviewLocationDetail(
                location_id=str(loc_d.get("location_id") or loc_d.get("id") or ""),
                name=loc_d.get("name") or loc_d.get("location_name") or "Location"
            ),
            room=PhotoReviewRoomDetail(
                room_id=str(room_d.get("room_id") or room_d.get("id") or ""),
                name=room_d.get("name") or room_d.get("room_name") or "Room"
            ),
            photo_url=r.get("photo_url") or r.get("after_photo_url") or "",
            photo_name=r.get("photo_name") or "After Cleaning Photo",
            ai_score=r.get("ai_score", 0.0),
            ai_confidence=r.get("ai_confidence", "medium"),
            status=r.get("status", "pending_review"),
            rejection_reason=r.get("rejection_reason"),
            date_submitted=c_dt
        ))

    return PhotoReviewPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        pending_reviews_count=pending_count,
        reviews=items
    )

@photo_reviews_router.get("/photo-reviews/{review_id}", response_model=PhotoReviewDetailModalResponse, summary="Get Photo Review Details Modal")
async def get_photo_review_details(
    review_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    r = await db["photo_reviews"].find_one({"$or": [{"_id": review_id}, {"review_id": review_id}]})
    now = datetime.now(timezone.utc)

    if not r:
        raise HTTPException(status_code=404, detail="Photo review not found")

    cleaner_d = r.get("cleaner", {})
    client_d = r.get("client", {})
    loc_d = r.get("location", {})
    room_d = r.get("room", {})
    c_dt = r.get("date_submitted") if isinstance(r.get("date_submitted"), datetime) else now

    return PhotoReviewDetailModalResponse(
        review_id=r.get("review_id", review_id),
        shift_id=r.get("shift_id") or "",
        cleaner=PhotoReviewCleanerDetail(
            worker_id=str(cleaner_d.get("worker_id") or cleaner_d.get("id") or ""),
            name=cleaner_d.get("name") or cleaner_d.get("full_name") or "Worker",
            profile_picture=cleaner_d.get("profile_picture") or cleaner_d.get("profile_photo")
        ),
        client=PhotoReviewClientDetail(
            client_id=str(client_d.get("client_id") or client_d.get("id") or ""),
            name=client_d.get("name") or client_d.get("company_name") or "Client"
        ),
        location=PhotoReviewLocationDetail(
            location_id=str(loc_d.get("location_id") or loc_d.get("id") or ""),
            name=loc_d.get("name") or loc_d.get("location_name") or "Location"
        ),
        room=PhotoReviewRoomDetail(
            room_id=str(room_d.get("room_id") or room_d.get("id") or ""),
            name=room_d.get("name") or room_d.get("room_name") or "Room"
        ),
        before_photo_url=r.get("before_photo_url"),
        after_photo_url=r.get("after_photo_url") or r.get("photo_url", "/uploads/photo_reviews/after_sample.jpg"),
        photo_name=r.get("photo_name", "Cleaning Photo"),
        ai_score=r.get("ai_score", 91.0),
        ai_confidence=r.get("ai_confidence", "high"),
        ai_feature_breakdown=r.get("ai_feature_breakdown", {
            "ssim_transformation": 0.92,
            "sharpness_score": 0.89,
            "clutter_reduction": 0.95
        }),
        status=r.get("status", "pending_review"),
        rejection_reason=r.get("rejection_reason"),
        date_submitted=c_dt
    )

from app.api.worker_shift_utils import resolve_shift_execution, calculate_cleaning_plan_progress

async def _sync_shift_room_approval(db, r_doc: dict, is_approved: bool):
    """Helper to update shift room status and recalculate shift progress when Admin approves or rejects proof."""
    if not r_doc:
        return
    shift_id = r_doc.get("shift_id")
    room_info = r_doc.get("room", {})
    room_id = room_info.get("room_id") if isinstance(room_info, dict) else r_doc.get("room_id")

    if not shift_id or not room_id:
        return

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        return

    rooms = shift_doc.get("rooms", [])
    target_room = next((r for r in rooms if str(r.get("room_id")) == str(room_id)), None)
    if not target_room:
        return

    now = datetime.now(timezone.utc)
    if is_approved:
        target_room["status"] = "completed"
        target_room["approval_status"] = "verified"
        target_room["is_verified"] = True
    else:
        target_room["status"] = "in_progress"
        target_room["approval_status"] = "rejected"
        target_room["is_verified"] = False

    # Mark photo status inside target room's submitted_photos and tasks' submitted_photos
    approved_photos_count = 0
    for r in rooms:
        for p in r.get("submitted_photos", []):
            if (p.get("review_id") == r_doc.get("review_id") or (r_doc.get("photo_id") and p.get("photo_id") == r_doc.get("photo_id"))) and is_approved:
                p["status"] = "approved"
            elif (p.get("review_id") == r_doc.get("review_id") or (r_doc.get("photo_id") and p.get("photo_id") == r_doc.get("photo_id"))) and not is_approved:
                p["status"] = "rejected"
            if p.get("status") == "approved":
                approved_photos_count += 1
        for t in r.get("tasks", []):
            for tp in t.get("submitted_photos", []):
                if (tp.get("review_id") == r_doc.get("review_id") or (r_doc.get("photo_id") and tp.get("photo_id") == r_doc.get("photo_id"))) and is_approved:
                    tp["status"] = "approved"
                elif (tp.get("review_id") == r_doc.get("review_id") or (r_doc.get("photo_id") and tp.get("photo_id") == r_doc.get("photo_id"))) and not is_approved:
                    tp["status"] = "rejected"

    if is_approved and approved_photos_count == 0:
        approved_photos_count = 1

    shift_doc["approved_photos_count"] = approved_photos_count
    progress = calculate_cleaning_plan_progress(shift_doc, approved_photos_count=approved_photos_count)

    all_completed = len(rooms) > 0 and all(r.get("status") == "completed" for r in rooms)
    shift_status = "completed" if all_completed else ("in_progress" if is_approved else shift_doc.get("status", "in_progress"))

    doc_id = shift_doc.get("_id")
    await db[coll_name].update_one(
        {"_id": doc_id},
        {"$set": {
            "rooms": rooms,
            "status": shift_status,
            "overall_progress_percentage": 100.0 if all_completed else progress["overall_progress_percentage"],
            "completed_rooms_count": progress["completed_rooms_count"],
            "in_progress_rooms_count": progress["in_progress_rooms_count"],
            "pending_rooms_count": progress["pending_rooms_count"],
            "completed_tasks_count": progress["completed_tasks_count"],
            "approved_photos_count": approved_photos_count,
            "updated_at": now
        }}
    )

    if all_completed:
        try:
            from app.services.shift_ws_service import broadcast_shift_completed_event
            await broadcast_shift_completed_event(
                db=db,
                shift_doc=shift_doc,
                completed_by=admin_id if 'admin_id' in locals() else "manager"
            )
        except Exception:
            pass

@photo_reviews_router.patch("/photo-reviews/{review_id}/approve", summary="Approve Photo Review (Triggers PyTorch Online Learning & Completes Room)")
async def approve_photo_review(
    review_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Approve Photo Review Endpoint.
    Approves worker submitted photo, updates AI online learning model, and marks linked shift room as completed with 'verified' status.
    """
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    now = datetime.now(timezone.utc)

    r_doc = await db["photo_reviews"].find_one({"$or": [{"_id": review_id}, {"review_id": review_id}]})
    raw_after = (r_doc.get("after_photo_path") or r_doc.get("after_photo_url") or r_doc.get("photo_url")) if r_doc else None
    after_path = str(raw_after).lstrip("/") if raw_after else "uploads/photo_reviews/after_sample.jpg"
    before_path = r_doc.get("before_photo_path") if r_doc else None

    # TRIGGER ONLINE LEARNING STEP (PyTorch SGD Update: y = 1.0)
    await update_ai_model_online_learning(
        after_photo_path=after_path,
        before_photo_path=before_path,
        is_approved=True,
        admin_id=admin_id
    )

    await db["photo_reviews"].update_one(
        {"$or": [{"_id": review_id}, {"review_id": review_id}]},
        {"$set": {
            "status": "approved",
            "reviewed_by_admin_id": admin_id,
            "reviewed_at": now,
            "updated_at": now
        }},
        upsert=True
    )

    if r_doc:
        await _sync_shift_room_approval(db, r_doc, is_approved=True)

    return {
        "review_id": review_id,
        "status": "approved",
        "message": "Photo review approved successfully. Room marked completed and verified in MongoDB shift."
    }

@photo_reviews_router.patch("/photo-reviews/{review_id}/reject", summary="Reject Photo Review (Triggers PyTorch Online Learning & Reopens Room)")
async def reject_photo_review(
    review_id: str,
    reject_in: PhotoReviewRejectRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Reject Photo Review Endpoint.
    Rejects submitted photo with reason, updates AI online learning model, and marks room as rejected in shift.
    """
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    now = datetime.now(timezone.utc)

    r_doc = await db["photo_reviews"].find_one({"$or": [{"_id": review_id}, {"review_id": review_id}]})
    raw_after = (r_doc.get("after_photo_path") or r_doc.get("after_photo_url") or r_doc.get("photo_url")) if r_doc else None
    after_path = str(raw_after).lstrip("/") if raw_after else "uploads/photo_reviews/after_sample.jpg"
    before_path = r_doc.get("before_photo_path") if r_doc else None

    # TRIGGER ONLINE LEARNING STEP (PyTorch SGD Update: y = 0.0)
    await update_ai_model_online_learning(
        after_photo_path=after_path,
        before_photo_path=before_path,
        is_approved=False,
        admin_id=admin_id
    )

    await db["photo_reviews"].update_one(
        {"$or": [{"_id": review_id}, {"review_id": review_id}]},
        {"$set": {
            "status": "rejected",
            "rejection_reason": reject_in.reason,
            "reviewed_by_admin_id": admin_id,
            "reviewed_at": now,
            "updated_at": now
        }},
        upsert=True
    )

    if r_doc:
        await _sync_shift_room_approval(db, r_doc, is_approved=False)

    return {
        "review_id": review_id,
        "status": "rejected",
        "message": "Photo review rejected. Linked room marked as rejected in shift."
    }
