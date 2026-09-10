from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime, timezone
from app.schemas.common import BasePaginatedResponse
from app.schemas.client_list import (
    CleaningTaskCreate, CleaningTaskResponse,
    RequiredPhotoCreate, RequiredPhotoResponse
)

class ShiftTaskItem(BaseModel):
    id: str = ""
    name: str = ""
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    is_completed: bool = False
    completed_at: Optional[datetime] = None

    def __init__(self, **data):
        if "task_id" in data and not data.get("id"):
            data["id"] = data["task_id"]
        if "task_name" in data and not data.get("name"):
            data["name"] = data["task_name"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

class SubmittedPhotoItem(BaseModel):
    photo_id: str = ""
    photo_name: str = ""
    photo_type: Optional[str] = None
    photo_url: str = ""
    before_photo_url: Optional[str] = None
    submitted_at: Any = Field(default_factory=lambda: datetime.now(timezone.utc))
    review_id: Optional[str] = None
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"

    def __init__(self, **data):
        if "id" in data and not data.get("photo_id"):
            data["photo_id"] = data["id"]
        if "photo_type" in data and not data.get("photo_name"):
            data["photo_name"] = data["photo_type"]
        if "name" in data and not data.get("photo_name"):
            data["photo_name"] = data["name"]
        elif not data.get("photo_name"):
            data["photo_name"] = "Photo"
        if not data.get("photo_url"):
            data["photo_url"] = data.get("after_photo_url") or data.get("photo_path") or "/uploads/sample.jpg"
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

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
    id: Optional[str] = None
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
    is_completed: bool = False
    approval_status: Optional[str] = "pending"
    completed_at: Optional[str] = None
    tasks: List[ShiftTaskItem] = Field(default_factory=list)
    required_photos: List[RequiredPhotoResponse] = Field(default_factory=list)
    submitted_photos: List[SubmittedPhotoItem] = Field(default_factory=list)
    completed_tasks_count: int = 0
    task_number: int = 0
    photo_number: int = 0

    def __init__(self, **data):
        if "id" in data and not data.get("room_id"):
            data["room_id"] = data["id"]
        elif "room_id" in data and not data.get("id"):
            data["id"] = data["room_id"]
        if "is_completed" not in data:
            data["is_completed"] = (data.get("status") in ["completed", "approved"])
        super().__init__(**data)

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftDraftCreate(BaseModel):
    client_id: str = Field(..., json_schema_extra={"example": "6a61b7f68ad7764bf1032f67"})
    location_id: str = Field(..., json_schema_extra={"example": "2a78f050-4410-424f-86dd-d9a442b67816"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-03"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00"})
    end_time: str = Field(..., json_schema_extra={"example": "16:00"})
    repeat_shift: Optional[str] = Field(default="Does not repeat", json_schema_extra={"example": "Does not repeat"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Deep clean executive floor"})
    cleaning_plan_id: Optional[str] = Field(default=None)
    room_ids: Optional[List[str]] = Field(default=None)
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
    repeat_shift: Optional[str] = "Does not repeat"
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
    client_id: Optional[str] = None
    location_id: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    repeat_shift: Optional[str] = None
    shift_notes: Optional[str] = None
    cleaning_plan_id: Optional[str] = None
    room_ids: Optional[List[str]] = None
    rooms: Optional[List[ShiftRoomInput]] = None

class ShiftDraftPaginatedResponse(BasePaginatedResponse):
    drafts: List[ShiftDraftResponse]

class WorkerDropdownItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    status: Literal["available", "on_shift", "off_duty"] = "available"
    worker_type: str = "employee"
    position: Optional[str] = None

class WorkerDropdownPaginatedResponse(BasePaginatedResponse):
    workers: List[WorkerDropdownItem]

class WorkerShiftAssignmentItem(BaseModel):
    worker_id: str
    shift_role: Optional[str] = "cleaning_specialist"

class ShiftAssignRequest(BaseModel):
    draft_id: str = Field(..., json_schema_extra={"example": "draft_uuid_12345"})
    team_leader_id: Optional[str] = Field(default=None, json_schema_extra={"example": "6a61be526067f847e843f8f9"})
    worker_ids: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["worker_id_1", "worker_id_2"]})
    worker_assignments: Optional[List[WorkerShiftAssignmentItem]] = Field(
        default=None,
        description="Optional list of worker assignments with shift roles (e.g., leader, co_leader, cleaning_specialist)"
    )

class ShiftWorkerDetail(BaseModel):
    worker_id: str
    name: Optional[str] = "Worker"
    profile_picture: Optional[str] = None
    worker_type: Optional[str] = "cleaner"
    shift_role: Optional[str] = "cleaning_specialist"
    position: Optional[str] = "normal"
    checkin_time: Optional[Any] = None
    checkout_time: Optional[Any] = None
    status: Optional[str] = None
    hours_worked: Optional[float] = None
    # Snapshotted at checkout (see worker_salary.rate_for_shift_record) — a later
    # change to the worker's live rate never rewrites what this shift actually paid.
    hourly_rate: Optional[float] = None
    shift_earnings: Optional[float] = None

    def __init__(self, **data):
        if "id" in data and not data.get("worker_id"):
            data["worker_id"] = data["id"]
        if not data.get("name") and data.get("full_name"):
            data["name"] = data["full_name"]
        elif not data.get("name"):
            data["name"] = "Worker"
        if not data.get("worker_type"):
            data["worker_type"] = data.get("position") or "cleaner"
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

class ShiftListItemResponse(BaseModel):
    id: str = Field(..., json_schema_extra={"example": "exec_plan_e3b88980fc_2026-08-26"})
    title: Optional[str] = Field(default=None, json_schema_extra={"example": "Bachelor Plan"})
    draft_id: Optional[str] = Field(default=None, json_schema_extra={"example": None})
    client_id: str = Field(..., json_schema_extra={"example": "cli_0cbe945846"})
    client_name: str = Field(..., json_schema_extra={"example": "Bam Bula"})
    location_id: str = Field(..., json_schema_extra={"example": "loc_0510d4c547"})
    location_name: str = Field(..., json_schema_extra={"example": "Mirpur"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-26"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00 AM"})
    end_time: str = Field(..., json_schema_extra={"example": "09:30 AM"})
    timezone: Optional[str] = Field(default="Europe/Amsterdam", json_schema_extra={"example": "Europe/Amsterdam"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Regular morning clean"})
    cleaning_plan_id: Optional[str] = Field(default=None, json_schema_extra={"example": "plan_e3b88980fc"})
    total_rooms_count: int = Field(default=0, json_schema_extra={"example": 1})
    total_tasks_count: int = Field(default=0, json_schema_extra={"example": 2})
    total_photo_required: int = Field(default=0, json_schema_extra={"example": 2})
    overall_progress_percentage: float = Field(default=0.0, json_schema_extra={"example": 0.0})
    completed_rooms_count: int = Field(default=0, json_schema_extra={"example": 0})
    in_progress_rooms_count: int = Field(default=0, json_schema_extra={"example": 0})
    pending_rooms_count: int = Field(default=0, json_schema_extra={"example": 1})
    service_kind: Optional[str] = Field(default="cleaning_plan", json_schema_extra={"example": "cleaning_plan"})
    status: str = Field(..., json_schema_extra={"example": "scheduled"})
    workers: List[ShiftWorkerDetail] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        from_attributes = True


class ShiftResponse(BaseModel):
    id: str = Field(..., json_schema_extra={"example": "exec_plan_e3b88980fc_2026-08-26"})
    title: Optional[str] = Field(default=None, json_schema_extra={"example": "Bachelor Plan"})
    draft_id: Optional[str] = Field(default=None, json_schema_extra={"example": None})
    client_id: str = Field(..., json_schema_extra={"example": "cli_0cbe945846"})
    client_name: str = Field(..., json_schema_extra={"example": "Bam Bula"})
    location_id: str = Field(..., json_schema_extra={"example": "loc_0510d4c547"})
    location_name: str = Field(..., json_schema_extra={"example": "Mirpur"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-26"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00 AM"})
    end_time: str = Field(..., json_schema_extra={"example": "09:30 AM"})
    timezone: Optional[str] = Field(default="Europe/Amsterdam", json_schema_extra={"example": "Europe/Amsterdam"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Regular morning clean"})
    cleaning_plan_id: Optional[str] = Field(default=None, json_schema_extra={"example": "plan_e3b88980fc"})
    rooms: List[ShiftRoomDetail] = Field(default_factory=list)
    total_rooms_count: int = Field(default=0, json_schema_extra={"example": 1})
    total_tasks_count: int = Field(default=0, json_schema_extra={"example": 2})
    total_photo_required: int = Field(default=0, json_schema_extra={"example": 2})
    overall_progress_percentage: float = Field(default=0.0, json_schema_extra={"example": 0.0})
    completed_rooms_count: int = Field(default=0, json_schema_extra={"example": 0})
    in_progress_rooms_count: int = Field(default=0, json_schema_extra={"example": 0})
    pending_rooms_count: int = Field(default=0, json_schema_extra={"example": 1})
    service_kind: Optional[str] = Field(default="cleaning_plan", json_schema_extra={"example": "cleaning_plan"})
    status: str = Field(..., json_schema_extra={"example": "scheduled"})
    workers: List[ShiftWorkerDetail] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftPaginatedResponse(BasePaginatedResponse):
    shifts: List[ShiftResponse]


class WorkerShiftPaginatedResponse(BasePaginatedResponse):
    shifts: List[ShiftListItemResponse] = Field(default_factory=list)

class ShiftUpdate(BaseModel):
    date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-07-26"})
    start_time: Optional[str] = Field(default=None, json_schema_extra={"example": "09:00"})
    end_time: Optional[str] = Field(default=None, json_schema_extra={"example": "17:00"})
    timezone: Optional[str] = Field(default=None, json_schema_extra={"example": "Europe/Amsterdam"})
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

class PhotoReviewPaginatedResponse(BasePaginatedResponse):
    pending_reviews_count: int = 0
    reviews: List[PhotoReviewItem] = Field(default_factory=list)

class PhotoReviewDetailModalResponse(BaseModel):
    review_id: str
    shift_id: str
    cleaner: PhotoReviewCleanerDetail
    client: PhotoReviewClientDetail
    location: PhotoReviewLocationDetail
    room: PhotoReviewRoomDetail
    before_photo_url: Optional[str] = None
    after_photo_url: str
    photo_name: str
    ai_score: float = 90.0
    ai_confidence: str = "high"
    ai_feature_breakdown: Dict[str, float] = Field(default_factory=dict)
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"
    rejection_reason: Optional[str] = None
    date_submitted: datetime


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
    title: Optional[str] = None
    client_name: str
    location_name: str
    location_address: Optional[str] = None
    start_time: str
    end_time: str
    timezone: Optional[str] = "Europe/Amsterdam"
    completed_rooms: int = 0
    total_rooms: int = 0
    overall_progress_percentage: float = 0.0
    status: str = "running"

class NextShiftHomeCard(BaseModel):
    shift_id: str
    title: Optional[str] = None
    client_name: str
    location_name: str
    location_address: Optional[str] = None
    start_time: str
    end_time: str
    timezone: Optional[str] = "Europe/Amsterdam"
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
    worker_name: str
    profile_photo: Optional[str] = None
    active_shift: Optional[ActiveShiftHomeCard] = None
    stats: HomeStatsCounters
    next_shift: Optional[NextShiftHomeCard] = None
    timezone: Optional[str] = "Europe/Amsterdam"
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

class ClientLiveStatusSessionSummary(BaseModel):
    shift_id: str
    date: Optional[str] = Field(default=None, description="Shift date (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    shift_date: Optional[str] = Field(default=None, description="Shift date alias (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    location_name: str
    room_name: Optional[str] = None
    status: str
    overall_progress_percentage: float
    start_time: str
    end_time: str

class ClientLiveStatusResponse(BaseModel):
    shift_id: str
    date: Optional[str] = Field(default=None, description="Shift date (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    shift_date: Optional[str] = Field(default=None, description="Shift date alias (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    status_label: str = "CLEANING IN PROGRESS"
    active_room_location_text: str
    overall_progress_percentage: float
    est_completion_time: Optional[str] = "12:00 PM"
    assigned_cleaner: AssignedCleanerCard
    current_location: CurrentLocationCard
    arrival_time_info: ArrivalTimeCard
    current_active_room: ClientLiveRoomProgress
    all_rooms_progress: List[ClientLiveRoomProgress] = Field(default_factory=list)
    active_sessions: List[ClientLiveStatusSessionSummary] = Field(default_factory=list)
    pending_approval_count: int = Field(default=0, description="Photos currently pending manager review", json_schema_extra={"example": 0})
    pending_photos_count: int = Field(default=0, description="Photos pending upload or review", json_schema_extra={"example": 0})
    rejected_photos_count: int = Field(default=0, description="Photos rejected by manager needing resubmission", json_schema_extra={"example": 0})
    uncompleted_tasks_count: int = Field(default=0, description="Cleaning tasks not yet completed", json_schema_extra={"example": 0})
    checkout_blocked_reason: Optional[str] = Field(default=None, description="Detailed reason explaining why checkout is blocked", json_schema_extra={"example": None})
    can_checkout: bool = Field(default=True, description="Whether shift is eligible for checkout with all items approved", json_schema_extra={"example": True})

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

class ClientScheduleResponse(BasePaginatedResponse):
    summary: ScheduleSummaryCounters
    visits: List[ClientCleaningVisitItem] = Field(default_factory=list)


# --- Admin Roaster Management Schemas ---
class DailyRosterShiftItem(BaseModel):
    shift_id: str
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    start_time: str
    end_time: str
    time_label: str
    duration_hours: float
    status: str = "scheduled"
    shift_notes: Optional[str] = None
    rooms_count: int = 0

class DailyRosterWorkerRow(BaseModel):
    worker_id: str
    worker_name: str
    profile_photo: Optional[str] = None
    worker_type: Optional[str] = "employee"
    shifts_today_count: int = 0
    shifts_today_label: str = "0 shifts today"
    shifts: List[DailyRosterShiftItem] = Field(default_factory=list)

class DailyRosterBanner(BaseModel):
    header_title: str = "DAILY ROSTER"
    date_str: str
    full_date: str
    total_scheduled_shifts: int = 0
    total_scheduled_hours: float = 0.0

class AdminDailyRosterResponse(BaseModel):
    banner: DailyRosterBanner
    total_team_members: int
    team_members: List[DailyRosterWorkerRow] = Field(default_factory=list)

class WeeklyRosterDayShiftItem(BaseModel):
    shift_id: str
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    start_time: str
    end_time: str
    duration_hours: float
    status: str = "scheduled"

class WeeklyRosterDayCell(BaseModel):
    day_name: str
    date_str: str
    full_date: str
    status: str = "Available"
    shift_count: int = 0
    shifts: List[WeeklyRosterDayShiftItem] = Field(default_factory=list)

class WeeklyRosterWorkerRow(BaseModel):
    worker_id: str
    worker_name: str
    profile_photo: Optional[str] = None
    shifts_this_week_count: int = 0
    shifts_this_week_label: str = "0 shifts this week"
    daily_schedule: List[WeeklyRosterDayCell] = Field(default_factory=list)

class WeeklyRosterBanner(BaseModel):
    header_title: str = "WEEKLY ROSTER"
    range_str: str
    start_date: str
    end_date: str
    total_scheduled_shifts: int = 0
    total_scheduled_hours: float = 0.0

class AdminWeeklyRosterResponse(BaseModel):
    banner: WeeklyRosterBanner
    days: List[dict] = Field(default_factory=list)
    total_team_members: int
    team_members: List[WeeklyRosterWorkerRow] = Field(default_factory=list)

class MonthlyRosterDaySummary(BaseModel):
    day_number: int
    full_date: str
    shift_count: int = 0
    total_hours: float = 0.0
    shifts: List[WeeklyRosterDayShiftItem] = Field(default_factory=list)

class MonthlyRosterWorkerRow(BaseModel):
    worker_id: str
    worker_name: str
    profile_photo: Optional[str] = None
    total_month_shifts: int = 0
    total_month_shifts_label: str = "0 shifts"
    daily_summaries: List[MonthlyRosterDaySummary] = Field(default_factory=list)

class MonthlyRosterBanner(BaseModel):
    header_title: str = "MONTHLY ROSTER"
    month_str: str
    month: int
    year: int
    total_scheduled_shifts: int = 0
    total_team_members: int = 0

class AdminMonthlyRosterResponse(BaseModel):
    banner: MonthlyRosterBanner
    days_in_month: int
    team_members: List[MonthlyRosterWorkerRow] = Field(default_factory=list)

class RosterShiftDetailModalResponse(BaseModel):
    shift_id: str
    worker_id: str
    worker_name: str
    worker_profile_photo: Optional[str] = None
    assignment_label: str = "Scheduled assignment"
    location_id: str
    location_name: str
    location_address: Optional[str] = None
    start_time: str
    end_time: str
    time_range: str
    date: str
    client_id: str
    client_name: str
    status: str = "scheduled"

class RosterShiftCreateRequest(BaseModel):
    client_id: str = Field(..., json_schema_extra={"example": "6a61b7fa8ad7764bf1032f70"})
    location_id: str = Field(..., json_schema_extra={"example": "7d355c71-a0bb-4ca1-a468-9cebf06bb0dc"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-03"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00"})
    end_time: str = Field(..., json_schema_extra={"example": "14:00"})
    worker_ids: List[str] = Field(..., json_schema_extra={"example": ["6a61be526067f847e843f8f9"]})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Regular office cleaning"})
    cleaning_plan_id: Optional[str] = Field(default=None)
    room_ids: Optional[List[str]] = Field(default=None)

class ClientLiveShiftPaginatedResponse(BasePaginatedResponse):
    shifts: List[ShiftResponse] = Field(default_factory=list)


# ============================================================================
# Shift Checklist & Task Photo Submission Schemas
# ============================================================================

class ChecklistPhotoItem(BaseModel):
    id: str = Field(..., description="Photo requirement unique ID (e.g. 'a2763ebc')")
    name: str = Field(..., description="Photo requirement name (e.g. 'Floor', 'Washroom')")
    photo_url: Optional[str] = Field(default=None, description="Submitted photo URL")
    status: str = Field(default="not_uploaded", description="Photo approval status: 'not_uploaded', 'pending_review', 'approved', 'rejected'")
    rejection_reason: Optional[str] = Field(default=None, description="Rejection reason if rejected by manager")
    submitted_at: Optional[datetime] = Field(default=None, description="Submission timestamp")


class ChecklistTaskItem(BaseModel):
    id: str = Field(..., description="Task unique ID")
    name: str = Field(..., description="Task name")
    frequency_type: Optional[str] = Field(default="every_visit", description="Frequency type")
    is_photo_req: bool = Field(default=False, description="Whether photos are required for this task")
    is_completed: bool = Field(default=False, description="True once all required photos are approved")
    completed_at: Optional[datetime] = Field(default=None, description="Completion timestamp")
    required_photos: List[ChecklistPhotoItem] = Field(default_factory=list, description="List of required photos for this task")


class ChecklistRoomItem(BaseModel):
    room_id: str = Field(..., description="Room ID")
    room_name: str = Field(..., description="Room name")
    room_type: Optional[str] = Field(default="standard", description="Room type")
    floor: Optional[int] = Field(default=1, description="Floor number")
    duration: Optional[int] = Field(default=30, description="Duration in minutes")
    status: str = Field(default="pending", description="Room status: 'pending', 'in_progress', 'completed'")
    is_completed: bool = Field(default=False, description="Whether all tasks in room are completed")
    completed_at: Optional[datetime] = Field(default=None, description="Room completion timestamp")
    tasks: List[ChecklistTaskItem] = Field(default_factory=list, description="Tasks in this room")


class ShiftChecklistResponse(BaseModel):
    shift_id: str = Field(..., description="Shift execution ID")
    title: str = Field(..., description="Shift / Cleaning Plan title")
    service_kind: str = Field(default="cleaning_plan", description="'cleaning_plan' or 'extra_service'")
    date: str = Field(..., description="Shift date (YYYY-MM-DD)")
    start_time: str = Field(..., description="Start time (e.g. 08:00 AM)")
    end_time: str = Field(..., description="End time (e.g. 10:30 AM)")
    status: str = Field(default="scheduled", description="Shift status")
    rooms: List[ChecklistRoomItem] = Field(default_factory=list, description="Rooms with task checklists")
    additional_tasks: List[ChecklistTaskItem] = Field(default_factory=list, description="Additional tasks for this shift")
    total_tasks_count: int = Field(default=0, description="Total tasks count")
    completed_tasks_count: int = Field(default=0, description="Completed tasks count")
    total_photos_count: int = Field(default=0, description="Total photos required count")
    approved_photos_count: int = Field(default=0, description="Approved photos count")
    pending_photos_count: int = Field(default=0, description="Pending review photos count")
    rejected_photos_count: int = Field(default=0, description="Rejected photos count")


class SubmitTaskPhotoRequest(BaseModel):
    photo_id: str = Field(..., description="Target required photo ID from task")
    photo_url: str = Field(..., description="Uploaded photo URL")
    task_id: Optional[str] = Field(default=None, description="Linked Task ID")
    room_id: Optional[str] = Field(default=None, description="Linked Room ID")
    is_additional_task: bool = Field(default=False, description="Whether this photo is for an additional task")
    comment: Optional[str] = Field(default=None, description="Optional worker note/comment")


class SubmitTaskPhotoResponse(BaseModel):
    review_id: str = Field(..., description="Generated photo review ID", json_schema_extra={"example": "RV-0EBB4A"})
    photo_id: str = Field(..., description="Required photo ID", json_schema_extra={"example": "a2763ebc"})
    status: str = Field(default="pending_review", description="Photo review status", json_schema_extra={"example": "pending_review"})
    message: str = Field(default="Photo submitted successfully for manager review", json_schema_extra={"example": "Photo submitted successfully for manager review"})
    photo_url: Optional[str] = Field(default=None, description="Submitted photo URL", json_schema_extra={"example": "https://s3.example.com/uploads/floor_clean.jpg"})
    photo_name: Optional[str] = Field(default=None, description="Auto-resolved photo requirement name", json_schema_extra={"example": "Floor"})
    task_id: Optional[str] = Field(default=None, description="Auto-resolved linked task ID", json_schema_extra={"example": "5afaaa81"})
    task_name: Optional[str] = Field(default=None, description="Auto-resolved linked task name", json_schema_extra={"example": "Floor Cleaning"})
    room_id: Optional[str] = Field(default=None, description="Auto-resolved linked room ID", json_schema_extra={"example": "room_6ff38b0f16"})
    room_name: Optional[str] = Field(default=None, description="Auto-resolved linked room name", json_schema_extra={"example": "Bachelor Room"})








