import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.shift import (
    ShiftDraftCreate, ShiftDraftResponse, ShiftDraftUpdate, ShiftDraftPaginatedResponse,
    WorkerDropdownItem, WorkerDropdownPaginatedResponse,
    ShiftAssignRequest, ShiftWorkerDetail, ShiftResponse, ShiftPaginatedResponse, ShiftUpdate,
    PhotoReviewPaginatedResponse, PhotoReviewItem, PhotoReviewRejectRequest
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

shift_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Shift Management"])

def _format_shift_response(doc: dict) -> ShiftResponse:
    s_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    raw_workers = doc.get("workers", [])
    formatted_workers = []
    for w in raw_workers:
        formatted_workers.append(ShiftWorkerDetail(
            worker_id=str(w.get("worker_id")),
            name=w.get("name", "Worker"),
            profile_picture=w.get("profile_picture"),
            worker_type=w.get("worker_type", "employee"),
            shift_role=w.get("shift_role", "cleaning_specialist")
        ))

    return ShiftResponse(
        id=s_id,
        draft_id=doc.get("draft_id"),
        client_id=doc.get("client_id", ""),
        client_name=doc.get("client_name", "Client"),
        location_id=doc.get("location_id", ""),
        location_name=doc.get("location_name", "Location"),
        date=doc.get("date", ""),
        start_time=doc.get("start_time", "08:00"),
        end_time=doc.get("end_time", "16:00"),
        shift_notes=doc.get("shift_notes"),
        cleaning_plan_id=doc.get("cleaning_plan_id"),
        rooms=doc.get("rooms", []),
        total_tasks_count=doc.get("total_tasks_count", 0),
        total_photo_required=doc.get("total_photo_required", 0),
        overall_progress_percentage=doc.get("overall_progress_percentage", 0.0),
        completed_rooms_count=doc.get("completed_rooms_count", 0),
        in_progress_rooms_count=doc.get("in_progress_rooms_count", 0),
        pending_rooms_count=doc.get("pending_rooms_count", 0),
        status=doc.get("status", "published"),
        workers=formatted_workers,
        created_at=c_at,
        updated_at=u_at
    )

