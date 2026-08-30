from datetime import datetime
from typing import List, Optional
from app.schemas.shift import (
    ShiftChecklistResponse, ChecklistRoomItem, ChecklistTaskItem, ChecklistPhotoItem
)

def build_shift_checklist_response(shift_doc: dict) -> ShiftChecklistResponse:
    """
    Constructs a detailed ShiftChecklistResponse model from MongoDB cleaning_plans / shift_executions document.
    Categorizes room tasks, task required photos, submission statuses, and additional tasks.
    """
    shift_id = str(shift_doc.get("id") or shift_doc.get("_id"))
    title = shift_doc.get("title") or shift_doc.get("name") or "Cleaning Shift"
    service_kind = shift_doc.get("service_kind", "cleaning_plan")
    date_val = str(shift_doc.get("date") or shift_doc.get("preferred_date") or "")
    start_time = shift_doc.get("start_time", "08:00 AM")
    end_time = shift_doc.get("end_time", "10:30 AM")
    shift_status = shift_doc.get("status", "scheduled")

    rooms_res: List[ChecklistRoomItem] = []
    total_tasks = 0
    completed_tasks = 0
    total_photos = 0
    approved_photos = 0
    pending_photos = 0
    rejected_photos = 0

    rooms = shift_doc.get("rooms", [])
    for r_idx, r in enumerate(rooms):
        r_id = str(r.get("room_id") or r.get("id") or f"room_{r_idx}")
        r_name = r.get("room_name") or r.get("name") or f"Room {r_idx+1}"
        r_status = r.get("status", "pending")
        r_is_completed = bool(r.get("is_completed", False) or r_status == "completed")
        r_completed_at = r.get("completed_at")
        if isinstance(r_completed_at, str):
            try:
                r_completed_at = datetime.fromisoformat(r_completed_at)
            except Exception:
                r_completed_at = None

        tasks_res: List[ChecklistTaskItem] = []
        for t in r.get("tasks", []):
            total_tasks += 1
            t_id = str(t.get("id") or t.get("task_id") or "")
            t_name = t.get("name") or t.get("task_name") or "Task"
            t_freq = t.get("frequency_type", "every_visit")
            t_is_photo = bool(t.get("is_photo_req", False))
            t_is_completed = bool(t.get("is_completed", False))
            if t_is_completed:
                completed_tasks += 1
            t_completed_at = t.get("completed_at")
            if isinstance(t_completed_at, str):
                try:
                    t_completed_at = datetime.fromisoformat(t_completed_at)
                except Exception:
                    t_completed_at = None

            req_photos = t.get("photo", []) or t.get("required_photos", [])
            photos_res: List[ChecklistPhotoItem] = []
            submitted_map = {}
            for sp in (t.get("submitted_photos", []) or []):
                p_key = str(sp.get("photo_id") or "")
                if p_key:
                    submitted_map[p_key] = sp

            for p in req_photos:
                total_photos += 1
                p_id = str(p.get("id") or p.get("photo_id") or "")
                p_name = p.get("name") or p.get("photo_name") or "Required Photo"

                sub_p = submitted_map.get(p_id)
                if sub_p:
                    p_url = sub_p.get("photo_url") or sub_p.get("after_photo_url")
                    p_st = sub_p.get("status", "pending_review")
                    p_rej = sub_p.get("rejection_reason")
                    p_time = sub_p.get("submitted_at")
                    if isinstance(p_time, str):
                        try:
                            p_time = datetime.fromisoformat(p_time)
                        except Exception:
                            p_time = None
                    if p_st == "approved":
                        approved_photos += 1
                    elif p_st == "rejected":
                        rejected_photos += 1
                    else:
                        pending_photos += 1
                else:
                    p_url = None
                    p_st = "not_uploaded"
                    p_rej = None
                    p_time = None

                photos_res.append(ChecklistPhotoItem(
                    id=p_id,
                    name=p_name,
                    photo_url=p_url,
                    status=p_st,
                    rejection_reason=p_rej,
                    submitted_at=p_time
                ))

            tasks_res.append(ChecklistTaskItem(
                id=t_id,
                name=t_name,
                frequency_type=t_freq,
                is_photo_req=t_is_photo or len(photos_res) > 0,
                is_completed=t_is_completed,
                completed_at=t_completed_at,
                required_photos=photos_res
            ))

        rooms_res.append(ChecklistRoomItem(
            room_id=r_id,
            room_name=r_name,
            room_type=r.get("room_type", "standard"),
            floor=r.get("floor", 1),
            duration=r.get("duration", 30),
            status=r_status,
            is_completed=r_is_completed,
            completed_at=r_completed_at,
            tasks=tasks_res
        ))

    add_tasks_res: List[ChecklistTaskItem] = []
    for at in shift_doc.get("additional_tasks", []):
        total_tasks += 1
        at_id = str(at.get("id") or "")
        at_name = at.get("name") or "Additional Task"
        at_freq = at.get("frequency_type", "every_visit")
        at_is_completed = bool(at.get("is_completed", False))
        if at_is_completed:
            completed_tasks += 1
        at_completed_at = at.get("completed_at")
        if isinstance(at_completed_at, str):
            try:
                at_completed_at = datetime.fromisoformat(at_completed_at)
            except Exception:
                at_completed_at = None

        at_req_photos = at.get("photo", []) or at.get("required_photos", [])
        at_photos_res: List[ChecklistPhotoItem] = []
        at_sub_map = {}
        for sp in (at.get("submitted_photos", []) or []):
            p_key = str(sp.get("photo_id") or "")
            if p_key:
                at_sub_map[p_key] = sp

        for p in at_req_photos:
            total_photos += 1
            p_id = str(p.get("id") or "")
            p_name = p.get("name") or "Required Photo"
            sub_p = at_sub_map.get(p_id)
            if sub_p:
                p_url = sub_p.get("photo_url")
                p_st = sub_p.get("status", "pending_review")
                p_rej = sub_p.get("rejection_reason")
                p_time = sub_p.get("submitted_at")
                if isinstance(p_time, str):
                    try:
                        p_time = datetime.fromisoformat(p_time)
                    except Exception:
                        p_time = None
                if p_st == "approved":
                    approved_photos += 1
                elif p_st == "rejected":
                    rejected_photos += 1
                else:
                    pending_photos += 1
            else:
                p_url = None
                p_st = "not_uploaded"
                p_rej = None
                p_time = None

            at_photos_res.append(ChecklistPhotoItem(
                id=p_id,
                name=p_name,
                photo_url=p_url,
                status=p_st,
                rejection_reason=p_rej,
                submitted_at=p_time
            ))

        add_tasks_res.append(ChecklistTaskItem(
            id=at_id,
            name=at_name,
            frequency_type=at_freq,
            is_photo_req=len(at_photos_res) > 0,
            is_completed=at_is_completed,
            completed_at=at_completed_at,
            required_photos=at_photos_res
        ))

    return ShiftChecklistResponse(
        shift_id=shift_id,
        title=title,
        service_kind=service_kind,
        date=date_val,
        start_time=start_time,
        end_time=end_time,
        status=shift_status,
        rooms=rooms_res,
        additional_tasks=add_tasks_res,
        total_tasks_count=total_tasks,
        completed_tasks_count=completed_tasks,
        total_photos_count=total_photos,
        approved_photos_count=approved_photos,
        pending_photos_count=pending_photos,
        rejected_photos_count=rejected_photos
    )
