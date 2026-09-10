from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_modules import (
    WorkerAssignmentItem, WorkerAssignmentRoom, WorkerAssignmentLeader,
    WorkerAssignmentsPaginatedResponse
)

router = APIRouter(prefix="/worker/assignments", tags=["Worker Assignments Management"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


async def _resolve_leaders(assigned_workers: list, db) -> tuple[Optional[WorkerAssignmentLeader], List[WorkerAssignmentLeader]]:
    leader = None
    co_leaders = []
    if not assigned_workers:
        return None, []

    w_ids = [str(w.get("worker_id") or w.get("id") or "") for w in assigned_workers if isinstance(w, dict)]
    w_map = {}
    if w_ids:
        oid_list = [ObjectId(x) for x in w_ids if ObjectId.is_valid(x)]
        or_clauses = [{"_id": {"$in": w_ids}}, {"id": {"$in": w_ids}}]
        if oid_list:
            or_clauses.append({"_id": {"$in": oid_list}})
        async for u in db["users"].find({"$or": or_clauses}):
            uid_str = str(u.get("_id") or u.get("id"))
            w_map[uid_str] = u
            if "id" in u and u["id"]:
                w_map[str(u["id"])] = u
            if "_id" in u:
                w_map[str(u["_id"])] = u

    for w in assigned_workers:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("worker_id") or w.get("id") or "")
        pos = str(w.get("position", "normal")).lower()
        u_doc = w_map.get(wid, {})
        w_name = u_doc.get("full_name") or u_doc.get("name") or w.get("name") or "Team Leader"
        w_phone = u_doc.get("phone") or u_doc.get("phone_number") or w.get("phone_number") or w.get("phone")
        w_pic = u_doc.get("profile_photo") or u_doc.get("profile_picture") or w.get("profile_photo") or w.get("profile_picture")

        if pos == "teamleader" and not leader:
            leader = WorkerAssignmentLeader(
                worker_id=wid,
                name=w_name,
                position="teamleader",
                phone=w_phone,
                profile_photo=w_pic
            )
        elif pos in ["co_leader", "coleader", "assistant"]:
            co_leaders.append(WorkerAssignmentLeader(
                worker_id=wid,
                name=w_name,
                position="co_leader",
                phone=w_phone,
                profile_photo=w_pic
            ))

    return leader, co_leaders


@router.get(
    "",
    response_model=WorkerAssignmentsPaginatedResponse,
    summary="Get Worker Assignments (Paginated)",
    description="Returns all active recurring cleaning plans and extra service contracts assigned to the logged-in worker with room checklists, team leaders, and schedule details."
)
async def get_worker_assignments(
    service_kind: Optional[str] = Query("all", description="Filter service kind: 'all', 'cleaning_plan', or 'extra_service'"),
    search: Optional[str] = Query(None, description="Search by plan title, client name, or location name"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    assignments = []

    # 1. Fetch recurring cleaning plans assigned to worker
    if service_kind in ["all", "cleaning_plan", None]:
        p_query = {
            "status": {"$ne": "cancelled"},
            "$or": [
                {"worker_ids": worker_id},
                {"assigned_workers.worker_id": worker_id},
                {"workers.worker_id": worker_id}
            ]
        }
        if search and search.strip():
            s_rgx = {"$regex": search.strip(), "$options": "i"}
            p_query["$and"] = [
                {"$or": [
                    {"title": s_rgx},
                    {"plan_name": s_rgx},
                    {"client_name": s_rgx},
                    {"company_name": s_rgx},
                    {"location_name": s_rgx}
                ]}
            ]

        cursor = db["cleaning_plans"].find(p_query).sort("created_at", -1)
        raw_plans = await cursor.to_list(length=100)

        for p in raw_plans:
            pid = str(p.get("id") or p.get("_id"))
            c_name = p.get("client_name") or p.get("company_name") or "Client"
            l_name = p.get("location_name") or "Location"
            l_addr = p.get("location_address") or p.get("address")

            # Determine worker position in this plan
            w_entries = p.get("assigned_workers") or p.get("workers") or []
            w_rec = next((w for w in w_entries if str(w.get("worker_id")) == worker_id), None)
            w_pos = w_rec.get("position", "normal") if w_rec else "normal"

            leader, co_leaders = await _resolve_leaders(w_entries, db)

            rooms_res = []
            for r in p.get("rooms", []):
                r_id = str(r.get("room_id") or r.get("id") or "")
                r_name = r.get("room_name") or r.get("name") or "Room"
                r_tasks = r.get("tasks", [])
                r_photos = r.get("required_photos", [])
                rooms_res.append(WorkerAssignmentRoom(
                    room_id=r_id,
                    room_name=r_name,
                    room_type=r.get("room_type", "standard"),
                    floor=int(r.get("floor", 1)),
                    tasks_count=len(r_tasks),
                    photos_required_count=len(r_photos)
                ))

            c_at = p.get("created_at") if isinstance(p.get("created_at"), datetime) else datetime.now(timezone.utc)

            assignments.append(WorkerAssignmentItem(
                id=pid,
                title=p.get("title") or p.get("plan_name", "Cleaning Plan"),
                service_kind="cleaning_plan",
                client_id=str(p.get("client_id") or ""),
                client_name=c_name,
                location_id=str(p.get("location_id") or ""),
                location_name=l_name,
                location_address=l_addr,
                start_time=str(p.get("start_time", "08:00 AM")),
                end_time=str(p.get("end_time", "04:00 PM")),
                timezone=str(p.get("timezone", "Europe/Amsterdam")),
                repeat_shift=p.get("repeat_shift"),
                working_days=p.get("working_days", []),
                position=w_pos,
                total_rooms_count=len(rooms_res) or 1,
                rooms=rooms_res,
                team_leader=leader,
                co_leaders=co_leaders,
                shift_notes=p.get("shift_notes"),
                status=str(p.get("status", "active")),
                created_at=c_at
            ))

    # 2. Fetch assigned extra services
    if service_kind in ["all", "extra_service", None]:
        es_query = {
            "assigned_workers.worker_id": worker_id,
            "status": {"$nin": ["cancelled", "rejected"]}
        }
        if search and search.strip():
            s_rgx = {"$regex": search.strip(), "$options": "i"}
            es_query["$and"] = [
                {"$or": [
                    {"title": s_rgx},
                    {"client_name": s_rgx},
                    {"location_name": s_rgx}
                ]}
            ]

        cursor_es = db["extra_services"].find(es_query).sort("created_at", -1)
        raw_es = await cursor_es.to_list(length=100)

        for es in raw_es:
            es_id = str(es.get("id") or es.get("_id"))
            w_entries = es.get("assigned_workers", [])
            w_rec = next((w for w in w_entries if str(w.get("worker_id")) == worker_id), None)
            w_pos = w_rec.get("position", "normal") if w_rec else "normal"
            leader, co_leaders = await _resolve_leaders(w_entries, db)

            rooms_res = [
                WorkerAssignmentRoom(
                    room_id=str(es.get("room_id") or "room_es"),
                    room_name=str(es.get("room_name") or "Service Area"),
                    room_type="extra_service",
                    floor=1,
                    tasks_count=len(es.get("tasks", [])),
                    photos_required_count=len(es.get("required_photos", []))
                )
            ]

            c_at = es.get("created_at") if isinstance(es.get("created_at"), datetime) else datetime.now(timezone.utc)

            assignments.append(WorkerAssignmentItem(
                id=es_id,
                title=f"Extra Service: {es.get('title', 'Service')}",
                service_kind="extra_service",
                client_id=str(es.get("client_id") or ""),
                client_name=str(es.get("client_name") or "Client"),
                location_id=str(es.get("location_id") or ""),
                location_name=str(es.get("location_name") or "Location"),
                location_address=es.get("location_address"),
                start_time=str(es.get("start_time", "08:00 AM")),
                end_time=str(es.get("end_time", "10:00 AM")),
                timezone=str(es.get("timezone", "Europe/Amsterdam")),
                repeat_shift=None,
                working_days=[str(es.get("preferred_date", ""))],
                position=w_pos,
                total_rooms_count=1,
                rooms=rooms_res,
                team_leader=leader,
                co_leaders=co_leaders,
                shift_notes=es.get("description"),
                status=str(es.get("status", "approved")),
                created_at=c_at
            ))

    total_count = len(assignments)
    start_idx = (page - 1) * limit
    paged = assignments[start_idx:start_idx + limit]
    has_more = (start_idx + len(paged)) < total_count

    return WorkerAssignmentsPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        assignments=paged
    )


@router.get(
    "/{plan_id}",
    response_model=WorkerAssignmentItem,
    summary="Get Single Assignment Detail",
    description="Returns full detailed view of a single cleaning plan or extra service assignment for the authenticated worker."
)
async def get_worker_assignment_detail(
    plan_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    # Try cleaning plans
    p_query = {"$or": [{"_id": plan_id}, {"id": plan_id}]} if not ObjectId.is_valid(plan_id) else {"$or": [{"_id": ObjectId(plan_id)}, {"id": plan_id}, {"_id": plan_id}]}
    p = await db["cleaning_plans"].find_one(p_query)
    if p:
        pid = str(p.get("id") or p.get("_id"))
        c_name = p.get("client_name") or p.get("company_name") or "Client"
        l_name = p.get("location_name") or "Location"
        l_addr = p.get("location_address") or p.get("address")

        w_entries = p.get("assigned_workers") or p.get("workers") or []
        w_rec = next((w for w in w_entries if str(w.get("worker_id")) == worker_id), None)
        w_pos = w_rec.get("position", "normal") if w_rec else "normal"

        leader, co_leaders = await _resolve_leaders(w_entries, db)

        rooms_res = []
        for r in p.get("rooms", []):
            r_id = str(r.get("room_id") or r.get("id") or "")
            r_name = r.get("room_name") or r.get("name") or "Room"
            r_tasks = r.get("tasks", [])
            r_photos = r.get("required_photos", [])
            rooms_res.append(WorkerAssignmentRoom(
                room_id=r_id,
                room_name=r_name,
                room_type=r.get("room_type", "standard"),
                floor=int(r.get("floor", 1)),
                tasks_count=len(r_tasks),
                photos_required_count=len(r_photos)
            ))

        c_at = p.get("created_at") if isinstance(p.get("created_at"), datetime) else datetime.now(timezone.utc)

        return WorkerAssignmentItem(
            id=pid,
            title=p.get("title") or p.get("plan_name", "Cleaning Plan"),
            service_kind="cleaning_plan",
            client_id=str(p.get("client_id") or ""),
            client_name=c_name,
            location_id=str(p.get("location_id") or ""),
            location_name=l_name,
            location_address=l_addr,
            start_time=str(p.get("start_time", "08:00 AM")),
            end_time=str(p.get("end_time", "04:00 PM")),
            timezone=str(p.get("timezone", "Europe/Amsterdam")),
            repeat_shift=p.get("repeat_shift"),
            working_days=p.get("working_days", []),
            position=w_pos,
            total_rooms_count=len(rooms_res) or 1,
            rooms=rooms_res,
            team_leader=leader,
            co_leaders=co_leaders,
            shift_notes=p.get("shift_notes"),
            status=str(p.get("status", "active")),
            created_at=c_at
        )

    # Try extra services
    es_query = {"$or": [{"_id": plan_id}, {"id": plan_id}]} if not ObjectId.is_valid(plan_id) else {"$or": [{"_id": ObjectId(plan_id)}, {"id": plan_id}, {"_id": plan_id}]}
    es = await db["extra_services"].find_one(es_query)
    if es:
        es_id = str(es.get("id") or es.get("_id"))
        w_entries = es.get("assigned_workers", [])
        w_rec = next((w for w in w_entries if str(w.get("worker_id")) == worker_id), None)
        w_pos = w_rec.get("position", "normal") if w_rec else "normal"
        leader, co_leaders = await _resolve_leaders(w_entries, db)

        rooms_res = [
            WorkerAssignmentRoom(
                room_id=str(es.get("room_id") or "room_es"),
                room_name=str(es.get("room_name") or "Service Area"),
                room_type="extra_service",
                floor=1,
                tasks_count=len(es.get("tasks", [])),
                photos_required_count=len(es.get("required_photos", []))
            )
        ]

        c_at = es.get("created_at") if isinstance(es.get("created_at"), datetime) else datetime.now(timezone.utc)

        return WorkerAssignmentItem(
            id=es_id,
            title=f"Extra Service: {es.get('title', 'Service')}",
            service_kind="extra_service",
            client_id=str(es.get("client_id") or ""),
            client_name=str(es.get("client_name") or "Client"),
            location_id=str(es.get("location_id") or ""),
            location_name=str(es.get("location_name") or "Location"),
            location_address=es.get("location_address"),
            start_time=str(es.get("start_time", "08:00 AM")),
            end_time=str(es.get("end_time", "10:00 AM")),
            timezone=str(es.get("timezone", "Europe/Amsterdam")),
            repeat_shift=None,
            working_days=[str(es.get("preferred_date", ""))],
            position=w_pos,
            total_rooms_count=1,
            rooms=rooms_res,
            team_leader=leader,
            co_leaders=co_leaders,
            shift_notes=es.get("description"),
            status=str(es.get("status", "approved")),
            created_at=c_at
        )

    raise HTTPException(status_code=404, detail="Assignment not found")
