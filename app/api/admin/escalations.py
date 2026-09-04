import uuid
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import List, Optional
from app.core.database import get_database
from app.schemas.escalation import (
    EscalationPaginatedResponse, EscalationItem, EscalationDrawerResponse,
    EscalationReporterDetail, EscalationAssigneeDetail, EscalationStatusUpdate
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.services.notification_service import NotificationService

escalations_router = APIRouter(prefix="/manager", tags=["Manager Escalation Management"])

@escalations_router.get(
    "/escalations",
    response_model=EscalationPaginatedResponse,
    summary="Admin Escalations Grid (Image 1 Mockup)",
    description=(
        "Returns a paginated list of all worker-reported escalations for the Manager/Admin dashboard. "
        "Supports real-time search across ID, title, subtitle, description, worker name, and location. "
        "Provides platform-wide status counters for 'open', 'in_progress', 'resolved', and 'closed' filter tabs."
    )
)
async def get_admin_escalations(
    page: int = Query(1, ge=1, description="Page number (1-indexed). Example: 1"),
    limit: int = Query(10, ge=1, le=100, description="Items per page. Example: 10"),
    status_filter: Optional[str] = Query(
        None,
        description="Filter by escalation status. Supported values: 'all', 'open', 'in_progress', 'resolved', 'closed'. Example: 'open'"
    ),
    search: Optional[str] = Query(
        None,
        description="Search term matching escalation_id, title, subtitle, description, reporter name, or location. Example: 'door lock'"
    ),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if status_filter and status_filter.lower().strip() not in ("all", ""):
        st_val = status_filter.lower().strip().replace(" ", "_")
        query["status"] = st_val

    if search and search.strip():
        s = search.strip()
        query["$or"] = [
            {"escalation_id": {"$regex": s, "$options": "i"}},
            {"title": {"$regex": s, "$options": "i"}},
            {"subtitle": {"$regex": s, "$options": "i"}},
            {"description": {"$regex": s, "$options": "i"}},
            {"reporter.name": {"$regex": s, "$options": "i"}},
            {"location_name": {"$regex": s, "$options": "i"}},
            {"room_name": {"$regex": s, "$options": "i"}}
        ]

    total_count, open_cnt, in_prog_cnt, res_cnt, closed_cnt = await asyncio.gather(
        db["escalations"].count_documents(query),
        db["escalations"].count_documents({"status": "open"}),
        db["escalations"].count_documents({"status": "in_progress"}),
        db["escalations"].count_documents({"status": "resolved"}),
        db["escalations"].count_documents({"status": "closed"})
    )

    skip = (page - 1) * limit
    cursor = db["escalations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_esc = await cursor.to_list(length=limit)

    # Collect worker IDs that need user profile lookup if name is missing
    missing_worker_uids = []
    for e in raw_esc:
        rep = e.get("reporter", {})
        w_id = str(rep.get("worker_id") or e.get("reporter_id") or e.get("worker_id") or "")
        if w_id and not rep.get("name"):
            missing_worker_uids.append(w_id)

    worker_lookup = {}
    if missing_worker_uids:
        u_query = {"$or": []}
        for uid in set(missing_worker_uids):
            u_query["$or"].append({"id": uid})
            u_query["$or"].append({"_id": uid})
            if ObjectId.is_valid(uid):
                u_query["$or"].append({"_id": ObjectId(uid)})
        if u_query["$or"]:
            async for u in db["users"].find(u_query):
                worker_lookup[str(u["_id"])] = u

    items = []
    for e in raw_esc:
        eid = str(e.get("escalation_id") or e.get("_id") or e.get("id"))
        rep_d = e.get("reporter") or {}
        ass_d = e.get("assigned_to") or e.get("resolved_by")

        # Worker reporter resolution without mock defaults
        rep_uid = str(rep_d.get("worker_id") or e.get("reporter_id") or e.get("worker_id") or "")
        rep_name = rep_d.get("name") or rep_d.get("full_name")
        rep_avatar = rep_d.get("profile_picture") or rep_d.get("profile_photo")
        rep_phone = rep_d.get("phone")

        if not rep_name and rep_uid in worker_lookup:
            w_u = worker_lookup[rep_uid]
            rep_name = w_u.get("full_name") or "Worker"
            rep_avatar = rep_avatar or w_u.get("profile_photo")
            rep_phone = rep_phone or w_u.get("phone")
        if not rep_name:
            rep_name = f"Worker {rep_uid[:6]}" if rep_uid else "Field Worker"

        c_dt = e.get("created_at")
        if not isinstance(c_dt, datetime):
            c_dt = datetime.now(timezone.utc)

        st = (e.get("status") or "open").lower().strip()
        st_lbl = st.replace("_", " ").title()

        # Assignee resolution
        assignee = None
        if ass_d and (ass_d.get("admin_id") or ass_d.get("id") or ass_d.get("name")):
            assignee = EscalationAssigneeDetail(
                admin_id=str(ass_d.get("admin_id") or ass_d.get("id") or ""),
                name=ass_d.get("name") or ass_d.get("full_name") or "Assigned Manager",
                email=ass_d.get("email")
            )

        # Dynamic location and room subtitle
        loc_name = e.get("location_name") or e.get("location") or ""
        room_name = e.get("room_name") or e.get("room") or ""
        sub_title = e.get("subtitle") or (
            f"{loc_name} - {room_name}" if loc_name and room_name else (loc_name or room_name or "General Incident")
        )

        # Photo resolution
        photo = e.get("photo_url")
        if not photo and e.get("photo_urls"):
            photo = e.get("photo_urls")[0]

        items.append(EscalationItem(
            escalation_id=eid,
            shift_id=e.get("shift_id"),
            title=e.get("title") or "Issue Reported",
            subtitle=sub_title,
            description=e.get("description") or "",
            category=e.get("category") or "maintenance",
            severity=e.get("severity") if e.get("severity") in ("high", "medium", "low") else "high",
            reporter=EscalationReporterDetail(
                worker_id=rep_uid,
                name=rep_name,
                profile_picture=rep_avatar,
                phone=rep_phone,
                email=rep_d.get("email")
            ),
            assigned_to=assignee,
            status=st if st in ("open", "in_progress", "resolved", "closed") else "open",
            status_label=st_lbl,
            photo_url=photo,
            created_at=c_dt
        ))

    return EscalationPaginatedResponse(
        total_count=total_count,
        open_count=open_cnt,
        in_progress_count=in_prog_cnt,
        resolved_count=res_cnt,
        closed_count=closed_cnt,
        page=page,
        limit=limit,
        escalations=items
    )

@escalations_router.get(
    "/escalations/{escalation_id}",
    response_model=EscalationDrawerResponse,
    summary="Get Escalation Drawer Details (Image 2 Mockup)",
    description=(
        "Retrieves complete details of an escalation for the side drawer inspection view. "
        "Includes full reporter profile info, shift metadata, location/room names, full photo gallery, "
        "and manager resolution history/notes."
    )
)
async def get_escalation_drawer_details(
    escalation_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": escalation_id}, {"escalation_id": escalation_id}, {"id": escalation_id}]}
    if ObjectId.is_valid(escalation_id):
        query["$or"].append({"_id": ObjectId(escalation_id)})

    e = await db["escalations"].find_one(query)
    if not e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Escalation with ID '{escalation_id}' not found."
        )

    rep_d = e.get("reporter") or {}
    ass_d = e.get("assigned_to") or e.get("resolved_by")
    c_dt = e.get("created_at") if isinstance(e.get("created_at"), datetime) else datetime.now(timezone.utc)

    # Worker info resolution
    rep_uid = str(rep_d.get("worker_id") or e.get("reporter_id") or e.get("worker_id") or "")
    rep_name = rep_d.get("name") or rep_d.get("full_name")
    rep_avatar = rep_d.get("profile_picture") or rep_d.get("profile_photo")
    rep_phone = rep_d.get("phone")
    rep_email = rep_d.get("email")

    if rep_uid and (not rep_name or not rep_phone or not rep_avatar):
        u_q = {"$or": [{"_id": rep_uid}, {"id": rep_uid}]}
        if ObjectId.is_valid(rep_uid):
            u_q["$or"].append({"_id": ObjectId(rep_uid)})
        worker_u = await db["users"].find_one(u_q)
        if worker_u:
            rep_name = rep_name or worker_u.get("full_name")
            rep_avatar = rep_avatar or worker_u.get("profile_photo")
            rep_phone = rep_phone or worker_u.get("phone")
            rep_email = rep_email or worker_u.get("email")

    assignee = None
    if ass_d and (ass_d.get("admin_id") or ass_d.get("id") or ass_d.get("name")):
        assignee = EscalationAssigneeDetail(
            admin_id=str(ass_d.get("admin_id") or ass_d.get("id") or ""),
            name=ass_d.get("name") or ass_d.get("full_name") or "Assigned Manager",
            email=ass_d.get("email")
        )

    loc_name = e.get("location_name") or (e.get("location", {}).get("name") if isinstance(e.get("location"), dict) else e.get("location")) or None
    room_name = e.get("room_name") or (e.get("room", {}).get("name") if isinstance(e.get("room"), dict) else e.get("room")) or None
    sub_title = e.get("subtitle") or (
        f"{loc_name} - {room_name}" if loc_name and room_name else (loc_name or room_name or "General Incident")
    )

    photos = e.get("photo_urls") or []
    if e.get("photo_url") and e.get("photo_url") not in photos:
        photos.append(e.get("photo_url"))
    primary_photo = e.get("photo_url") or (photos[0] if photos else None)

    st = (e.get("status") or "open").lower().strip()
    st_lbl = st.replace("_", " ").title()

    return EscalationDrawerResponse(
        escalation_id=e.get("escalation_id") or str(e.get("_id")),
        shift_id=e.get("shift_id"),
        title=e.get("title") or "Escalation Incident",
        subtitle=sub_title,
        description=e.get("description") or "",
        category=e.get("category") or "maintenance",
        severity=e.get("severity") or "high",
        reporter=EscalationReporterDetail(
            worker_id=rep_uid,
            name=rep_name or "Field Worker",
            profile_picture=rep_avatar,
            phone=rep_phone,
            email=rep_email
        ),
        assigned_to=assignee,
        status=st,
        status_label=st_lbl,
        photo_url=primary_photo,
        photo_urls=photos,
        location_name=loc_name,
        room_name=room_name,
        client_name=e.get("client_name"),
        created_at=c_dt,
        updated_at=e.get("updated_at"),
        resolved_at=e.get("resolved_at"),
        notes=e.get("notes")
    )

@escalations_router.patch(
    "/escalations/{escalation_id}/status",
    summary="Update Escalation Status (Resolve / In Progress / Open / Closed)",
    description=(
        "Updates the status and resolution notes of an escalation. "
        "Supported status values: 'open', 'in_progress', 'resolved', 'closed'. "
        "When marked as 'resolved', records resolution timestamps and automatically dispatches "
        "a push notification, database notification, and WebSocket alert to the reporter worker."
    )
)
async def update_escalation_status(
    escalation_id: str,
    status_in: EscalationStatusUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": escalation_id}, {"escalation_id": escalation_id}, {"id": escalation_id}]}
    if ObjectId.is_valid(escalation_id):
        query["$or"].append({"_id": ObjectId(escalation_id)})

    e = await db["escalations"].find_one(query)
    if not e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Escalation with ID '{escalation_id}' not found."
        )

    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")
    manager_name = getattr(current_user, "full_name", "Manager")
    manager_email = getattr(current_user, "email", None)
    now = datetime.now(timezone.utc)
    st_val = status_in.status.lower().strip()

    update_fields = {
        "status": st_val,
        "notes": status_in.notes,
        "updated_by_admin_id": admin_id,
        "updated_at": now
    }

    if st_val == "resolved":
        update_fields["resolved_at"] = now
        update_fields["resolved_by"] = {
            "admin_id": admin_id,
            "name": manager_name,
            "email": manager_email
        }
        update_fields["assigned_to"] = {
            "admin_id": admin_id,
            "name": manager_name,
            "email": manager_email
        }
    elif st_val == "in_progress" and not e.get("assigned_to"):
        update_fields["assigned_to"] = {
            "admin_id": admin_id,
            "name": manager_name,
            "email": manager_email
        }

    await db["escalations"].update_one(
        {"_id": e["_id"]},
        {"$set": update_fields}
    )

    # Dispatches Push Notification, DB Notification, and WebSocket alert to the reporter worker
    notif_service = NotificationService()
    await notif_service.notify_escalation_status_changed(
        escalation=e,
        new_status=st_val,
        manager_user=current_user,
        notes=status_in.notes
    )

    return {
        "escalation_id": e.get("escalation_id") or escalation_id,
        "status": st_val,
        "notes": status_in.notes,
        "resolved_at": update_fields.get("resolved_at"),
        "updated_at": now,
        "message": f"Escalation status updated to '{st_val}' successfully and notification dispatched to reporter."
    }
