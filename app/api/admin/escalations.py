import uuid
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.escalation import (
    EscalationPaginatedResponse, EscalationItem, EscalationDrawerResponse,
    EscalationReporterDetail, EscalationAssigneeDetail, EscalationStatusUpdate
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

escalations_router = APIRouter(prefix="/manager", tags=["Manager Escalation Management"])

@escalations_router.get("/escalations", response_model=EscalationPaginatedResponse, summary="Admin Escalations Grid (Image 1 Mockup)")
async def get_admin_escalations(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = None,  # all, open, in_progress, resolved, closed
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if status_filter and status_filter.lower() != "all":
        st_val = status_filter.lower().replace(" ", "_")
        query["status"] = st_val

    if search:
        query["$or"] = [
            {"escalation_id": {"$regex": search, "$options": "i"}},
            {"title": {"$regex": search, "$options": "i"}},
            {"subtitle": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
            {"reporter.name": {"$regex": search, "$options": "i"}}
        ]

    total_count, open_cnt, in_prog_cnt, res_cnt = await asyncio.gather(
        db["escalations"].count_documents(query),
        db["escalations"].count_documents({"status": "open"}),
        db["escalations"].count_documents({"status": "in_progress"}),
        db["escalations"].count_documents({"status": "resolved"})
    )

    skip = (page - 1) * limit
    cursor = db["escalations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_esc = await cursor.to_list(length=limit)

    items = []
    for e in raw_esc:
        eid = str(e.get("_id") or e.get("escalation_id"))
        rep_d = e.get("reporter", {})
        ass_d = e.get("assigned_to")

        c_dt = e.get("created_at")
        if not isinstance(c_dt, datetime):
            c_dt = datetime.now(timezone.utc)

        st = e.get("status", "open")
        st_lbl = st.replace("_", " ").title()

        assignee = EscalationAssigneeDetail(
            admin_id=ass_d.get("admin_id", "a_1"),
            name=ass_d.get("name", "Kaz Putters")
        ) if ass_d else None

        items.append(EscalationItem(
            escalation_id=e.get("escalation_id") or eid,
            shift_id=e.get("shift_id"),
            title=e.get("title", "Issue Reported"),
            subtitle=e.get("subtitle", "Location - Room"),
            description=e.get("description", ""),
            severity=e.get("severity", "high"),
            reporter=EscalationReporterDetail(
                worker_id=rep_d.get("worker_id", "w_1"),
                name=rep_d.get("name", "Lisa Visser"),
                profile_picture=rep_d.get("profile_picture")
            ),
            assigned_to=assignee,
            status=st,
            status_label=st_lbl,
            photo_url=e.get("photo_url"),
            created_at=c_dt
        ))

    return EscalationPaginatedResponse(
        total_count=total_count,
        open_count=open_cnt,
        in_progress_count=in_prog_cnt,
        resolved_count=res_cnt,
        page=page,
        limit=limit,
        escalations=items
    )

@escalations_router.get("/escalations/{escalation_id}", response_model=EscalationDrawerResponse, summary="Get Escalation Drawer Details (Image 2 Mockup)")
async def get_escalation_drawer_details(
    escalation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    e = await db["escalations"].find_one({"$or": [{"_id": escalation_id}, {"escalation_id": escalation_id}]})
    now = datetime.now(timezone.utc)

    if not e:
        raise HTTPException(status_code=404, detail="Escalation not found")

    rep_d = e.get("reporter", {})
    ass_d = e.get("assigned_to")
    c_dt = e.get("created_at") if isinstance(e.get("created_at"), datetime) else now

    assignee = EscalationAssigneeDetail(
        admin_id=str(ass_d.get("admin_id") or ass_d.get("id") or ""),
        name=ass_d.get("name") or ass_d.get("full_name") or "Admin"
    ) if ass_d else None

    loc_name = e.get("location_name") or e.get("location", {}).get("name") or "Location"
    room_name = e.get("room_name") or e.get("room", {}).get("name") or ""
    sub_title = e.get("subtitle") or (f"{loc_name} - {room_name}" if room_name else loc_name)

    return EscalationDrawerResponse(
        escalation_id=e.get("escalation_id", escalation_id),
        shift_id=e.get("shift_id"),
        title=e.get("title") or "Escalation Incident",
        subtitle=sub_title,
        description=e.get("description", ""),
        severity=e.get("severity", "medium"),
        reporter=EscalationReporterDetail(
            worker_id=str(rep_d.get("worker_id") or rep_d.get("id") or ""),
            name=rep_d.get("name") or rep_d.get("full_name") or "Worker",
            profile_picture=rep_d.get("profile_picture")
        ),
        assigned_to=assignee,
        status=e.get("status", "open"),
        photo_url=e.get("photo_url"),
        created_at=c_dt,
        notes=e.get("notes")
    )

@escalations_router.patch("/escalations/{escalation_id}/status", summary="Update Escalation Status (Resolve / In Progress / Open / Closed)")
async def update_escalation_status(
    escalation_id: str,
    status_in: EscalationStatusUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    now = datetime.now(timezone.utc)

    st_val = status_in.status.lower()

    await db["escalations"].update_one(
        {"$or": [{"_id": escalation_id}, {"escalation_id": escalation_id}]},
        {"$set": {
            "status": st_val,
            "notes": status_in.notes,
            "updated_by_admin_id": admin_id,
            "updated_at": now
        }},
        upsert=True
    )

    return {
        "escalation_id": escalation_id,
        "status": st_val,
        "notes": status_in.notes,
        "message": f"Escalation status updated to '{st_val}' successfully."
    }
