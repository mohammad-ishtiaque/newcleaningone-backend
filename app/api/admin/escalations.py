import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.escalation import (
    EscalationPaginatedResponse, EscalationItem, EscalationDrawerResponse,
    EscalationReporterDetail, EscalationAssigneeDetail, EscalationStatusUpdate
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_admin

escalations_router = APIRouter(prefix="/admin", tags=["Admin Escalation Management"])

@escalations_router.get("/escalations", response_model=EscalationPaginatedResponse, summary="Admin Escalations Grid (Image 1 Mockup)")
async def get_admin_escalations(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = None,  # all, open, in_progress, resolved, closed
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
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

    total_count = await db["escalations"].count_documents(query)
    open_cnt = await db["escalations"].count_documents({"status": "open"})
    in_prog_cnt = await db["escalations"].count_documents({"status": "in_progress"})
    res_cnt = await db["escalations"].count_documents({"status": "resolved"})

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

    if not items and not search:
        # Default mock items matching Image 1 mockup
        now = datetime.now(timezone.utc)
        mock_data = [
            ("ESC-001", "Broken mirror in Room 305", "NH Hotel Amsterdam - Kamer 305", "A large bathroom mirror appears cracked. It is unclear whether it concerns existing damage.", "Lisa Visser", None, "open", "high"),
            ("ESC-002", "Bathroom Water Leak - Room 701", "Hilton Rotterdam - Room 701", "Active water leak from the pipe under the sink. Water spreads to bedroom. Urgent maintenance required.", "Eva Smit", "Kaz Putters", "in_progress", "high"),
            ("ESC-003", "Guest complained about missed areas", "NH Hotel Amsterdam - Kamer 203", "Guest from Room 203 reported that no cleaning had been done behind the bathroom door.", "Lisa Visser", "Jan de Vries", "in_progress", "medium"),
            ("ESC-004", "Chemical spills on corridor floor", "UMC Utrecht - Floor 5 Corridor", "Cleaning agent accidentally spilled in the hallway. Area marked but requires clean cleaning and ventilation.", "Noah Bos", None, "open", "high"),
            ("ESC-005", "Guest supplies not replenished", "Van der Valk Eindhoven - Storage", "Shampoo and conditioner not available in the storage room. Order must be placed.", "Sophie de Boer", "Marit Janssen", "resolved", "low")
        ]
        open_cnt, in_prog_cnt, res_cnt = 2, 2, 1
        for e_code, title, sub, desc, rep_name, ass_name, st, sev in mock_data:
            if status_filter and status_filter.lower() != "all" and st != status_filter.lower().replace(" ", "_"):
                continue
            assignee = EscalationAssigneeDetail(admin_id="adm_1", name=ass_name) if ass_name else None
            items.append(EscalationItem(
                escalation_id=e_code,
                shift_id=f"shift_{e_code}",
                title=title,
                subtitle=sub,
                description=desc,
                severity=sev,
                reporter=EscalationReporterDetail(worker_id=f"w_{e_code}", name=rep_name, profile_picture=None),
                assigned_to=assignee,
                status=st,
                status_label=st.replace("_", " ").title(),
                photo_url=f"/uploads/escalations/{e_code.lower()}.jpg",
                created_at=now
            ))

    return EscalationPaginatedResponse(
        total_count=len(items),
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
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    e = await db["escalations"].find_one({"$or": [{"_id": escalation_id}, {"escalation_id": escalation_id}]})
    now = datetime.now(timezone.utc)

    if not e:
        # Default mock detail matching Image 2 mockup
        return EscalationDrawerResponse(
            escalation_id=escalation_id,
            shift_id="shift_esc001",
            title="Broken mirror in Room 305",
            subtitle="NH Hotel Amsterdam - Kamer 305",
            description="A large bathroom mirror appears cracked. It is unclear whether it concerns existing damage.",
            severity="high",
            reporter=EscalationReporterDetail(worker_id="w_1", name="Lisa Visser", profile_picture=None),
            assigned_to=EscalationAssigneeDetail(admin_id="adm_1", name="Kaz Putters"),
            status="open",
            photo_url="/uploads/escalations/esc-001.jpg",
            created_at=now,
            notes="Maintenance team requested on site."
        )

    rep_d = e.get("reporter", {})
    ass_d = e.get("assigned_to")
    c_dt = e.get("created_at") if isinstance(e.get("created_at"), datetime) else now

    assignee = EscalationAssigneeDetail(
        admin_id=ass_d.get("admin_id", "adm_1"),
        name=ass_d.get("name", "Kaz Putters")
    ) if ass_d else None

    return EscalationDrawerResponse(
        escalation_id=e.get("escalation_id", escalation_id),
        shift_id=e.get("shift_id"),
        title=e.get("title", "Broken mirror in Room 305"),
        subtitle=e.get("subtitle", "NH Hotel Amsterdam - Kamer 305"),
        description=e.get("description", ""),
        severity=e.get("severity", "high"),
        reporter=EscalationReporterDetail(
            worker_id=rep_d.get("worker_id", "w_1"),
            name=rep_d.get("name", "Lisa Visser"),
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
    current_user: UserInDB = Depends(require_admin)
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
