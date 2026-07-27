from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from app.schemas.client_list import (
    CleaningTaskCreate, CleaningTaskResponse,
    RequiredPhotoCreate, RequiredPhotoResponse
)

class ShiftTaskItem(BaseModel):
    id: str
    name: str
    is_completed: bool = False
    completed_at: Optional[datetime] = None

class SubmittedPhotoItem(BaseModel):
    photo_id: str
    photo_name: str
    photo_url: str
    submitted_at: datetime
    review_id: Optional[str] = None
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"

class ShiftRoomInput(BaseModel):
    room_id: str = Field(..., json_schema_extra={"example": "2ac45be9-9443-4378-8286-152ce553a90b"})
    custom_room_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Executive Suite 301"})
    additional_tasks: Optional[List[CleaningTaskCreate]] = Field(
        default=None,
        json_schema_extra={"example": [{"name": "Sanitize executive desk"}]}
    )
    additional_photo_requirements: Optional[List[RequiredPhotoCreate]] = Field(
        default=None,
        json_schema_extra={"example": [{"name": "Desk sanitization photo"}]}
    )
    duration: Optional[int] = Field(default=None, json_schema_extra={"example": 45})
    clean_type: Optional[str] = Field(default=None, json_schema_extra={"example": "standard"})
    tasks: Optional[List[CleaningTaskCreate]] = Field(default=None)
    required_photos: Optional[List[RequiredPhotoCreate]] = Field(default=None)

class ShiftRoomDetail(BaseModel):
    room_id: str
    custom_room_name: Optional[str] = None
    room_name: str
    room_type: Optional[str] = "standard"
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    floor: Optional[int] = 1
    clean_type: str = "standard"
    duration: int = 30
    status: Literal["pending", "in_progress", "photo_submitted", "completed"] = "pending"
    tasks: List[ShiftTaskItem] = Field(default_factory=list)
    required_photos: List[RequiredPhotoResponse] = Field(default_factory=list)
    submitted_photos: List[SubmittedPhotoItem] = Field(default_factory=list)
    completed_tasks_count: int = 0
    task_number: int = 0
    photo_number: int = 0

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftDraftCreate(BaseModel):
    client_id: str = Field(..., json_schema_extra={"example": "6a61b7f68ad7764bf1032f67"})
    location_id: str = Field(..., json_schema_extra={"example": "2a78f050-4410-424f-86dd-d9a442b67816"})
    date: str = Field(..., json_schema_extra={"example": "2026-07-25"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00"})
    end_time: str = Field(..., json_schema_extra={"example": "16:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Deep clean executive floor"})
    cleaning_plan_id: Optional[str] = Field(default=None, json_schema_extra={"example": "cleaning_plan_id_here"})
    room_ids: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["room_id_1", "room_id_2"]})
    rooms: Optional[List[ShiftRoomInput]] = Field(default=None)

class ShiftDraftResponse(BaseModel):
    id: str
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    date: str
    start_time: str
    end_time: str
    shift_notes: Optional[str] = None
    cleaning_plan_id: Optional[str] = None
    rooms: List[ShiftRoomDetail] = Field(default_factory=list)
    total_tasks_count: int = 0
    total_photo_required: int = 0
    status: str = "draft"
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftDraftUpdate(BaseModel):
    client_id: Optional[str] = Field(default=None, json_schema_extra={"example": "6a61b7f68ad7764bf1032f67"})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "2a78f050-4410-424f-86dd-d9a442b67816"})
    date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-07-26"})
    start_time: Optional[str] = Field(default=None, json_schema_extra={"example": "09:00"})
    end_time: Optional[str] = Field(default=None, json_schema_extra={"example": "17:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated draft notes"})
    cleaning_plan_id: Optional[str] = Field(default=None)
    room_ids: Optional[List[str]] = Field(default=None)
    rooms: Optional[List[ShiftRoomInput]] = Field(default=None)

class ShiftDraftPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    drafts: List[ShiftDraftResponse]

class WorkerDropdownItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    status: Literal["available", "on_shift"]
    worker_type: str

class WorkerDropdownPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    workers: List[WorkerDropdownItem]

class WorkerShiftAssignmentItem(BaseModel):
    worker_id: str
    shift_role: Optional[str] = "cleaning_specialist"

class ShiftAssignRequest(BaseModel):
    draft_id: str = Field(..., json_schema_extra={"example": "draft_uuid_12345"})
    worker_ids: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["worker_id_1", "worker_id_2"]})
    worker_assignments: Optional[List[WorkerShiftAssignmentItem]] = Field(
        default=None,
        description="Optional list of worker assignments with shift roles (e.g., leader, co_leader, cleaning_specialist)"
    )

class ShiftWorkerDetail(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    worker_type: str
    shift_role: Optional[str] = "cleaning_specialist"

class ShiftResponse(BaseModel):
    id: str
    draft_id: Optional[str] = None
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    date: str
    start_time: str
    end_time: str
    shift_notes: Optional[str] = None
    cleaning_plan_id: Optional[str] = None
    rooms: List[ShiftRoomDetail] = Field(default_factory=list)
    total_tasks_count: int = 0
    total_photo_required: int = 0
    overall_progress_percentage: float = 0.0
    completed_rooms_count: int = 0
    in_progress_rooms_count: int = 0
    pending_rooms_count: int = 0
    status: str
    workers: List[ShiftWorkerDetail] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    shifts: List[ShiftResponse]

class ShiftUpdate(BaseModel):
    date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-07-26"})
    start_time: Optional[str] = Field(default=None, json_schema_extra={"example": "09:00"})
    end_time: Optional[str] = Field(default=None, json_schema_extra={"example": "17:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated shift notes"})
    status: Optional[str] = Field(default=None, json_schema_extra={"example": "published"})
    worker_ids: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["worker_id_1"]})
    cleaning_plan_id: Optional[str] = Field(default=None)
    room_ids: Optional[List[str]] = Field(default=None)
    rooms: Optional[List[ShiftRoomInput]] = Field(default=None)

class DayShiftGroup(BaseModel):
    date: str
    total_shifts: int
    shifts: List[ShiftResponse] = Field(default_factory=list)

class ShiftOverviewResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    total_shifts: int
    overview: List[DayShiftGroup] = Field(default_factory=list)

# --- Worker Roster Schemas ---
class RosterShiftDetail(BaseModel):
    id: str
    client_id: str
    name: str
    profile_picture: Optional[str] = None
    phone_number: Optional[str] = None
    time_start: str
    time_end: str
    status: Literal["upcoming", "running", "completed"] = "upcoming"
    location_id: str
    location_name: str
    location_picture: Optional[str] = None
    total_rooms_count: int = 0
    room_ids: List[str] = Field(default_factory=list)
    assigned_admin_name: str = "Admin"
    assigned_admin_profile: Optional[str] = None

class WorkerRosterGroup(BaseModel):
    date: str
    shifts: List[RosterShiftDetail] = Field(default_factory=list)

class WorkerRosterResponse(BaseModel):
    total_shifts: int
    roster: List[WorkerRosterGroup] = Field(default_factory=list)

# --- Shift Execution & Live Status Schemas ---
class ShiftExecutionStateResponse(BaseModel):
    shift_id: str
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    status: str
    overall_progress_percentage: float = 0.0
    total_rooms_count: int = 0
    completed_rooms_count: int = 0
    in_progress_rooms_count: int = 0
    pending_rooms_count: int = 0
    rooms: List[ShiftRoomDetail] = Field(default_factory=list)

class PhotoUploadResponse(BaseModel):
    review_id: str
    shift_id: str
    room_id: str
    photo_url: str
    status: str = "pending_review"
    message: str = "Photo submitted successfully for Admin review"

class PhotoReviewRejectRequest(BaseModel):
    reason: str = Field(..., json_schema_extra={"example": "Photo is blurry, please re-take clear photo of sanitized desk."})

class PhotoReviewCleanerDetail(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None

class PhotoReviewClientDetail(BaseModel):
    client_id: str
    name: str

class PhotoReviewLocationDetail(BaseModel):
    location_id: str
    name: str

class PhotoReviewRoomDetail(BaseModel):
    room_id: str
    name: str

class PhotoReviewItem(BaseModel):
    review_id: str
    shift_id: str
    cleaner: PhotoReviewCleanerDetail
    client: PhotoReviewClientDetail
    location: PhotoReviewLocationDetail
    room: PhotoReviewRoomDetail
    photo_url: str
    photo_name: str
    ai_score: Optional[float] = 90.0
    ai_confidence: Optional[str] = "high"
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"
    rejection_reason: Optional[str] = None
    date_submitted: datetime

class PhotoReviewPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    pending_reviews_count: int
    reviews: List[PhotoReviewItem] = Field(default_factory=list)

class LiveStatusTaskItem(BaseModel):
    id: str
    name: str
    status: Literal["DONE", "ACTIVE", "PENDING"]
    completed_at: Optional[str] = None

class LiveStatusRoomItem(BaseModel):
    room_id: str
    room_name: str
    completed_tasks: int
    total_tasks: int
    tasks: List[LiveStatusTaskItem] = Field(default_factory=list)

class LiveStatusResponse(BaseModel):
    shift_id: str
    client_name: str
    location_name: str
    status: str
    overall_progress_percentage: float
    assigned_cleaner_name: str
    arrival_time: Optional[str] = None
    rooms: List[LiveStatusRoomItem] = Field(default_factory=list)

# --- Worker Home Schemas ---
class ActiveShiftHomeCard(BaseModel):
    shift_id: str
    client_name: str
    location_name: str
    location_address: Optional[str] = None
    start_time: str
    end_time: str
    completed_rooms: int = 0
    total_rooms: int = 0
    overall_progress_percentage: float = 0.0
    status: str = "running"

class NextShiftHomeCard(BaseModel):
    shift_id: str
    client_name: str
    location_name: str
    location_address: Optional[str] = None
    start_time: str
    end_time: str
    time_until_start: Optional[str] = None
    date: str

class HomeStatsCounters(BaseModel):
    todays_shifts: int = 0
    completed: int = 0
    pending: int = 0

class ActivityFeedItem(BaseModel):
    id: str
    title: str
    subtitle: Optional[str] = None
    timestamp: str
    created_at: datetime

class WorkerHomeResponse(BaseModel):
    greeting: str
    worker_name: str
    profile_photo: Optional[str] = None
    active_shift: Optional[ActiveShiftHomeCard] = None
    stats: HomeStatsCounters
    next_shift: Optional[NextShiftHomeCard] = None
    recent_activity: List[ActivityFeedItem] = Field(default_factory=list)

# --- Client Live Status Schemas ---
class AssignedCleanerCard(BaseModel):
    worker_id: str
    name: str
    designation: str = "Team Lead - Alpha"
    profile_picture: Optional[str] = None

class CurrentLocationCard(BaseModel):
    location_id: str
    location_name: str
    address_subtitle: Optional[str] = None

class ArrivalTimeCard(BaseModel):
    arrival_time: str
    arrival_status: str = "On time"
    date_str: str

class ClientLiveTaskItem(BaseModel):
    id: str
    name: str
    time_str: Optional[str] = None
    status: Literal["DONE", "ACTIVE", "PENDING"]
    completed_at: Optional[datetime] = None

class ClientLiveRoomProgress(BaseModel):
    room_id: str
    room_name: str
    location_name: str
    completed_tasks_count: int
    total_tasks_count: int
    tasks: List[ClientLiveTaskItem] = Field(default_factory=list)

class ClientLiveStatusResponse(BaseModel):
    shift_id: str
    status_label: str = "CLEANING IN PROGRESS"
    active_room_location_text: str
    overall_progress_percentage: float
    est_completion_time: Optional[str] = "12:00 PM"
    assigned_cleaner: AssignedCleanerCard
    current_location: CurrentLocationCard
    arrival_time_info: ArrivalTimeCard
    current_active_room: ClientLiveRoomProgress
    all_rooms_progress: List[ClientLiveRoomProgress] = Field(default_factory=list)

# --- Clients Schedule Schemas ---
class ScheduleSummaryCounters(BaseModel):
    this_month_visits: int = 0
    completed_visits: int = 0
    upcoming_visits: int = 0

class ClientCleaningVisitItem(BaseModel):
    id: str
    date_badge_month: str
    date_badge_day: str
    formatted_date: str
    time_interval: str
    assigned_team: str = "Team Alpha"
    service_type: str = "Regular Cleaning"
    status: Literal["In Progress", "Scheduled", "Completed"]
    location_name: str
    location_id: Optional[str] = None
    date_raw: str

class ClientScheduleResponse(BaseModel):
    summary: ScheduleSummaryCounters
    total_count: int
    page: int
    limit: int
    visits: List[ClientCleaningVisitItem] = Field(default_factory=list)




