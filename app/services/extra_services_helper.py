import uuid
from datetime import datetime, timezone
from typing import List, Optional
from app.schemas.extra_services import (
    ExtraServiceResponse, ExtraServiceWorkerDetail, ExtraServiceTaskItem,
    ExtraServicePhotoRequirement, ClientInfo, LocationInfo, RoomInfo
)
from app.schemas.client_list import TaskPhotoResponse

def format_extra_service_response(doc: dict) -> ExtraServiceResponse:
    """
    Formats a raw MongoDB document from the extra_services collection into an ExtraServiceResponse model.
    Guarantees hierarchical task-connected photos, accurate task/photo counts, and worker details.
    """
    doc_id = str(doc.get("_id") or doc.get("id"))
    doc["id"] = doc_id

    # Format dates
    if "created_at" not in doc or not isinstance(doc["created_at"], datetime):
        doc["created_at"] = datetime.now(timezone.utc)
    if "updated_at" not in doc or not isinstance(doc["updated_at"], datetime):
        doc["updated_at"] = datetime.now(timezone.utc)

    # Format tasks list with embedded photo requirements
    raw_tasks = doc.get("tasks", [])
    tasks_res = []
    total_photos_count = 0

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

            total_photos_count += len(task_photos)

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

    # Format photos list (legacy / standalone fallback)
    raw_photos = doc.get("required_photos", [])
    photos_res = []
    for p in raw_photos:
        if isinstance(p, str):
            photos_res.append(ExtraServicePhotoRequirement(id=f"p_{uuid.uuid4().hex[:6]}", name=p, is_uploaded=False))
            total_photos_count += 1
        elif isinstance(p, dict):
            photos_res.append(ExtraServicePhotoRequirement(**p))
            total_photos_count += 1

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
    doc["client"] = ClientInfo(id=str(c_id), name=str(c_name))

    loc_id = doc.get("location_id") or "loc_default"
    loc_name = doc.get("location_name") or "Main Location"
    doc["location"] = LocationInfo(id=str(loc_id), name=str(loc_name))

    r_id = doc.get("room_id") or "room_default"
    r_name = doc.get("room_name") or doc.get("title") or "Room"
    doc["room"] = RoomInfo(id=str(r_id), name=str(r_name))

    doc["tasks"] = tasks_res
    doc["total_tasks_count"] = len(tasks_res)
    doc["total_photos_count"] = total_photos_count
    doc["required_photos"] = photos_res
    doc["assigned_workers"] = workers_res

    return ExtraServiceResponse(**doc)
