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
