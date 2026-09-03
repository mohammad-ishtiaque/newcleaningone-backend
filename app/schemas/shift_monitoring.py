from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from app.schemas.common import BasePaginatedResponse

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
    profile_photo: Optional[str] = None
    profile_picture: Optional[str] = None
    position: Optional[str] = "normal"
    shift_id: str
    shift_name: Optional[str] = None
    date: Optional[str] = Field(default=None, description="Shift service date (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    shift_date: Optional[str] = Field(default=None, description="Shift service date alias (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    location_id: str
    location_name: str
    client_id: str
    client_name: str
    shift_start_time: str
    shift_end_time: str
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    hours_worked_display: Optional[str] = "Not started"
    hours_worked_numeric: float = 0.0
    progress_percentage: float = 0.0
    status: Literal["ontime", "late", "missing", "scheduled"]
    pending_approval_count: int = Field(default=0, description="Photos currently pending manager review", json_schema_extra={"example": 0})
    pending_photos_count: int = Field(default=0, description="Photos pending upload or review", json_schema_extra={"example": 0})
    rejected_photos_count: int = Field(default=0, description="Photos rejected by manager needing resubmission", json_schema_extra={"example": 0})
    uncompleted_tasks_count: int = Field(default=0, description="Cleaning tasks not yet completed", json_schema_extra={"example": 0})
    checkout_blocked_reason: Optional[str] = Field(default=None, description="Detailed reason explaining why checkout is blocked", json_schema_extra={"example": None})
    can_checkout: bool = Field(default=True, description="Whether worker is eligible to check out with all items approved", json_schema_extra={"example": True})

class LiveStatusResponse(BaseModel):
    total_shifts_count: int
    ontime_count: int
    late_count: int
    missing_count: int
    page: int
    limit: int
    has_more: bool = False
    items: List[LiveStatusItem] = Field(default_factory=list)

    def __init__(self, **data):
        if "has_more" not in data or data.get("has_more") is None:
            tsc = data.get("total_shifts_count", 0)
            p = data.get("page", 1)
            lim = data.get("limit", 10)
            data["has_more"] = bool((p * lim) < tsc)
        super().__init__(**data)

class AttendanceTrackingItem(BaseModel):
    worker_id: str
    worker_name: str
    profile_picture: Optional[str] = None
    worker_type: str
    hours_worked: str  # e.g., "168h" or "168.5h"
    hours_worked_numeric: float
    total_shifts: int
    late_days: int

class AttendanceTrackingPaginatedResponse(BasePaginatedResponse):
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

class LocationStatPaginatedResponse(BasePaginatedResponse):
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


# --- Picture 4: Live Worker Details Drawer Schemas ---
class WorkerLiveShiftDetail(BaseModel):
    check_in: str = "--:--"
    check_out: str = "--:--"
    duration: str = "0h"
    status: str = "Scheduled"

class WorkerLiveDetailsResponse(BaseModel):
    worker_id: str
    worker_name: str
    worker_type: str
    position: Optional[str] = None
    shift_id: Optional[str] = None
    shift_label: Optional[str] = None
    current_status: str = "On Time"
    profile_picture: Optional[str] = None
    period: Literal["today", "weekly", "monthly"] = "today"
    hours_worked: str = "0h"
    hours_worked_numeric: float = 0.0
    total_hours_worked: Optional[str] = None
    total_hours_worked_numeric: Optional[float] = None
    shifts_count: int = 0
    total_shifts: Optional[int] = None
    avg_duration: str = "0.0h"
    avg_duration_numeric: float = 0.0
    avg_shift_duration: Optional[str] = None
    shift_details: Optional[WorkerLiveShiftDetail] = Field(default_factory=WorkerLiveShiftDetail)
    activity_history_available: bool = True


# --- Picture 5: Worker Attendance Stats Drawer Schemas ---
class WeeklyTrendItem(BaseModel):
    week_label: Optional[str] = None
    day: Optional[str] = None
    date: Optional[str] = None
    hours: float

    def __init__(self, **data):
        if "week_label" not in data or data.get("week_label") is None:
            data["week_label"] = data.get("day") or data.get("date") or ""
        if "day" not in data:
            data["day"] = data.get("week_label")
        super().__init__(**data)

class MonthlyTrendItem(BaseModel):
    month_label: Optional[str] = None
    month: Optional[str] = None
    hours: float

    def __init__(self, **data):
        if "month_label" not in data or data.get("month_label") is None:
            data["month_label"] = data.get("month") or ""
        if "month" not in data:
            data["month"] = data.get("month_label")
        super().__init__(**data)

class WorkerAttendanceStatsDrawerResponse(BaseModel):
    worker_id: str
    worker_name: str
    worker_type: str
    profile_picture: Optional[str] = None
    hours_worked: str
    hours_worked_numeric: float
    total_hours_worked: Optional[str] = None
    total_hours_worked_numeric: Optional[float] = None
    completed_shifts: int = 0
    total_shifts: Optional[int] = 0
    avg_shift_duration: str = "0.0h"
    avg_shift_duration_numeric: float = 0.0
    late_checkins: int = 0
    late_days: Optional[int] = 0
    weekly_hours_trend: List[WeeklyTrendItem] = Field(default_factory=list)
    weekly_trend: Optional[List[WeeklyTrendItem]] = None
    monthly_hours_trend: List[MonthlyTrendItem] = Field(default_factory=list)
    monthly_trend: Optional[List[MonthlyTrendItem]] = None

    def __init__(self, **data):
        if "hours_worked" not in data and "total_hours_worked" in data:
            data["hours_worked"] = data["total_hours_worked"]
        if "hours_worked_numeric" not in data and "total_hours_worked_numeric" in data:
            data["hours_worked_numeric"] = data["total_hours_worked_numeric"]
        if "completed_shifts" not in data and "total_shifts" in data:
            data["completed_shifts"] = data["total_shifts"]
        if "late_checkins" not in data and "late_days" in data:
            data["late_checkins"] = data["late_days"]
        if "weekly_hours_trend" not in data and "weekly_trend" in data:
            data["weekly_hours_trend"] = data["weekly_trend"]
        if "monthly_hours_trend" not in data and "monthly_trend" in data:
            data["monthly_hours_trend"] = data["monthly_trend"]
        super().__init__(**data)

