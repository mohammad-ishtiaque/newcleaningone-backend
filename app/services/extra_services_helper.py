import uuid
from datetime import datetime, timezone
from app.schemas.extra_services import (
    ExtraServiceResponse, ExtraServiceWorkerDetail, ExtraServiceTaskItem,
    ExtraServicePhotoRequirement, ClientInfo, LocationInfo, RoomInfo
)

def format_extra_service_response(doc: dict) -> ExtraServiceResponse:
    """
    Formats a raw MongoDB document from the extra_services collection into an ExtraServiceResponse model.
    """
    doc_id = str(doc.get("_id") or doc.get("id"))
    doc["id"] = doc_id

    # Format dates
    if "created_at" not in doc or not isinstance(doc["created_at"], datetime):
        doc["created_at"] = datetime.now(timezone.utc)
    if "updated_at" not in doc or not isinstance(doc["updated_at"], datetime):
        doc["updated_at"] = datetime.now(timezone.utc)

    # Format tasks list
    raw_tasks = doc.get("tasks", [])
    tasks_res = []
    for t in raw_tasks:
        if isinstance(t, str):
            tasks_res.append(ExtraServiceTaskItem(id=f"t_{uuid.uuid4().hex[:6]}", name=t, is_completed=False))
        elif isinstance(t, dict):
            tasks_res.append(ExtraServiceTaskItem(**t))

    # Format photos list
    raw_photos = doc.get("required_photos", [])
    photos_res = []
    for p in raw_photos:
        if isinstance(p, str):
            photos_res.append(ExtraServicePhotoRequirement(id=f"p_{uuid.uuid4().hex[:6]}", name=p, is_uploaded=False))
        elif isinstance(p, dict):
            photos_res.append(ExtraServicePhotoRequirement(**p))

    # Format assigned workers
    raw_workers = doc.get("assigned_workers", [])
    workers_res = []
    for w in raw_workers:
        if isinstance(w, dict):
            workers_res.append(ExtraServiceWorkerDetail(
                worker_id=str(w.get("worker_id") or w.get("id")),
                name=w.get("name", "Worker"),
                profile_picture=w.get("profile_picture")
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
    doc["required_photos"] = photos_res
    doc["assigned_workers"] = workers_res

    return ExtraServiceResponse(**doc)
