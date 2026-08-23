from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.models.user import RoleEnum

async def _get_shift_recipient_uids(db, shift_doc: dict) -> List[str]:
    """
    Collects all user IDs that should receive live shift update broadcasts:
    - The specific Client user(s)
    - All active Managers and Admins
    - All Assigned Workers
    """
    recipient_uids = set()

    # 1. Add Client user ID
    client_id_raw = str(shift_doc.get("client_id") or "")
    if client_id_raw:
        recipient_uids.add(client_id_raw)
        # Also resolve client user by email if client_id is a client_list record
        c_query = {"$or": [{"_id": ObjectId(client_id_raw)}, {"id": client_id_raw}]} if ObjectId.is_valid(client_id_raw) else {"$or": [{"_id": client_id_raw}, {"id": client_id_raw}]}
        c_doc = await db["client_list"].find_one(c_query)
        if c_doc and c_doc.get("email"):
            u_doc = await db["users"].find_one({"email": c_doc["email"]})
            if u_doc:
                recipient_uids.add(str(u_doc.get("_id") or u_doc.get("id")))

    # 2. Add all Assigned Workers
    for w in shift_doc.get("assigned_workers", []) or shift_doc.get("workers", []):
        if isinstance(w, dict):
            wid = str(w.get("worker_id") or w.get("id") or "")
            if wid:
                recipient_uids.add(wid)
    for wid in shift_doc.get("worker_ids", []):
        if str(wid).strip():
            recipient_uids.add(str(wid).strip())

    # 3. Add all active Managers and Admins
    cursor = db["users"].find(
        {"role": {"$in": ["manager", "admin", RoleEnum.manager, RoleEnum.admin]}, "is_active": True},
        {"_id": 1, "id": 1}
    )
    admin_users = await cursor.to_list(length=100)
    for adm in admin_users:
        adm_id = str(adm.get("_id") or adm.get("id"))
        recipient_uids.add(adm_id)

    return list(recipient_uids)


async def broadcast_shift_event(db, shift_doc: dict, event_payload: dict):
    """Broadcasts a real-time WebSocket event for shift updates."""
    try:
        from app.api.chat import ws_manager
        recipient_uids = await _get_shift_recipient_uids(db, shift_doc)
        if recipient_uids:
            await ws_manager.broadcast_to_users(event_payload, recipient_uids)
    except Exception as e:
        print(f"Error broadcasting shift WebSocket event: {e}")


async def broadcast_shift_attendance_event(
    db,
    shift_doc: dict,
    worker_id: str,
    action: str,  # "check_in" | "check_out"
    state: str,   # "checked_in" | "completed"
    checkin_time: Optional[datetime] = None,
    checkout_time: Optional[datetime] = None,
    hours_worked: Optional[float] = None,
    status_label: Optional[str] = None
):
    """Broadcasts worker check-in / check-out attendance changes in real time."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "attendance_changed",
        "shift_id": shift_id,
        "worker_id": worker_id,
        "action": action,
        "state": state,
        "checkin_time": checkin_time.isoformat() if isinstance(checkin_time, datetime) else str(checkin_time) if checkin_time else None,
        "checkout_time": checkout_time.isoformat() if isinstance(checkout_time, datetime) else str(checkout_time) if checkout_time else None,
        "hours_worked": hours_worked,
        "status": status_label or ("ontime" if action == "check_in" else "completed"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)


async def broadcast_room_status_event(
    db,
    shift_doc: dict,
    room_id: str,
    room_name: str,
    status_val: str,
    started_at: Optional[datetime] = None
):
    """Broadcasts room cleaning start / status changes in real time."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "room_status_changed",
        "shift_id": shift_id,
        "room_id": room_id,
        "room_name": room_name,
        "status": status_val,
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)


async def broadcast_task_toggled_event(
    db,
    shift_doc: dict,
    room_id: str,
    task_id: str,
    is_completed: bool,
    overall_progress_percentage: float,
    completed_tasks_count: int,
    total_tasks_count: int
):
    """Broadcasts checklist task toggling with recalculated progress in real time."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "task_toggled",
        "shift_id": shift_id,
        "room_id": room_id,
        "task_id": task_id,
        "is_completed": is_completed,
        "overall_progress_percentage": overall_progress_percentage,
        "completed_tasks_count": completed_tasks_count,
        "total_tasks_count": total_tasks_count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)


async def broadcast_photo_submitted_event(
    db,
    shift_doc: dict,
    room_id: str,
    review_id: str,
    photo_url: str,
    photo_type: str,
    ai_score: int,
    status_val: str = "pending_review"
):
    """Broadcasts photo upload with AI analysis result in real time."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "photo_submitted",
        "shift_id": shift_id,
        "room_id": room_id,
        "review_id": review_id,
        "photo_url": photo_url,
        "photo_type": photo_type,
        "ai_score": ai_score,
        "status": status_val,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)


async def broadcast_room_completed_event(
    db,
    shift_doc: dict,
    room_id: str,
    room_name: str,
    review_id: str,
    overall_progress_percentage: float
):
    """Broadcasts room completion proof submission for Manager review."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "room_proof_uploaded",
        "shift_id": shift_id,
        "room_id": room_id,
        "room_name": room_name,
        "review_id": review_id,
        "status": "photo_submitted",
        "approval_status": "pending",
        "overall_progress_percentage": overall_progress_percentage,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)


async def broadcast_shift_completed_event(
    db,
    shift_doc: dict
):
    """Broadcasts entire shift completion in real time."""
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    payload = {
        "type": "shift_completed",
        "shift_id": shift_id,
        "status": "completed",
        "overall_progress_percentage": 100.0,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await broadcast_shift_event(db, shift_doc, payload)
