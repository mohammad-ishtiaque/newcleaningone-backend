from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class MinimalItem(BaseModel):
    id: str
    name: str

class ShiftSummaryGroup(BaseModel):
    count: int
    shifts: List[MinimalItem] = Field(default_factory=list)

class WorkerSummaryGroup(BaseModel):
    count: int
    workers: List[MinimalItem] = Field(default_factory=list)

class LocationSummaryGroup(BaseModel):
    count: int
    locations: List[MinimalItem] = Field(default_factory=list)

class AdminDashboardHomeResponse(BaseModel):
    active_shifts_now: ShiftSummaryGroup
    todays_upcoming_shifts: ShiftSummaryGroup
    todays_completed_shifts: ShiftSummaryGroup
    active_workers: WorkerSummaryGroup
    active_locations: LocationSummaryGroup

class InProgressShiftItem(BaseModel):
    shift_id: str
    worker_id: str
    worker_name: str
    worker_profile_picture: Optional[str] = None
    location_id: str
    location_name: str
    location_picture_url: Optional[str] = None
    worker_checkin_time: Optional[datetime] = None
    shift_start_time: str
    shift_end_time: str
    progress: float  # e.g. 20.0
    progress_percentage: str  # e.g. "20%"
    checkin_status: str  # "on_time", "late", "missing"

class InProgressShiftPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    shifts: List[InProgressShiftItem] = Field(default_factory=list)

class WorkerAttendanceDetailItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    worker_type: str
    shift_id: Optional[str] = None
    checkin_time: Optional[datetime] = None

class WorkerAttendanceGroup(BaseModel):
    count: int
    workers: List[WorkerAttendanceDetailItem] = Field(default_factory=list)

class WorkerAttendanceSummaryResponse(BaseModel):
    total_checkin: WorkerAttendanceGroup
    late_workers: WorkerAttendanceGroup
    missing_workers: WorkerAttendanceGroup

# --- Admin Dashboard HomePage Operations Overview Schemas (Image Mockup) ---
class AttentionWorkerCallPill(BaseModel):
    worker_id: str
    worker_name: str
    late_duration_minutes: int
    late_duration_text: str
    phone_number: Optional[str] = None

class AttentionRequiredBanner(BaseModel):
    people_need_attention_count: int = 3
    badge_text: str = "30+ min late"
    banner_subtitle: str = "Contact them now or arrange a replacement."
    call_pills: List[AttentionWorkerCallPill] = Field(default_factory=list)

class DashboardSummaryCards(BaseModel):
    active_shifts_count: int = 14
    workers_on_site_count: int = 8
    late_no_show_count: int = 3
    reviews_pending_count: int = 12

class DashboardWorkerItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    shift_time_range: str
    delay_reason: Optional[str] = None
    status: str
    status_badge_label: str
    can_call: bool = False
    phone_number: Optional[str] = None

class ClientLocationGroup(BaseModel):
    client_id: str
    client_company_name: str
    location_id: str
    location_name: str
    roster_count_text: str
    workers: List[DashboardWorkerItem] = Field(default_factory=list)

class OpenEscalationsBanner(BaseModel):
    open_escalations_count: int = 2
    subtitle: str = "One requires a response today"
    action_url: str = "/admin/escalations"

class AdminDashboardOperationsOverviewResponse(BaseModel):
    greeting: str
    subtitle_date: str
    attention_banner: AttentionRequiredBanner
    summary_cards: DashboardSummaryCards
    live_operations_by_client: List[ClientLocationGroup] = Field(default_factory=list)
    open_escalations_banner: OpenEscalationsBanner

