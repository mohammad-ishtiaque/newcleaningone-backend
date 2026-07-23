from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

class WorkerCheckInResponse(BaseModel):
    shift_id: str
    worker_id: str
    checkin_time: datetime
    status: Literal["ontime", "late"]

class WorkerCheckOutResponse(BaseModel):
    shift_id: str
    worker_id: str
    checkout_time: datetime
    hours_worked: float

class WorkerAttendanceToggleResponse(BaseModel):
    shift_id: str
    worker_id: str
    action: Literal["check_in", "check_out", "already_completed"]
    state: Literal["not_checked_in", "checked_in", "completed"]
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    status: Optional[str] = None
    hours_worked: Optional[float] = None
    is_auto_checked_out: bool = False
    message: str

class WorkerShiftStateResponse(BaseModel):
    shift_id: str
    worker_id: str
    state: Literal["not_checked_in", "checked_in", "completed"]
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    status: Optional[str] = None
    hours_worked: Optional[float] = None
    is_auto_checked_out: bool = False

class LiveStatusItem(BaseModel):
    worker_id: str
    worker_name: str
    worker_type: str
    profile_picture: Optional[str] = None
    shift_id: str
    shift_name: Optional[str] = None
    location_id: str
    location_name: str
    client_id: str
    client_name: str
    shift_start_time: str
    shift_end_time: str
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    status: Literal["ontime", "late", "missing", "scheduled"]

class LiveStatusResponse(BaseModel):
    total_shifts_count: int
    ontime_count: int
    late_count: int
    missing_count: int
    page: int
    limit: int
    items: List[LiveStatusItem] = Field(default_factory=list)

class AttendanceTrackingItem(BaseModel):
    worker_id: str
    worker_name: str
    profile_picture: Optional[str] = None
    worker_type: str
    hours_worked: str  # e.g., "168h" or "168.5h"
    hours_worked_numeric: float
    total_shifts: int
    late_days: int

class AttendanceTrackingPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    workers: List[AttendanceTrackingItem] = Field(default_factory=list)

class LocationStatItem(BaseModel):
    location_id: str
    location_name: str
    client_id: str
    client_name: str
    workers_count: int
    hours_worked: str  # e.g., "220h"
    hours_worked_numeric: float
    shifts_count: int

class LocationStatPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    locations: List[LocationStatItem] = Field(default_factory=list)

class WorkerShiftDetailItem(BaseModel):
    shift_id: str
    client_name: str
    location_name: str
    date: str
    start_time: str
    end_time: str
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    duration_hours: float
    status: str

class WorkerShiftStatsResponse(BaseModel):
    worker_id: str
    worker_name: str
    profile_picture: Optional[str] = None
    worker_type: str
    period: str  # today, weekly, monthly
    total_hours_worked: str  # e.g., "168h"
    total_hours_worked_numeric: float
    total_shifts_count: int
    late_days_count: int
    avg_shift_duration: str  # e.g., "7.5h"
    shifts: List[WorkerShiftDetailItem] = Field(default_factory=list)

class WorkerDailyActivityRow(BaseModel):
    shift_id: str
    date: str  # e.g., "9 Jun 2026"
    date_iso: str  # e.g., "2026-06-09"
    check_in_time: str  # e.g., "08:00"
    check_out_time: str  # e.g., "16:00"
    total_hours: str  # e.g., "8h"
    total_hours_numeric: float
    status: str  # "On Time", "Late", "Absent"

class WorkerDailyActivityResponse(BaseModel):
    worker_id: str
    worker_name: str
    profile_picture: Optional[str] = None
    worker_type: str
    month: str  # e.g., "June 2026"
    month_iso: str  # e.g., "2026-06"
    total_hours_worked: str  # e.g., "56h"
    total_hours_worked_numeric: float
    attendance_percentage: str  # e.g., "100%"
    attendance_percentage_numeric: float
    late_days: int
    absent_days: int
    total_shifts: int
    daily_activity: List[WorkerDailyActivityRow] = Field(default_factory=list)
