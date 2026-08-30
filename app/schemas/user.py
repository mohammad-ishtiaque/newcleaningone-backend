from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Literal
from datetime import datetime, date
from app.models.user import RoleEnum, WorkerTypeEnum
from app.schemas.common import BasePaginatedResponse
from app.services.worker_salary import (
    DEFAULT_HOURLY_RATE,
    RATE_FIELD_DESCRIPTION,
    reject_legacy_salary_field,
    validate_hourly_rate,
)

# ---- Auth Requests ----
class LoginRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})
    password: str = Field(json_schema_extra={"example": "Secure123"})
    remember_me: bool = Field(default=True, json_schema_extra={"example": True})
    onesignal_player_id: Optional[str] = Field(default=None, json_schema_extra={"example": "1234-5678-abcd"})

class VerifyEmailRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})
    otp_code: str = Field(json_schema_extra={"example": "string"})
    onesignal_player_id: Optional[str] = Field(default=None, json_schema_extra={"example": "1234-5678-abcd"})

class ResendOTPRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})

class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})

class ResetPasswordRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})
    otp_code: str = Field(json_schema_extra={"example": "string"})
    new_password: str = Field(json_schema_extra={"example": "Worker123"})

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(json_schema_extra={"example": "Worker123"})
    new_password: str = Field(json_schema_extra={"example": "Secure123"})

# ---- Base User Response ----
class UserResponse(BaseModel):
    id: str = Field(alias="id", default=None)
    full_name: str
    name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None
    role: RoleEnum
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    profile_photo: Optional[str] = None
    push_notifications_enabled: bool = True
    temporary_password: Optional[str] = None

    def __init__(self, **data):
        if "name" not in data and "full_name" in data:
            data["name"] = data["full_name"]
        elif "full_name" not in data and "name" in data:
            data["full_name"] = data["name"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        from_attributes = True

class ManagerCreateResponse(UserResponse):
    pass

class SignupResponse(BaseModel):
    message: str = "Signup successful. Please verify your email via OTP."
    user: UserResponse

# ---- Profile Responses ----
class WorkerProfileResponse(UserResponse):
    employee_id: Optional[str] = None
    dob: Optional[date] = None
    nationality: Optional[str] = None
    worker_type: Optional[WorkerTypeEnum] = None
    id_uploaded: bool = False
    id_card_front_link: Optional[str] = None
    id_card_back_link: Optional[str] = None
    certificate_uploaded: bool = False
    certificate_count: int = 0
    certificate_links: List[str] = []
    profile_photo_uploaded: bool = False
    profile_photo_link: Optional[str] = None
    onboarding_complete1: bool = False
    is_profile_completed: bool = False
    isagree_condition: Optional[bool] = False
    location: Optional[str] = None
    working_days: List[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat"])
    off_days: List[str] = Field(default_factory=lambda: ["sun"])

class WorkerWorkingDaysResponse(BaseModel):
    worker_id: str = Field(..., json_schema_extra={"example": "6a8d6190b230abb1f64db3c2"})
    worker_name: str = Field(..., json_schema_extra={"example": "Jahid Hasan"})
    working_days: List[str] = Field(
        default=["mon", "tue", "wed", "thu", "fri", "sat"],
        json_schema_extra={"example": ["mon", "tue", "wed", "thu", "fri", "sat"]},
        description="List of active working days (e.g. ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'])"
    )
    off_days: List[str] = Field(
        default=["sun"],
        json_schema_extra={"example": ["sun"]},
        description="List of off-duty days when worker is unavailable (e.g. ['sun'])"
    )
    total_working_days: int = Field(default=6, json_schema_extra={"example": 6})
    updated_at: Optional[datetime] = None

class WorkerWorkingDaysUpdate(BaseModel):
    working_days: List[str] = Field(
        ...,
        json_schema_extra={"example": ["mon", "tue", "wed", "thu", "fri", "sat"]},
        description="List of working days. Accepts 3-letter abbreviations ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun') or full day names ('monday', 'tuesday', etc.)."
    )

class WorkerProfileEdit(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None

class WorkerPersonalInformation(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    employee_id:  Optional[str] = None
    location: Optional[str] = None

class PushSettingsUpdate(BaseModel):
    onesignal_player_id: Optional[str] = None

class PushSettingsResponse(BaseModel):
    push_notifications_enabled: bool
    onesignal_player_id: Optional[str] = None

class WorkerDraftResponse(BaseModel):
    dob: Optional[date] = None
    nationality: Optional[str] = None
    worker_type: Optional[WorkerTypeEnum] = None
    isagree_condition: Optional[bool] = None
    id_card_front_link: Optional[str] = None
    id_card_back_link: Optional[str] = None
    profile_photo_link: Optional[str] = None
    certificate_count: int = 0
    certificate_links: List[str] = []

class ClientProfileResponse(UserResponse):
    company_name: Optional[str] = None

class AdminProfileResponse(UserResponse):
    address: Optional[str] = None
    website: Optional[str] = None

# ---- Creation & Updates ----
class UserCreate(BaseModel):
    full_name: str = Field(json_schema_extra={"example": "Sadim Hasan"})
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})
    password: str = Field(json_schema_extra={"example": "Secure123"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "Secure123"})

# Worker
class WorkerSignup(UserCreate):
    pass

class WorkerOnboardingStep1(BaseModel):
    dob: date = Field(json_schema_extra={"example": "2002-02-27"})
    nationality: str = Field(json_schema_extra={"example": "Bangladeshi"})
    worker_type: WorkerTypeEnum = Field(json_schema_extra={"example": "full_time"})
    isagree_condition: bool = Field(default=True, json_schema_extra={"example": True})

# Client
class ClientSignup(BaseModel):
    full_name: str = Field(json_schema_extra={"example": "Mahfuz Alam"})
    email: EmailStr = Field(json_schema_extra={"example": "c1@yopmail.com"})
    password: str = Field(json_schema_extra={"example": "Secure123"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801318532935"})
    company_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Betopia Group"})

class ClientUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    company_name: Optional[str] = None

# Admin
class AdminCreate(BaseModel):
    full_name: str = Field(json_schema_extra={"example": "Jahid Hasan"})
    email: EmailStr = Field(json_schema_extra={"example": "a1@yopmail.com"})
    password: str = Field(json_schema_extra={"example": "Secure123"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8812345678912"})
    role: RoleEnum = Field(default=RoleEnum.admin, json_schema_extra={"example": "admin"})

class AdminUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Mahfuz ALam"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801319414320"})
    address: Optional[str] = Field(default=None, json_schema_extra={"example": "Mohakhali"})
    website: Optional[str] = Field(default=None, json_schema_extra={"example": "www.Betopia.com"})
    is_active: Optional[bool] = Field(default=None, json_schema_extra={"example": True})

# ---- Admin Worker Management Schemas ----
class AdminWorkerCreate(BaseModel):
    full_name: str = Field(..., json_schema_extra={"example": "Rahim Ahmed"})
    name: Optional[str] = None
    worker_type: Literal["employee", "freelancer"] = Field(default="employee", json_schema_extra={"example": "employee"})
    position: Optional[str] = Field(default="Cleaner", json_schema_extra={"example": "Cleaner"})
    email: EmailStr = Field(..., json_schema_extra={"example": "rahim.worker@yopmail.com"})
    phone: str = Field(..., json_schema_extra={"example": "+8801700000000"})
    status: Literal["active", "suspended", "banned"] = Field(default="active", json_schema_extra={"example": "active"})
    base_location: Optional[str] = Field(default="Amsterdam-Centrum", json_schema_extra={"example": "Amsterdam-Centrum"})
    hourly_rate: float = Field(default=DEFAULT_HOURLY_RATE, description=RATE_FIELD_DESCRIPTION, json_schema_extra={"example": 25.5})
    languages: List[str] = Field(default_factory=lambda: ["Nederlands", "English"], json_schema_extra={"example": ["Nederlands", "English"]})
    national_id: Optional[str] = Field(default=None, json_schema_extra={"example": "NID-12345678"})
    certificates: List[str] = Field(default_factory=list, json_schema_extra={"example": ["Certificate in Professional Cleaning"]})
    national_id_front: Optional[str] = None
    national_id_back: Optional[str] = None
    employee_contract_pdf: Optional[str] = None

    def __init__(self, **data):
        if "full_name" not in data and "name" in data:
            data["full_name"] = data["name"]
        elif "name" not in data and "full_name" in data:
            data["name"] = data["full_name"]
        super().__init__(**data)

    _reject_legacy_salary = model_validator(mode="before")(reject_legacy_salary_field)

    @field_validator("hourly_rate", mode="before")
    @classmethod
    def _check_hourly_rate(cls, v):
        return validate_hourly_rate(v)

class AdminWorkerUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Rahim Ahmed"})
    name: Optional[str] = None
    email: Optional[EmailStr] = Field(default=None, json_schema_extra={"example": "rahim.worker@yopmail.com"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801700000000"})
    worker_type: Optional[Literal["employee", "freelancer"]] = Field(default=None, json_schema_extra={"example": "employee"})
    position: Optional[str] = Field(default=None, json_schema_extra={"example": "Senior Cleaner"})
    base_location: Optional[str] = Field(default=None, json_schema_extra={"example": "Amsterdam-Centrum"})
    hourly_rate: Optional[float] = Field(default=None, description=RATE_FIELD_DESCRIPTION + " Omit to leave the worker's current rate unchanged.", json_schema_extra={"example": 30.5})
    languages: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["Nederlands", "English"]})
    status: Optional[Literal["active", "suspended", "banned", "on_shift", "off_duty"]] = Field(default=None, json_schema_extra={"example": "active"})
    national_id: Optional[str] = Field(default=None, json_schema_extra={"example": "NID-12345678"})
    certificates: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["Advanced Cleaning Cert"]})
    national_id_front: Optional[str] = None
    national_id_back: Optional[str] = None
    employee_contract_pdf: Optional[str] = None

    def __init__(self, **data):
        if "full_name" not in data and "name" in data:
            data["full_name"] = data["name"]
        elif "name" not in data and "full_name" in data:
            data["name"] = data["full_name"]
        super().__init__(**data)

    _reject_legacy_salary = model_validator(mode="before")(reject_legacy_salary_field)

    @field_validator("hourly_rate", mode="before")
    @classmethod
    def _check_hourly_rate(cls, v):
        return v if v is None else validate_hourly_rate(v)

class AdminWorkerResponse(BaseModel):
    id: str
    name: str
    role: str = "worker"
    worker_type: str
    position: str
    email: EmailStr
    phone: str
    status: str
    base_location: Optional[str] = None
    hourly_rate: float = 25.0
    languages: List[str] = Field(default_factory=list)
    national_id_front: Optional[str] = None
    national_id_back: Optional[str] = None
    employee_contract_pdf: Optional[str] = None
    is_signup: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class AdminWorkerPaginatedResponse(BasePaginatedResponse):
    workers: List[AdminWorkerResponse]

from typing import Optional, List, Literal

# ---- Worker Approval Management Schemas ----
class WorkerApproveRequest(BaseModel):
    worker_type: Optional[Literal["employee", "freelancer"]] = Field(default="employee", json_schema_extra={"example": "employee"})
    position: Optional[str] = Field(default="Cleaner", json_schema_extra={"example": "Cleaner"})
    base_location: Optional[str] = Field(default="Amsterdam-Centrum", json_schema_extra={"example": "Amsterdam-Centrum"})
    hourly_rate: Optional[float] = Field(default=None, description=RATE_FIELD_DESCRIPTION + " Omit to keep the rate already on the worker's account.", json_schema_extra={"example": 25.5})

    _reject_legacy_salary = model_validator(mode="before")(reject_legacy_salary_field)

    @field_validator("hourly_rate", mode="before")
    @classmethod
    def _check_hourly_rate(cls, v):
        return v if v is None else validate_hourly_rate(v)

class WorkerRejectRequest(BaseModel):
    reject_reason: Optional[str] = Field(default=None, json_schema_extra={"example": "Incomplete documentation or identity verification failed"})

class WorkerApprovalUpdate(BaseModel):
    approval_status: Literal["approved", "rejected"] = Field(..., json_schema_extra={"example": "approved"})  # approved, rejected
    rejection_reason: Optional[str] = Field(default=None, json_schema_extra={"example": "Incomplete documentation"})

class WorkerApprovalResponse(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    worker_type: Optional[str] = None
    approval_status: str
    is_approved: bool
    hourly_rate: Optional[float] = 25.0
    rejection_reason: Optional[str] = None
    id_card_front: Optional[str] = None
    id_card_back: Optional[str] = None
    profile_photo: Optional[str] = None
    certificates: Optional[List[str]] = Field(default_factory=list)
    dob: Optional[str] = None
    nationality: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class WorkerApprovalPaginatedResponse(BasePaginatedResponse):
    pending_approvals: List[WorkerApprovalResponse]

class WorkerDetailResponse(BaseModel):
    id: str
    worker_id: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    worker_type: Optional[str] = "employee"
    position: Optional[str] = "Cleaner"
    base_location: Optional[str] = "Amsterdam-Centrum"
    profile_photo: Optional[str] = None
    id_card_front: Optional[str] = None
    id_card_back: Optional[str] = None
    certificates: Optional[List[str]] = Field(default_factory=list)
    dob: Optional[str] = None
    nationality: Optional[str] = None
    status: str = "active"
    account_status: str = "active"
    is_approved: bool = True
    approval_status: str = "approved"
    is_profile_completed: bool = True
    temp_password_changed: bool = True
    is_signup: bool = True
    last_login_at: Optional[datetime] = None
    total_shifts_count: int = 0
    completed_shifts_count: int = 0
    rating: float = 5.0
    hourly_rate: float = 25.0
    created_at: datetime
    updated_at: datetime

class WorkerCountResponse(BaseModel):
    total_workers: int = Field(..., json_schema_extra={"example": 15})
    employee_count: int = Field(..., json_schema_extra={"example": 10})
    freelancer_count: int = Field(..., json_schema_extra={"example": 5})

class WorkerListItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    worker_type: str
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = "active"
    is_signup: bool = True
    is_profile_completed: bool = True
    temp_password_changed: bool = True
    last_login_at: Optional[datetime] = None

class WorkerListPaginatedResponse(BasePaginatedResponse):
    workers: List[WorkerListItem] = Field(default_factory=list)


# --- Admin Worker Management Table & Modals Schemas (Image 1, 2, 3) ---
class AdminWorkerStatusUpdate(BaseModel):
    status: Literal["active", "suspended", "banned"]
    reason: Optional[str] = None

class AdminWorkerTableItem(BaseModel):
    worker_id: str
    full_name: str
    name: Optional[str] = None
    email: Optional[str] = None
    temporary_password: Optional[str] = None
    profile_photo: Optional[str] = None
    worker_type: str
    position: Optional[str] = None
    location: Optional[str] = None
    hourly_rate: float = 25.0
    languages: List[str] = Field(default_factory=list)
    hours_worked: str = "0h"
    hours_worked_numeric: float = 0.0
    status: str = "Active"
    account_status: str = "active"
    approval_status: str = "approved"
    is_active: bool = True
    is_profile_completed: bool = True
    temp_password_changed: bool = True
    is_signup: bool = True
    last_login_at: Optional[datetime] = None

    def __init__(self, **data):
        if "name" not in data and "full_name" in data:
            data["name"] = data["full_name"]
        elif "full_name" not in data and "name" in data:
            data["full_name"] = data["name"]
        super().__init__(**data)

class AdminWorkerTablePaginatedResponse(BaseModel):
    total_workers: int
    employees_count: int
    freelancers_count: int
    page: int
    limit: int
    has_more: bool = False
    workers: List[AdminWorkerTableItem] = Field(default_factory=list)

    def __init__(self, **data):
        if "has_more" not in data or data.get("has_more") is None:
            tw = data.get("total_workers", 0)
            p = data.get("page", 1)
            lim = data.get("limit", 10)
            data["has_more"] = bool((p * lim) < tw)
        super().__init__(**data)

class WorkerBulkImportResult(BaseModel):
    total_rows: int
    imported_count: int
    failed_count: int
    errors: List[str] = Field(default_factory=list)

