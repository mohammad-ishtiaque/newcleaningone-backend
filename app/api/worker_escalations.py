import uuid
import base64
import re
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, UploadFile, File, Form, Path
from typing import List, Optional, Union
from app.core.database import get_database
from app.schemas.escalation import (
    WorkerEscalationCreate, WorkerEscalationResponse, WorkerEscalationDetail,
    WorkerEscalationListResponse, EscalationReporterDetail
)
from app.models.user import UserInDB
from app.api.worker import require_worker
from app.services.notification_service import NotificationService
from app.services.s3_service import S3Service
from app.services.escalation_photo_service import (
    WORKER_ESCALATION_OPENAPI_EXTRA,
    WORKER_SHIFT_ESCALATION_OPENAPI_EXTRA,
    process_photos_to_s3
)
from app.api.worker_shift_utils import resolve_shift_execution

router = APIRouter(prefix="/worker", tags=["Worker Escalations"])


@router.post(
    "/escalations",
    response_model=WorkerEscalationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Worker Escalation Report",
    description="""
Enables a worker to submit an incident, blockage, or maintenance report with photo evidence automatically uploaded to Amazon S3.

### Supported Features:
- **Photo Upload to Amazon S3**: Supports direct binary file uploads (`photos`, `photo`, `files`, `file`) and base64 encoded images (`data:image/...;base64,...`). Every photo is automatically resized (1080px responsive width) and uploaded to Amazon S3.
- **Dual Content-Type Support**: Accepts both `multipart/form-data` (for direct file uploads from mobile/camera) and `application/json` (for JSON payloads).
- **Shift & Location Linking**: Automatically resolves shift, client, facility, and room details if `shift_id` is supplied.
- **Manager Alerts**: Dispatches background notifications to active managers.

### Field Notes & Supported Values:
- **`title`**: String (required). Short summary of the incident. Example: `"Broken Keycard / Inaccessible Room"`
- **`description`**: String (required). Comprehensive details of the issue. Example: `"Door handle is detached and room cannot be entered."`
- **`category`**: String (optional, default: `"maintenance"`). Supported values:
  - `"maintenance"`: Physical damage, plumbing, electrical, fixtures.
  - `"safety"`: Hazards, chemical spills, security concerns.
  - `"access"`: Locked doors, invalid keycards, blocked pathways.
  - `"cleaning"`: Deep staining, biohazards, abnormal mess.
  - `"equipment"`: Broken vacuum, missing cleaning cart, faulty supplies.
  - `"client_dispute"`: Client complaints, on-site conflicts.
  - `"other"`: Miscellaneous incidents.
- **`severity`**: String (optional, default: `"high"`). Supported values:
  - `"high"`: Immediate blocker halting shift progress.
  - `"medium"`: Issue requiring attention but cleaning can continue elsewhere.
  - `"low"`: Minor defect or informational report.
- **`shift_id`**: String (optional). Associated shift execution or plan ID.
- **`room_id`**: String (optional). Specific room ID within the shift.
- **`photos` / `photo`**: Image file(s) (optional). Binary files (`.jpg`, `.jpeg`, `.png`, `.webp`).
- **`photo_urls`**: Array of strings (optional). Direct URLs or base64 image strings.
""",
    openapi_extra=WORKER_ESCALATION_OPENAPI_EXTRA
)
async def create_worker_escalation(
    request: Request,
    title: Optional[str] = Form(None, description="Short summary of the issue", example="Broken Keycard / Inaccessible Room"),
    description: Optional[str] = Form(None, description="Detailed problem description", example="Door handle is detached and room cannot be entered."),
    category: Optional[str] = Form("maintenance", description="Incident category: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'", example="maintenance"),
    severity: Optional[str] = Form("high", description="Urgency: 'high', 'medium', 'low'", example="high"),
    shift_id: Optional[str] = Form(None, description="Associated shift execution ID", example="exec_plan_6141aedb01_2026-09-01"),
    room_id: Optional[str] = Form(None, description="Associated room ID within the shift", example="room_01"),
    location_name: Optional[str] = Form(None, description="Facility or site name", example="Grand Hotel Central"),
    room_name: Optional[str] = Form(None, description="Room name or number", example="Room 107"),
    photo_urls: Optional[List[str]] = Form(None, description="Pre-existing photo URLs or base64 strings"),
    photo_url: Optional[str] = Form(None, description="Single photo URL or base64 string"),
    photos: Optional[List[Union[UploadFile, str]]] = File(default=None, description="Photo evidence files to upload to Amazon S3"),
    photo: Optional[Union[UploadFile, str]] = File(default=None, description="Single photo evidence file to upload to Amazon S3"),
    files: Optional[List[Union[UploadFile, str]]] = File(default=None, description="Alternative multiple files upload field"),
    file: Optional[Union[UploadFile, str]] = File(default=None, description="Alternative single file upload field"),
    current_user: UserInDB = Depends(require_worker)
):
    content_type = request.headers.get("content-type", "") if request else ""
    raw_files: List[UploadFile] = []
    raw_photo_strings: List[str] = []

    def _is_file_upload(obj):
        return bool(obj and hasattr(obj, "filename") and obj.filename and hasattr(obj, "read"))

    # Collect files from multipart form parameters (robustly supporting UploadFile and Swagger empty string / string values)
    for f_list in [photos, files]:
        if f_list:
            items = f_list if isinstance(f_list, list) else [f_list]
            for f in items:
                if _is_file_upload(f):
                    raw_files.append(f)
                elif isinstance(f, str) and f.strip():
                    raw_photo_strings.append(f.strip())

    for single_f in [photo, file]:
        if _is_file_upload(single_f):
            raw_files.append(single_f)
        elif isinstance(single_f, str) and single_f.strip():
            raw_photo_strings.append(single_f.strip())

    # Collect string URLs from form
    if photo_urls:
        for u in photo_urls:
            if u and isinstance(u, str) and u.strip():
                raw_photo_strings.append(u.strip())
    if photo_url and isinstance(photo_url, str) and photo_url.strip() and photo_url.strip() not in raw_photo_strings:
        raw_photo_strings.append(photo_url.strip())

    # Seamless application/json payload parsing
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                title = body.get("title") or title
                description = body.get("description") or description
                category = body.get("category") or category
                severity = body.get("severity") or severity
                shift_id = body.get("shift_id") or shift_id
                room_id = body.get("room_id") or room_id
                location_name = body.get("location_name") or location_name
                room_name = body.get("room_name") or room_name

                j_urls = body.get("photo_urls") or body.get("photos") or []
                if isinstance(j_urls, str):
                    j_urls = [j_urls]
                for u in j_urls:
                    if u and str(u) not in raw_photo_strings:
                        raw_photo_strings.append(str(u))

                j_single = body.get("photo_url") or body.get("photo") or body.get("photo_base64")
                if j_single and str(j_single) not in raw_photo_strings:
                    raw_photo_strings.append(str(j_single))
        except Exception:
            pass

    # Validate mandatory fields
    if not title or not str(title).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escalation title ('title') is required."
        )
    if not description or not str(description).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escalation description ('description') is required."
        )

    # Normalize category and severity
    valid_categories = {"maintenance", "safety", "access", "cleaning", "equipment", "client_dispute", "other"}
    cat = (category or "maintenance").lower().strip()
    if cat not in valid_categories:
        cat = "maintenance"

    valid_severities = {"high", "medium", "low"}
    sev = (severity or "high").lower().strip()
    if sev not in valid_severities:
        sev = "high"

    # Normalize empty strings to None
    shift_id = (shift_id or "").strip() or None
    room_id = (room_id or "").strip() or None
    room_name = (room_name or "").strip() or None
    location_name = (location_name or "").strip() or None

    db = get_database()
    now = datetime.now(timezone.utc)
    esc_id = f"esc_{uuid.uuid4().hex[:10]}"

    loc_name = location_name
    rm_name = room_name
    client_name = None
    shift_doc = None

    if shift_id:
        shift_doc, _ = await resolve_shift_execution(shift_id, db)

    if shift_doc:
        loc_name = shift_doc.get("location_name") or shift_doc.get("location") or loc_name or "Assigned Facility"
        client_name = shift_doc.get("client_name") or shift_doc.get("company_name")
        if room_id:
            for r in shift_doc.get("rooms", []):
                if str(r.get("id") or r.get("room_id")) == str(room_id):
                    rm_name = r.get("name") or r.get("room_name") or rm_name
                    break

    loc_name = loc_name or "General Site"
    sub_title = f"{loc_name} - {rm_name}" if rm_name else loc_name

    # Upload all photos to Amazon S3
    s3_service = S3Service()
    final_photo_urls = await process_photos_to_s3(
        files=raw_files,
        raw_strings=raw_photo_strings,
        s3_service=s3_service
    )
    primary_photo = final_photo_urls[0] if final_photo_urls else None

    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "")

    doc = {
        "_id": esc_id,
        "id": esc_id,
        "escalation_id": esc_id,
        "shift_id": shift_id,
        "room_id": room_id,
        "title": title.strip(),
        "subtitle": sub_title,
        "category": cat,
        "severity": sev,
        "description": description.strip(),
        "location_name": loc_name,
        "room_name": rm_name,
        "client_name": client_name,
        "photo_url": primary_photo,
        "photo_urls": final_photo_urls,
        "status": "open",
        "reporter": {
            "worker_id": worker_id,
            "name": getattr(current_user, "full_name", None) or getattr(current_user, "name", "Worker"),
            "profile_picture": getattr(current_user, "profile_photo", None) or getattr(current_user, "profile_picture", None),
            "phone": getattr(current_user, "phone", None),
            "email": getattr(current_user, "email", None)
        },
        "created_at": now,
        "updated_at": now
    }

    await db["escalations"].insert_one(doc)

    # Notify managers about new escalation in background
    notif_service = NotificationService()
    try:
        await notif_service.notify_escalation_created(doc, current_user)
    except Exception as n_err:
        print(f"[Escalation Notify Error] {n_err}")

    return WorkerEscalationResponse(
        id=esc_id,
        escalation_id=esc_id,
        shift_id=shift_id,
        room_id=room_id,
        title=doc["title"],
        category=doc["category"],
        severity=doc["severity"],
        description=doc["description"],
        status="open",
        photo_url=primary_photo,
        photo_urls=final_photo_urls,
        created_at=now,
        message="Escalation report submitted successfully"
    )


@router.post(
    "/shifts/{shift_id}/escalations",
    response_model=WorkerEscalationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Escalation within Specific Shift",
    description="""
Enables a worker to submit an incident directly linked to a specific active shift with photo evidence automatically uploaded to Amazon S3.

### Supported Features:
- **Photo Upload to Amazon S3**: Supports direct binary file uploads (`photos`, `photo`, `files`, `file`) and base64 encoded images (`data:image/...;base64,...`). All photos are automatically uploaded to Amazon S3.
- **Inherited Context**: Automatically inherits client, location, and room details from the specified shift.
- **Dual Content-Type**: Accepts both `multipart/form-data` and `application/json`.
""",
    openapi_extra=WORKER_SHIFT_ESCALATION_OPENAPI_EXTRA
)
async def create_shift_escalation(
    shift_id: str = Path(..., description="Target Shift / Execution ID", example="exec_plan_6141aedb01_2026-09-01"),
    request: Request = None,
    title: Optional[str] = Form(None, description="Short summary of the issue", example="Broken Keycard / Inaccessible Room"),
    description: Optional[str] = Form(None, description="Detailed problem description", example="Door handle is detached and room cannot be entered."),
    category: Optional[str] = Form("maintenance", description="Incident category: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'", example="maintenance"),
    severity: Optional[str] = Form("high", description="Urgency: 'high', 'medium', 'low'", example="high"),
    room_id: Optional[str] = Form(None, description="Associated room ID within the shift", example="room_01"),
    location_name: Optional[str] = Form(None, description="Facility or site name (optional, inherited from shift)", example="Grand Hotel Central"),
    room_name: Optional[str] = Form(None, description="Room name or number (optional, inherited from shift)", example="Room 107"),
    photo_urls: Optional[List[str]] = Form(None, description="Pre-existing photo URLs or base64 strings"),
    photo_url: Optional[str] = Form(None, description="Single photo URL or base64 string"),
    photos: Optional[List[Union[UploadFile, str]]] = File(default=None, description="Photo evidence files to upload to Amazon S3"),
    photo: Optional[Union[UploadFile, str]] = File(default=None, description="Single photo evidence file to upload to Amazon S3"),
    files: Optional[List[Union[UploadFile, str]]] = File(default=None, description="Alternative multiple files upload field"),
    file: Optional[Union[UploadFile, str]] = File(default=None, description="Alternative single file upload field"),
    current_user: UserInDB = Depends(require_worker)
):
    clean_shift_id = (shift_id or "").strip()
    # Validate shift existence for shift-linked escalation
    db = get_database()
    shift_doc, _ = await resolve_shift_execution(clean_shift_id, db)

    if not shift_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift with ID '{clean_shift_id}' not found."
        )

    # Enforce shift_id from path and delegate
    return await create_worker_escalation(
        request=request,
        title=title,
        description=description,
        category=category,
        severity=severity,
        shift_id=clean_shift_id,
        room_id=room_id,
        location_name=location_name,
        room_name=room_name,
        photo_urls=photo_urls,
        photo_url=photo_url,
        photos=photos,
        photo=photo,
        files=files,
        file=file,
        current_user=current_user
    )

@router.get(
    "/escalations",
    response_model=WorkerEscalationListResponse,
    summary="List Worker's Escalations",
    description=(
        "Retrieves a paginated list of all escalations submitted by the logged-in worker. "
        "Supports filtering by status ('all', 'open', 'in_progress', 'resolved', 'closed'). "
        "Returns summary counters for active/resolved items."
    )
)
async def get_worker_escalations(
    page: int = Query(1, ge=1, description="Page number (1-indexed). Example: 1"),
    limit: int = Query(10, ge=1, le=100, description="Items per page. Example: 10"),
    status_filter: Optional[str] = Query(
        None,
        description="Filter by status: 'all', 'open', 'in_progress', 'resolved', 'closed'. Example: 'open'"
    ),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "")

    worker_clause = {
        "$or": [
            {"reporter.worker_id": worker_id},
            {"reporter_id": worker_id},
            {"worker_id": worker_id}
        ]
    }

    query = {"$and": [worker_clause]}
    if status_filter and status_filter.lower().strip() not in ("all", ""):
        st_val = status_filter.lower().strip().replace(" ", "_")
        query["$and"].append({"status": st_val})

    total_count = await db["escalations"].count_documents(query)
    open_count = await db["escalations"].count_documents({"$and": [worker_clause, {"status": "open"}]})
    resolved_count = await db["escalations"].count_documents({"$and": [worker_clause, {"status": "resolved"}]})

    skip = (page - 1) * limit
    cursor = db["escalations"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw = await cursor.to_list(length=limit)

    items = []
    for r in raw:
        eid = str(r.get("escalation_id") or r.get("_id") or r.get("id"))
        st = (r.get("status") or "open").lower().strip()
        st_lbl = st.replace("_", " ").title()

        rep_d = r.get("reporter") or {}
        reporter = EscalationReporterDetail(
            worker_id=str(rep_d.get("worker_id") or worker_id),
            name=rep_d.get("name") or current_user.full_name,
            profile_picture=rep_d.get("profile_picture") or getattr(current_user, "profile_photo", None),
            phone=rep_d.get("phone") or getattr(current_user, "phone", None),
            email=rep_d.get("email") or getattr(current_user, "email", None)
        )

        photos = r.get("photo_urls") or []
        if r.get("photo_url") and r.get("photo_url") not in photos:
            photos.append(r.get("photo_url"))

        c_dt = r.get("created_at") if isinstance(r.get("created_at"), datetime) else datetime.now(timezone.utc)

        items.append(WorkerEscalationDetail(
            id=eid,
            escalation_id=eid,
            shift_id=r.get("shift_id"),
            room_id=r.get("room_id"),
            title=r.get("title") or "Escalation Incident",
            category=r.get("category") or "maintenance",
            severity=r.get("severity") or "high",
            description=r.get("description") or "",
            status=st,
            status_label=st_lbl,
            location_name=r.get("location_name"),
            room_name=r.get("room_name"),
            photo_url=r.get("photo_url") or (photos[0] if photos else None),
            photo_urls=photos,
            reporter=reporter,
            notes=r.get("notes"),
            created_at=c_dt,
            updated_at=r.get("updated_at"),
            resolved_at=r.get("resolved_at")
        ))

    has_more = bool((page * limit) < total_count)

    return WorkerEscalationListResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        open_count=open_count,
        resolved_count=resolved_count,
        escalations=items
    )

@router.get(
    "/escalations/{escalation_id}",
    response_model=WorkerEscalationDetail,
    summary="Get Single Escalation Details",
    description=(
        "Fetches complete details for a specific escalation submitted by the worker. "
        "Enforces strict privacy: workers cannot view escalations submitted by others. "
        "Includes current resolution status, manager response notes, and photo records."
    )
)
async def get_worker_escalation_detail(
    escalation_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "")

    query = {"$or": [{"_id": escalation_id}, {"id": escalation_id}, {"escalation_id": escalation_id}]}
    if ObjectId.is_valid(escalation_id):
        query["$or"].append({"_id": ObjectId(escalation_id)})

    esc = await db["escalations"].find_one(query)
    if not esc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Escalation with ID '{escalation_id}' not found."
        )

    # Security check: Worker can only view their own escalations
    rep = esc.get("reporter") or {}
    owner_id = str(rep.get("worker_id") or esc.get("reporter_id") or esc.get("worker_id") or "")
    if owner_id and owner_id != worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: You are not authorized to view this escalation report."
        )

    eid = str(esc.get("escalation_id") or esc.get("_id") or esc.get("id"))
    st = (esc.get("status") or "open").lower().strip()
    st_lbl = st.replace("_", " ").title()

    photos = esc.get("photo_urls") or []
    if esc.get("photo_url") and esc.get("photo_url") not in photos:
        photos.append(esc.get("photo_url"))

    c_dt = esc.get("created_at") if isinstance(esc.get("created_at"), datetime) else datetime.now(timezone.utc)

    reporter = EscalationReporterDetail(
        worker_id=owner_id or worker_id,
        name=rep.get("name") or current_user.full_name,
        profile_picture=rep.get("profile_picture") or getattr(current_user, "profile_photo", None),
        phone=rep.get("phone") or getattr(current_user, "phone", None),
        email=rep.get("email") or getattr(current_user, "email", None)
    )

    return WorkerEscalationDetail(
        id=eid,
        escalation_id=eid,
        shift_id=esc.get("shift_id"),
        room_id=esc.get("room_id"),
        title=esc.get("title") or "Escalation Incident",
        category=esc.get("category") or "maintenance",
        severity=esc.get("severity") or "high",
        description=esc.get("description") or "",
        status=st,
        status_label=st_lbl,
        location_name=esc.get("location_name"),
        room_name=esc.get("room_name"),
        photo_url=esc.get("photo_url") or (photos[0] if photos else None),
        photo_urls=photos,
        reporter=reporter,
        notes=esc.get("notes"),
        created_at=c_dt,
        updated_at=esc.get("updated_at"),
        resolved_at=esc.get("resolved_at")
    )
