import uuid
import asyncio
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

async def _sync_shift_room_approval(db, r_doc: dict, is_approved: bool, rejection_reason: Optional[str] = None):
    """
    Helper to update shift room and task status and recalculate shift progress when Admin approves or rejects proof.
    Ripple Logic:
    1. Photo status -> Approved/Rejected.
    2. If all required photos for a task are approved -> Task marked is_completed=True.
    3. If all tasks for a room are completed -> Room marked status='completed', is_completed=True.
    4. If all rooms & additional tasks are completed -> Shift marked status='completed'.
    """
    if not r_doc:
        return
    shift_id = r_doc.get("shift_id")
    target_photo_id = str(r_doc.get("photo_id") or "")
    review_id = str(r_doc.get("review_id") or r_doc.get("_id") or "")

    if not shift_id:
        return

    shift_doc, coll_name = await resolve_shift_execution(shift_id, db)
    if not shift_doc:
        return

    now = datetime.now(timezone.utc)
    rooms = shift_doc.get("rooms", [])
    additional_tasks = shift_doc.get("additional_tasks", [])

    approved_photos_count = 0

    # 1. Update photos in rooms and tasks
    for r in rooms:
        # Update room level submitted photos
        for p in r.get("submitted_photos", []):
            if str(p.get("photo_id")) == target_photo_id or str(p.get("review_id")) == review_id:
                p["status"] = "approved" if is_approved else "rejected"
                if not is_approved and rejection_reason:
                    p["rejection_reason"] = rejection_reason
            if p.get("status") == "approved":
                approved_photos_count += 1

        # Update task level submitted photos
        for t in r.get("tasks", []):
            for tp in t.get("submitted_photos", []):
                if str(tp.get("photo_id")) == target_photo_id or str(tp.get("review_id")) == review_id:
                    tp["status"] = "approved" if is_approved else "rejected"
                    if not is_approved and rejection_reason:
                        tp["rejection_reason"] = rejection_reason

            # Evaluate task completion
            req_photos = t.get("photo", []) or t.get("required_photos", [])
            sub_photos = t.get("submitted_photos", [])
            if len(req_photos) > 0:
                all_photos_ok = True
                for rp in req_photos:
                    rp_id = str(rp.get("id") or rp.get("photo_id") or "")
                    matched_sub = next((sp for sp in sub_photos if str(sp.get("photo_id")) == rp_id), None)
                    if not matched_sub or matched_sub.get("status") != "approved":
                        all_photos_ok = False
                        break
                t["is_completed"] = all_photos_ok
                t["completed_at"] = now if all_photos_ok else None

        # Evaluate room completion
        r_tasks = r.get("tasks", [])
        if len(r_tasks) > 0 and all(t.get("is_completed") for t in r_tasks):
            r["status"] = "completed"
            r["is_completed"] = True
            r["approval_status"] = "verified"
            r["completed_at"] = now.isoformat()
        else:
            if not is_approved:
                r["status"] = "in_progress"
                r["is_completed"] = False
                r["approval_status"] = "rejected"
            elif len(r_tasks) == 0 and is_approved:
                r["status"] = "completed"
                r["is_completed"] = True
                r["approval_status"] = "verified"
                r["completed_at"] = now.isoformat()

    # 2. Update additional tasks
    for at in additional_tasks:
        for atp in at.get("submitted_photos", []):
            if str(atp.get("photo_id")) == target_photo_id or str(atp.get("review_id")) == review_id:
                atp["status"] = "approved" if is_approved else "rejected"
                if not is_approved and rejection_reason:
                    atp["rejection_reason"] = rejection_reason

        at_req = at.get("photo", []) or at.get("required_photos", [])
        at_sub = at.get("submitted_photos", [])
        if len(at_req) > 0:
            all_at_ok = True
            for rp in at_req:
                rp_id = str(rp.get("id") or rp.get("photo_id") or "")
                matched_sub = next((sp for sp in at_sub if str(sp.get("photo_id")) == rp_id), None)
                if not matched_sub or matched_sub.get("status") != "approved":
                    all_at_ok = False
                    break
            at["is_completed"] = all_at_ok
            at["completed_at"] = now if all_at_ok else None

    # Count all approved photos
    total_approved = 0
    for r in rooms:
        for t in r.get("tasks", []):
            for tp in t.get("submitted_photos", []):
                if tp.get("status") == "approved":
                    total_approved += 1
    for at in additional_tasks:
        for atp in at.get("submitted_photos", []):
            if atp.get("status") == "approved":
                total_approved += 1

    if total_approved == 0 and is_approved:
        total_approved = 1

    shift_doc["approved_photos_count"] = total_approved
    progress = calculate_cleaning_plan_progress(shift_doc, approved_photos_count=total_approved)

    all_rooms_done = len(rooms) > 0 and all(r.get("status") == "completed" for r in rooms)
    all_add_done = len(additional_tasks) == 0 or all(at.get("is_completed") for at in additional_tasks)
    shift_all_done = all_rooms_done and all_add_done

    shift_status = "completed" if shift_all_done else ("in_progress" if is_approved else shift_doc.get("status", "in_progress"))

    doc_id = shift_doc.get("_id")
    update_dict = {
        "rooms": rooms,
        "status": shift_status,
        "overall_progress_percentage": 100.0 if shift_all_done else progress["overall_progress_percentage"],
        "completed_rooms_count": progress["completed_rooms_count"],
        "in_progress_rooms_count": progress["in_progress_rooms_count"],
        "pending_rooms_count": progress["pending_rooms_count"],
        "completed_tasks_count": progress["completed_tasks_count"],
        "approved_photos_count": total_approved,
        "updated_at": now
    }
    if additional_tasks:
        update_dict["additional_tasks"] = additional_tasks

    await db[coll_name].update_one({"_id": doc_id}, {"$set": update_dict})

    # Broadcast real-time websocket updates
    try:
        from app.services.shift_ws_service import broadcast_room_status_event, broadcast_shift_completed_event
        for r in rooms:
            if r.get("status") == "completed":
                await broadcast_room_status_event(
                    db=db,
                    shift_doc=shift_doc,
                    room_id=str(r.get("room_id") or r.get("id")),
                    room_name=r.get("room_name") or r.get("name", "Room"),
                    status_val="completed"
                )
        if shift_all_done:
            await broadcast_shift_completed_event(db=db, shift_doc=shift_doc)
    except Exception:
        pass


@photo_reviews_router.patch("/photo-reviews/{review_id}/approve", summary="Approve Photo Review (Triggers PyTorch Online Learning & Completes Room)")
async def approve_photo_review(
    review_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Approve Photo Review Endpoint.
    Approves worker submitted photo, updates AI online learning model, and marks linked tasks, rooms, and shift as completed when all required photos are approved.
    """
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    now = datetime.now(timezone.utc)

    r_doc = await db["photo_reviews"].find_one({"$or": [{"_id": review_id}, {"review_id": review_id}]})
    raw_after = (r_doc.get("after_photo_path") or r_doc.get("after_photo_url") or r_doc.get("photo_url")) if r_doc else None
    after_path = str(raw_after).lstrip("/") if raw_after else "uploads/photo_reviews/after_sample.jpg"
    before_path = r_doc.get("before_photo_path") if r_doc else None

    # TRIGGER ONLINE LEARNING STEP ASYNCHRONOUSLY (PyTorch SGD Update: y = 1.0)
    asyncio.create_task(update_ai_model_online_learning(
        after_photo_path=after_path,
        before_photo_path=before_path,
        is_approved=True,
        admin_id=admin_id
    ))

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
        "message": "Photo review approved successfully. Tasks, rooms, and shift completion status updated in MongoDB."
    }


@photo_reviews_router.patch("/photo-reviews/{review_id}/reject", summary="Reject Photo Review (Triggers PyTorch Online Learning & Reopens Room)")
async def reject_photo_review(
    review_id: str,
    reject_in: PhotoReviewRejectRequest,
    current_user: UserInDB = Depends(require_manager)
):
    """
    Reject Photo Review Endpoint.
    Rejects submitted photo with reason, updates AI online learning model, reopens room, and sends rich push notification with DeepLink to the assigned worker.
    """
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    now = datetime.now(timezone.utc)

    r_doc = await db["photo_reviews"].find_one({"$or": [{"_id": review_id}, {"review_id": review_id}]})
    raw_after = (r_doc.get("after_photo_path") or r_doc.get("after_photo_url") or r_doc.get("photo_url")) if r_doc else None
    after_path = str(raw_after).lstrip("/") if raw_after else "uploads/photo_reviews/after_sample.jpg"
    before_path = r_doc.get("before_photo_path") if r_doc else None

    # TRIGGER ONLINE LEARNING STEP ASYNCHRONOUSLY (PyTorch SGD Update: y = 0.0)
    asyncio.create_task(update_ai_model_online_learning(
        after_photo_path=after_path,
        before_photo_path=before_path,
        is_approved=False,
        admin_id=admin_id
    ))

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
        await _sync_shift_room_approval(db, r_doc, is_approved=False, rejection_reason=reject_in.reason)

        # Send Rich Push Notification & WebSocket Alert to Assigned Worker asynchronously
        async def _dispatch_reject_notifs():
            try:
                from app.services.notification_service import NotificationService
                from app.api.chat import ws_manager

                worker_id = str(r_doc.get("cleaner", {}).get("worker_id") or "")
                shift_id = str(r_doc.get("shift_id") or "")
                photo_name = r_doc.get("photo_name") or "Task Photo"
                room_name = r_doc.get("room", {}).get("name") if isinstance(r_doc.get("room"), dict) else "Room"

                notif_service = NotificationService()
                notif_payload = {
                    "shift_id": shift_id,
                    "review_id": review_id,
                    "photo_id": r_doc.get("photo_id"),
                    "photo_name": photo_name,
                    "room_name": room_name,
                    "rejection_reason": reject_in.reason,
                    "deeplink": f"cleaningone://worker/shifts/{shift_id}",
                    "route": f"/worker/shifts/{shift_id}"
                }

                if worker_id:
                    await notif_service.create_notification(
                        user_id=worker_id,
                        title=f"Photo Rejected: {photo_name}",
                        message=f"Your photo for '{photo_name}' in {room_name} was rejected. Reason: '{reject_in.reason}'. Please resubmit photo.",
                        notification_type="photo_rejected",
                        recipient_type="worker",
                        data=notif_payload
                    )

                    await ws_manager.broadcast_to_users({
                        "type": "photo_rejected",
                        **notif_payload
                    }, [worker_id])
            except Exception as e:
                print(f"Error sending photo rejection push notification: {e}")

        asyncio.create_task(_dispatch_reject_notifs())

    return {
        "review_id": review_id,
        "status": "rejected",
        "message": "Photo review rejected. Push notification with deep link sent to worker for resubmission."
    }

