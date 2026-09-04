import uuid
from datetime import datetime, timezone
from typing import List, Optional
from app.schemas.extra_services import (
    ExtraServiceListItem, ExtraServiceResponse, ExtraServiceWorkerDetail, ExtraServiceTaskItem,
    ExtraServicePhotoRequirement, ClientInfo, LocationInfo, RoomInfo
)
from app.schemas.client_list import TaskPhotoResponse

def _extract_extra_service_base(doc: dict):
    doc_id = str(doc.get("_id") or doc.get("id"))
    
    # Format dates
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    # Calculate tasks and photos counts
    raw_tasks = doc.get("tasks", [])
    total_tasks_count = len(raw_tasks)
    total_photos_count = 0

    for t in raw_tasks:
        if isinstance(t, dict):
            raw_photos = t.get("photo") or t.get("photos") or []
            if isinstance(raw_photos, list):
                total_photos_count += len(raw_photos)
        elif isinstance(t, str):
            pass

    raw_req_photos = doc.get("required_photos", [])
    if isinstance(raw_req_photos, list):
        total_photos_count += len(raw_req_photos)

    # Format assigned workers
    raw_workers = doc.get("assigned_workers", [])
    workers_res = []
    for w in raw_workers:
        if isinstance(w, dict):
            wid = str(w.get("worker_id") or w.get("id"))
            w_name = w.get("name") or w.get("full_name") or "Worker"
            w_pic = w.get("profile_photo") or w.get("profile_picture")
            w_pos = w.get("position", "normal")
            workers_res.append(ExtraServiceWorkerDetail(
                worker_id=wid,
                name=w_name,
                email=w.get("email"),
                role=w.get("role", "worker"),
                worker_type=w.get("worker_type", "employee"),
                position=w_pos,
                phone=w.get("phone"),
                profile_photo=w_pic,
                profile_picture=w_pic
            ))

    c_id = doc.get("client_id") or "client_default"
    c_name = doc.get("client_name") or "Client"
    client_info = ClientInfo(id=str(c_id), name=str(c_name))

    loc_id = doc.get("location_id") or "loc_default"
    loc_name = doc.get("location_name") or "Main Location"
    loc_info = LocationInfo(id=str(loc_id), name=str(loc_name))

    r_id = doc.get("room_id") or "room_default"
    r_name = doc.get("room_name") or doc.get("title") or "Room"
    room_info = RoomInfo(id=str(r_id), name=str(r_name))

    prio = str(doc.get("priority", "Medium Priority"))
    if "high" in prio.lower():
        prio = "High Priority"
    elif "low" in prio.lower():
        prio = "Low Priority"
    elif "medium" in prio.lower():
        prio = "Medium Priority"

    date_sub = doc.get("date_submitted")
    if not date_sub:
        date_sub = c_at.strftime("%b %d, %Y")

    base_data = {
        "id": doc_id,
        "title": doc.get("title", "Extra Service Request"),
        "preferred_date": doc.get("preferred_date", c_at.strftime("%Y-%m-%d")),
        "priority": prio,
        "description": doc.get("description", ""),
        "status": doc.get("status", "under_review"),
        "client_id": str(c_id),
        "client_name": str(c_name),
        "location_id": str(loc_id),
        "location_name": str(loc_name),
        "room_id": str(r_id),
        "room_name": str(r_name),
        "client": client_info,
        "location": loc_info,
        "room": room_info,
        "date_submitted": date_sub,
        "rejection_reason": doc.get("rejection_reason"),
        "start_time": doc.get("start_time"),
        "duration_minutes": doc.get("duration_minutes"),
        "duration": doc.get("duration"),
        "end_time": doc.get("end_time"),
        "assigned_workers": workers_res,
        "total_tasks_count": total_tasks_count,
        "total_photos_count": total_photos_count,
        "estimated_hours": float(doc.get("estimated_hours", 0.0) or 0.0),
        "actual_start_time": doc.get("actual_start_time"),
        "actual_finish_time": doc.get("actual_finish_time"),
        "hours_credited": doc.get("hours_credited"),
        "created_at": c_at,
        "updated_at": u_at
    }
    return base_data, raw_tasks, raw_req_photos, total_photos_count

def format_extra_service_list_item(doc: dict) -> ExtraServiceListItem:
    """
    Formats a raw MongoDB document into a lightweight ExtraServiceListItem (Short View for list endpoints).
    Omits heavy nested tasks/photos arrays while providing exact counts and summary attributes.
    """
    base_data, _, _, _ = _extract_extra_service_base(doc)
    return ExtraServiceListItem(**base_data)

def format_extra_service_response(doc: dict) -> ExtraServiceResponse:
    """
    Formats a raw MongoDB document into an ExtraServiceResponse model (Full View for single item details).
    Guarantees full hierarchical tasks with photo requirements, worker details, and status.
    """
    base_data, raw_tasks, raw_req_photos, total_photos_count = _extract_extra_service_base(doc)

    tasks_res = []
    for t in raw_tasks:
        if isinstance(t, str):
            tasks_res.append(ExtraServiceTaskItem(
                id=f"t_{uuid.uuid4().hex[:6]}",
                name=t,
                frequency_type="every_visit",
                is_photo_req=False,
                photo=[],
                total_photos_required=0,
                is_completed=False,
                completed_at=None
            ))
        elif isinstance(t, dict):
            t_id = str(t.get("id") or t.get("_id") or f"t_{uuid.uuid4().hex[:6]}")
            t_name = t.get("name", "Task")
            t_freq = t.get("frequency_type", "every_visit")
            is_req = bool(t.get("is_photo_req", False))

            raw_photos = t.get("photo") or t.get("photos") or []
            task_photos = []
            if isinstance(raw_photos, list):
                for p in raw_photos:
                    if isinstance(p, dict):
                        p_id = str(p.get("id") or p.get("_id") or uuid.uuid4().hex[:8])
                        task_photos.append(TaskPhotoResponse(id=p_id, name=p.get("name", "Photo")))
                    elif isinstance(p, str):
                        task_photos.append(TaskPhotoResponse(id=uuid.uuid4().hex[:8], name=p))
                    elif hasattr(p, "name"):
                        p_id = str(getattr(p, "id", None) or uuid.uuid4().hex[:8])
                        task_photos.append(TaskPhotoResponse(id=p_id, name=getattr(p, "name", "Photo")))

            if task_photos and not is_req:
                is_req = True

            tasks_res.append(ExtraServiceTaskItem(
                id=t_id,
                name=t_name,
                frequency_type=t_freq,
                is_photo_req=is_req,
                photo=task_photos,
                total_photos_required=len(task_photos),
                is_completed=bool(t.get("is_completed", False)),
                completed_at=t.get("completed_at")
            ))

    photos_res = []
    for p in raw_req_photos:
        if isinstance(p, str):
            photos_res.append(ExtraServicePhotoRequirement(id=f"p_{uuid.uuid4().hex[:6]}", name=p, is_uploaded=False))
        elif isinstance(p, dict):
            photos_res.append(ExtraServicePhotoRequirement(**p))

    base_data["tasks"] = tasks_res
    base_data["required_photos"] = photos_res
    base_data["total_tasks_count"] = len(tasks_res)
    base_data["total_photos_count"] = total_photos_count

    return ExtraServiceResponse(**base_data)
