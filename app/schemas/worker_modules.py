from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse

# --- Worker Assignments Schemas ---
class WorkerAssignmentRoom(BaseModel):
    room_id: str
    room_name: str
    room_type: str = "standard"
    floor: int = 1
    tasks_count: int = 0
    photos_required_count: int = 0

class WorkerAssignmentLeader(BaseModel):
    worker_id: str
    name: str
    position: str = "teamleader"
    phone: Optional[str] = None
    profile_photo: Optional[str] = None

class WorkerAssignmentItem(BaseModel):
    id: str
    title: str = Field(..., json_schema_extra={"example": "Amsterdam Tower Weekly Plan"})
    service_kind: Literal["cleaning_plan", "extra_service"] = "cleaning_plan"
    client_id: str
    client_name: str = Field(..., json_schema_extra={"example": "Hilton Amsterdam"})
    location_id: str
    location_name: str = Field(..., json_schema_extra={"example": "Main Tower"})
    location_address: Optional[str] = None
    start_time: str = Field(..., json_schema_extra={"example": "08:00 AM"})
    end_time: str = Field(..., json_schema_extra={"example": "04:00 PM"})
    timezone: str = "Europe/Amsterdam"
    repeat_shift: str = "Every week"
    working_days: List[str] = Field(default_factory=list, json_schema_extra={"example": ["mon", "wed", "fri"]})
    position: str = Field(default="normal", json_schema_extra={"example": "normal"})
    total_rooms_count: int = 1
    rooms: List[WorkerAssignmentRoom] = Field(default_factory=list)
    team_leader: Optional[WorkerAssignmentLeader] = None
    co_leaders: List[WorkerAssignmentLeader] = Field(default_factory=list)
    shift_notes: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now())

class WorkerAssignmentsPaginatedResponse(BasePaginatedResponse):
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    assignments: List[WorkerAssignmentItem] = Field(default_factory=list)


# --- Worker Invoices / Payouts Schemas ---
class WorkerInvoiceItem(BaseModel):
    invoice_id: str = Field(..., json_schema_extra={"example": "inv_2026_08_01"})
    invoice_number: str = Field(..., json_schema_extra={"example": "INV-2026-0881"})
    period_start: str = Field(..., json_schema_extra={"example": "2026-08-01"})
    period_end: str = Field(..., json_schema_extra={"example": "2026-08-15"})
    hours_worked: float = Field(..., json_schema_extra={"example": 72.5})
    regular_hours: Optional[float] = Field(default=None, json_schema_extra={"example": 67.0})
    overtime_hours: Optional[float] = Field(default=None, json_schema_extra={"example": 5.5})
    hourly_rate: float = Field(..., json_schema_extra={"example": 18.50})
    gross_amount: float = Field(..., json_schema_extra={"example": 1341.25})
    bonus_amount: float = Field(default=0.0, json_schema_extra={"example": 50.0})
    deductions: float = Field(default=0.0, json_schema_extra={"example": 0.0})
    net_payout: float = Field(..., json_schema_extra={"example": 1391.25})
    # How much of gross_amount is still owed on THIS invoice specifically
    # (gross_amount minus net_payout, taken from the real stored record).
    balance_due: float = Field(default=0.0, json_schema_extra={"example": 0.0})
    currency: str = "EUR"
    status: Literal["paid", "pending", "processing", "partial"] = "paid"
    payment_method: Optional[str] = "Bank Transfer"
    paid_at: Optional[datetime] = None
    download_url: Optional[str] = None

class WorkerInvoicesPaginatedResponse(BasePaginatedResponse):
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    # Gross value of all real invoices on record (sum of gross_amount) — distinct
    # from total_paid below. "Earned" is what the work is worth; "paid" is what
    # has actually been disbursed so far.
    total_earned: float = 0.0
    total_paid: float = 0.0
    pending_payout: float = 0.0
    # The worker's current live hourly rate — for a header like "25 EUR/h".
    # Not tied to any one invoice; read fresh from the worker's own profile.
    current_hourly_rate: float = 0.0
    total_hours_worked: float = 0.0
    total_overtime_hours: float = 0.0
    invoices: List[WorkerInvoiceItem] = Field(default_factory=list)


# --- Worker Availability Schemas ---
class DayAvailability(BaseModel):
    day: str = Field(..., json_schema_extra={"example": "monday"})
    is_available: bool = True
    start_time: str = Field(default="08:00 AM", json_schema_extra={"example": "08:00 AM"})
    end_time: str = Field(default="05:00 PM", json_schema_extra={"example": "05:00 PM"})

class LeaveRequestItem(BaseModel):
    id: str
    start_date: str = Field(..., json_schema_extra={"example": "2026-09-01"})
    end_date: str = Field(..., json_schema_extra={"example": "2026-09-05"})
    reason: str = Field(..., json_schema_extra={"example": "Annual leave vacation"})
    status: Literal["approved", "pending", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now())

class WorkerAvailabilityResponse(BaseModel):
    worker_id: str
    weekly_availability: List[DayAvailability] = Field(default_factory=list)
    preferred_hours_per_week: int = Field(default=40, json_schema_extra={"example": 40})
    leave_requests: List[LeaveRequestItem] = Field(default_factory=list)

class WorkerAvailabilityUpdateRequest(BaseModel):
    weekly_availability: Optional[List[DayAvailability]] = None
    preferred_hours_per_week: Optional[int] = Field(default=None, json_schema_extra={"example": 40})

class LeaveRequestCreate(BaseModel):
    start_date: str = Field(..., json_schema_extra={"example": "2026-09-01"})
    end_date: str = Field(..., json_schema_extra={"example": "2026-09-05"})
    reason: str = Field(..., json_schema_extra={"example": "Medical appointment leave"})


# ============================================================================
# --- Worker Estimated Earnings Schemas ---
# ============================================================================
class WorkerShiftEarningItem(BaseModel):
    shift_id: str = Field(..., json_schema_extra={"example": "exec_plan_1234_2026-08-30"})
    title: str = Field(..., json_schema_extra={"example": "Hilton Amsterdam Cleaning"})
    location_name: str = Field(..., json_schema_extra={"example": "Main Tower"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-30"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00 AM"})
    end_time: str = Field(..., json_schema_extra={"example": "10:30 AM"})
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    raw_hours_worked: float = Field(..., json_schema_extra={"example": 2.52})
    rounded_hours_worked: float = Field(..., json_schema_extra={"example": 3.0})
    regular_hours: float = Field(..., json_schema_extra={"example": 2.5})
    overtime_hours: float = Field(..., json_schema_extra={"example": 0.5})
    hourly_rate: float = Field(..., json_schema_extra={"example": 25.0})
    earnings: float = Field(..., json_schema_extra={"example": 75.0})
    status: str = Field(..., json_schema_extra={"example": "completed"})

class WorkerDailyEarningItem(BaseModel):
    date: str = Field(..., json_schema_extra={"example": "2026-08-30"})
    day_name: str = Field(..., json_schema_extra={"example": "Sunday"})
    shifts_count: int = Field(..., json_schema_extra={"example": 2})
    total_hours_worked: float = Field(..., json_schema_extra={"example": 5.5})
    regular_hours: float = Field(..., json_schema_extra={"example": 5.0})
    overtime_hours: float = Field(..., json_schema_extra={"example": 0.5})
    daily_earnings: float = Field(..., json_schema_extra={"example": 137.5})

class WorkerEstimatedEarningsResponse(BaseModel):
    worker_id: str
    worker_name: str
    hourly_rate: float = Field(..., json_schema_extra={"example": 25.0})
    currency: str = "EUR"
    timeframe: str = Field(..., json_schema_extra={"example": "month"})
    period_label: str = Field(..., json_schema_extra={"example": "August 2026"})
    total_shifts_worked: int = Field(..., json_schema_extra={"example": 14})
    total_hours_worked: float = Field(..., json_schema_extra={"example": 42.5})
    regular_hours: float = Field(..., json_schema_extra={"example": 38.0})
    overtime_hours: float = Field(..., json_schema_extra={"example": 4.5})
    estimated_gross_earnings: float = Field(..., json_schema_extra={"example": 1062.5})
    total_paid_earnings: float = Field(default=0.0, json_schema_extra={"example": 500.0})
    pending_balance: float = Field(default=0.0, json_schema_extra={"example": 562.5})
    daily_breakdown: List[WorkerDailyEarningItem] = Field(default_factory=list)
    shifts: List[WorkerShiftEarningItem] = Field(default_factory=list)


# ============================================================================
# --- Manager Worker Monthly Earnings & Invoicing Schemas ---
# ============================================================================
class ManagerWorkerMonthlyEarningsResponse(BaseModel):
    worker_id: str
    worker_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    worker_type: str = "employee"
    position: str = "Cleaner"
    hourly_rate: float = 25.0
    currency: str = "EUR"
    month: int = 8
    year: int = 2026
    month_name: str = "August 2026"
    total_shifts_worked: int = 0
    total_hours_worked: float = 0.0
    regular_hours: float = 0.0
    overtime_hours: float = 0.0
    gross_earnings: float = 0.0
    total_paid: float = 0.0
    balance_due: float = 0.0
    payment_status: Literal["paid", "partial", "unpaid"] = "unpaid"
    shifts: List[WorkerShiftEarningItem] = Field(default_factory=list)
    invoices: List[WorkerInvoiceItem] = Field(default_factory=list)

class ManagerWorkerEarningsOverviewItem(BaseModel):
    worker_id: str
    name: str
    email: Optional[str] = None
    worker_type: str = "employee"
    position: str = "Cleaner"
    hourly_rate: float = 25.0
    total_shifts: int = 0
    total_hours: float = 0.0
    regular_hours: float = 0.0
    overtime_hours: float = 0.0
    gross_earnings: float = 0.0
    total_paid: float = 0.0
    balance_due: float = 0.0
    payment_status: Literal["paid", "partial", "unpaid"] = "unpaid"

class ManagerWorkerEarningsPaginatedResponse(BasePaginatedResponse):
    total_workers: int = 0
    total_gross_payout: float = 0.0
    total_paid_payout: float = 0.0
    total_balance_due: float = 0.0
    month: int = 8
    year: int = 2026
    workers: List[ManagerWorkerEarningsOverviewItem] = Field(default_factory=list)

class ManagerWorkerInvoiceCreate(BaseModel):
    worker_id: str = Field(..., json_schema_extra={"example": "6a8d6190b230abb1f64db3c2"})
    period_month: int = Field(default=8, ge=1, le=12, json_schema_extra={"example": 8})
    period_year: int = Field(default=2026, ge=2020, json_schema_extra={"example": 2026})
    amount_paid: float = Field(..., gt=0, json_schema_extra={"example": 500.0})
    payment_method: Optional[str] = Field(default="bank_transfer", json_schema_extra={"example": "bank_transfer"})  # bank_transfer, cash, stripe, check
    payment_status: Optional[Literal["paid", "partial", "pending"]] = Field(default="paid", json_schema_extra={"example": "paid"})
    payment_reference: Optional[str] = Field(default=None, json_schema_extra={"example": "TXN-987654321"})
    notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Mid-month salary payout"})
    due_date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-08-31"})

class ManagerWorkerInvoiceUpdate(BaseModel):
    amount_paid: Optional[float] = Field(default=None, gt=0, json_schema_extra={"example": 650.0})
    payment_status: Optional[Literal["paid", "partial", "pending", "cancelled", "refunded"]] = Field(default=None, json_schema_extra={"example": "paid"})
    payment_method: Optional[str] = Field(default=None, json_schema_extra={"example": "bank_transfer"})
    payment_reference: Optional[str] = Field(default=None, json_schema_extra={"example": "TXN-987654321-UPDATED"})
    notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Full settlement cleared"})

class ManagerWorkerInvoiceDetailResponse(BaseModel):
    id: str = Field(..., json_schema_extra={"example": "inv_202608_a1b2c3"})
    invoice_id: str = Field(..., json_schema_extra={"example": "INV-202608-A1B2C3"})
    invoice_number: str = Field(..., json_schema_extra={"example": "INV-202608-A1B2C3"})
    worker_id: str
    worker_name: str
    worker_email: Optional[str] = None
    worker_type: str = "employee"
    period_month: int
    period_year: int
    period_label: str
    hours_worked: float
    regular_hours: float
    overtime_hours: float
    hourly_rate: float
    gross_amount: float
    amount_paid: float
    balance_due: float
    currency: str = "EUR"
    payment_status: str
    payment_method: Optional[str] = "bank_transfer"
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    created_by_manager_id: Optional[str] = None
    created_by_manager_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    shifts_included: List[WorkerShiftEarningItem] = Field(default_factory=list)

class ManagerWorkerInvoicePaginatedResponse(BasePaginatedResponse):
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    total_invoiced_amount: float = 0.0
    total_paid_amount: float = 0.0
    invoices: List[ManagerWorkerInvoiceDetailResponse] = Field(default_factory=list)

