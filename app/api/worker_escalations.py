import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from app.core.database import get_database
from app.schemas.escalation import (
    WorkerEscalationCreate, WorkerEscalationResponse, EscalationItem, EscalationReporterDetail
)
from app.models.user import UserInDB
from app.api.worker import require_worker
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/worker", tags=["Worker Escalations"])

@router.post("/escalations", response_model=WorkerEscalationResponse, status_code=status.HTTP_201_CREATED, summary="Create Worker Escalation Report")
async def create_worker_escalation(
    esc_in: WorkerEscalationCreate,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    esc_id = f"esc_{uuid.uuid4().hex[:10]}"

    # Fetch shift and location info if shift_id provided
    loc_name = "Assigned Location"
    room_name = None
    shift_doc = None

    if esc_in.shift_id:
        shift_doc = await db["shifts"].find_one({"$or": [{"_id": esc_in.shift_id}, {"id": esc_in.shift_id}]})
        if not shift_doc:
            shift_doc = await db["shift_executions"].find_one({"$or": [{"_id": esc_in.shift_id}, {"id": esc_in.shift_id}, {"plan_id": esc_in.shift_id}]})
        if not shift_doc:
            shift_doc = await db["cleaning_plans"].find_one({"$or": [{"_id": esc_in.shift_id}, {"id": esc_in.shift_id}]})

    if shift_doc:
        loc_name = shift_doc.get("location_name") or shift_doc.get("location") or "Assigned Location"
        if esc_in.room_id:
            for r in shift_doc.get("rooms", []):
                if str(r.get("id") or r.get("room_id")) == str(esc_in.room_id):
                    room_name = r.get("name") or r.get("room_name")
                    break

    photos = esc_in.photo_urls or []
    if esc_in.photo_url and esc_in.photo_url not in photos:
        photos.append(esc_in.photo_url)
    primary_photo = photos[0] if photos else esc_in.photo_url

    doc = {
        "_id": esc_id,
        "id": esc_id,
        "escalation_id": esc_id,
        "shift_id": esc_in.shift_id,
        "room_id": esc_in.room_id,
        "title": esc_in.title,
        "category": esc_in.category or "maintenance",
        "severity": esc_in.severity,
        "description": esc_in.description,
        "location_name": loc_name,
        "room_name": room_name,
        "photo_url": primary_photo,
        "photo_urls": photos,
        "status": "open",
        "reporter": {
            "worker_id": str(current_user.id),
            "name": current_user.full_name,
            "profile_picture": getattr(current_user, "profile_photo", None)
        },
        "created_at": now,
        "updated_at": now
    }

    await db["escalations"].insert_one(doc)

    # Notify managers about new escalation
    notif_service = NotificationService()
    await notif_service.create_notification(
        title=f"New Escalation: {esc_in.title}",
        message=f"{current_user.full_name} reported an issue: {esc_in.description[:100]}",
        notification_type="escalation",
        recipient_type="manager"
    )

    return WorkerEscalationResponse(
        id=esc_id,
        escalation_id=esc_id,
        shift_id=esc_in.shift_id,
        room_id=esc_in.room_id,
        title=esc_in.title,
        category=esc_in.category or "maintenance",
        severity=esc_in.severity,
        description=esc_in.description,
        status="open",
        photo_url=primary_photo,
        photo_urls=photos,
        created_at=now,
        message="Escalation report submitted successfully"
    )

@router.post("/shifts/{shift_id}/escalations", response_model=WorkerEscalationResponse, status_code=status.HTTP_201_CREATED, summary="Create Escalation within Specific Shift")
async def create_shift_escalation(
    shift_id: str,
    esc_in: WorkerEscalationCreate,
    current_user: UserInDB = Depends(require_worker)
):
    esc_in.shift_id = shift_id
    return await create_worker_escalation(esc_in, current_user)

@router.get("/escalations", summary="List Worker's Escalations")
async def get_worker_escalations(
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    raw = await db["escalations"].find({
        "$or": [
            {"reporter.worker_id": worker_id},
            {"reporter_id": worker_id},
            {"worker_id": worker_id}
        ]
    }).sort("created_at", -1).to_list(length=100)
    for r in raw:
        r["id"] = str(r.get("_id") or r.get("id"))
    return {"total_count": len(raw), "escalations": raw}


@router.get("/escalations/{escalation_id}", summary="Get Single Escalation Details")
async def get_worker_escalation_detail(
    escalation_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    esc = await db["escalations"].find_one({
        "$or": [{"_id": escalation_id}, {"id": escalation_id}, {"escalation_id": escalation_id}]
    })
    if not esc:
        raise HTTPException(status_code=404, detail="Escalation not found")
    esc["id"] = str(esc.get("_id") or esc.get("id"))
    return esc
