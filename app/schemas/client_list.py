from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any, Union, Literal
from datetime import datetime, date, timezone
from enum import Enum
from app.schemas.common import BasePaginatedResponse

# --- Enums ---
class ContactRoleEnum(str, Enum):
    operation_contact = "operation_contact"
    facility_manager = "facility_manager"
    operation_manager = "operation_manager"

class LocationTypeEnum(str, Enum):
    room = "room"
    office = "office"
    floor = "floor"

class ContractStatusEnum(str, Enum):
    active = "active"
    expiring = "expiring"
    expired = "expired"

# --- Contacts ---
class ContactCreate(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "John Smith"})
    role: str = Field(..., json_schema_extra={"example": "facility_manager"})
    email: EmailStr = Field(..., json_schema_extra={"example": "john.smith@client.com"})
    phone: str = Field(..., json_schema_extra={"example": "+1234567890"})

class ContactUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

class ContactResponse(BaseModel):
    id: str
    name: str
    role: str
    email: EmailStr
    phone: str
    created_at: str
    updated_at: str

# --- Locations ---
class LocationCreate(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "Main HQ Office"})
    type: str = Field(..., json_schema_extra={"example": "office"})
    address: str = Field(..., json_schema_extra={"example": "123 Business Way, Dhaka"})
    floor: int = Field(default=1, json_schema_extra={"example": 3})
    description: Optional[str] = Field(default="", json_schema_extra={"example": "Headquarters 3rd floor"})

class LocationUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    floor: Optional[int] = None
    description: Optional[str] = None

class LocationResponse(BaseModel):
    id: str
    client_id: Optional[str] = None
    name: str
    type: Optional[str] = "office"
    address: str
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "Netherlands"
    floor: Optional[int] = 1
    number_of_rooms: Optional[int] = 0
    rooms_count: Optional[int] = 0
    cleaning_plans_count: Optional[int] = 0
    description: Optional[str] = ""
    image_url: Optional[str] = None
    is_active: Optional[bool] = True
    notes: Optional[str] = None
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

# --- Contracts ---
class ContractCreate(BaseModel):
    client_name: str = Field(..., json_schema_extra={"example": "Betopia Group"})
    expiry_date: date = Field(..., json_schema_extra={"example": "2026-12-31"})

class ContractUpdate(BaseModel):
    client_name: Optional[str] = None
    expiry_date: Optional[date] = None
    status: Optional[str] = None

class ContractRenewRequest(BaseModel):
    expiry_date: date = Field(..., json_schema_extra={"example": "2027-12-31"})

class ContractResponse(BaseModel):
    id: str
    client_name: str
    status: str
    expiry_date: str
    pdf_url: Optional[str] = None
    created_at: str
    updated_at: str

# --- Cleaning Plans & Tasks ---

# Same lowercase 3-letter weekday convention already used for plan-level `working_days`
# elsewhere in this codebase (see resolve_plan_working_days / normalize_working_days).
VALID_WEEKDAY_ABBREVIATIONS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _validate_task_weekly_days(value: Optional[List[str]]) -> Optional[List[str]]:
    """Only meaningful when a task's frequency_type == 'weekly'; which weekdays it recurs on."""
    if value is None:
        return value
    normalized = [str(d).strip().lower() for d in value]
    invalid = sorted({d for d in normalized if d not in VALID_WEEKDAY_ABBREVIATIONS})
    if invalid:
        raise ValueError(
            f"Invalid weekday(s) in weekly_days: {invalid}. Must be one of {sorted(VALID_WEEKDAY_ABBREVIATIONS)}"
        )
    # De-dup while preserving first-seen order (mon..sun as picked in the UI).
    return list(dict.fromkeys(normalized))


def _validate_task_monthly_dates(value: Optional[List[int]]) -> Optional[List[int]]:
    """Only meaningful when a task's frequency_type == 'monthly'; which calendar dates it recurs on."""
    if value is None:
        return value
    invalid = sorted({d for d in value if not (1 <= int(d) <= 31)})
    if invalid:
        raise ValueError(f"Invalid date(s) of month in monthly_dates: {invalid}. Must be between 1 and 31")
    return sorted({int(d) for d in value})


def _validate_task_fixed_date(value: Optional[str]) -> Optional[str]:
    """Only meaningful when a task's frequency_type == 'fixed_date'; the single calendar date it occurs on."""
    if value is None:
        return value
    from datetime import date as _date
    try:
        _date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"Invalid fixed_date '{value}'. Must be an ISO date string, e.g. '2026-09-20'")
    return str(value).strip()


def _validate_task_duration_minutes(value: Optional[int]) -> Optional[int]:
    """Independent of scheduling — how long this specific task takes, in minutes."""
    if value is None:
        return value
    if int(value) <= 0:
        raise ValueError(f"Invalid duration_minutes '{value}'. Must be a positive number of minutes")
    return int(value)


class TaskPhotoCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., json_schema_extra={"example": "After Deep Floor scrubbing"})

class TaskPhotoResponse(BaseModel):
    id: str
    name: str

class CleaningTaskCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., json_schema_extra={"example": "Deep Floor Scrubbing"})
    frequency_type: Optional[str] = Field(default="every_visit", json_schema_extra={"example": "every_visit"}) # every_visit, weekly, monthly, yearly, fixed_date
    is_photo_req: Optional[bool] = Field(default=False, json_schema_extra={"example": True})
    photo: Optional[List[TaskPhotoCreate]] = Field(default_factory=list)
    # All four below are additive, optional fields — every existing field above is unchanged.
    # weekly_days / monthly_dates: only meaningful when frequency_type == "weekly" / "monthly".
    weekly_days: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["mon", "sat"]})
    monthly_dates: Optional[List[int]] = Field(default=None, json_schema_extra={"example": [1, 15]})
    # fixed_date: only meaningful when frequency_type == "fixed_date" (a one-time additional task).
    fixed_date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-09-20"})
    # duration_minutes: independent of scheduling — how long this specific task takes.
    # Used for "Additional Tasks" on a cleaning plan; counts toward the plan's total duration/end time.
    duration_minutes: Optional[int] = Field(default=None, json_schema_extra={"example": 10})
    # description: free-text detail about what the task involves — e.g. a client explaining
    # exactly what an additional task/extra service they're requesting needs.
    description: Optional[str] = Field(default=None, json_schema_extra={"example": "Windows on the east side have visible streaks after rain, please deep clean."})

    _check_weekly_days = field_validator("weekly_days")(_validate_task_weekly_days)
    _check_monthly_dates = field_validator("monthly_dates")(_validate_task_monthly_dates)
    _check_fixed_date = field_validator("fixed_date")(_validate_task_fixed_date)
    _check_duration_minutes = field_validator("duration_minutes")(_validate_task_duration_minutes)

class CleaningTaskUpdate(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    frequency_type: Optional[str] = None
    is_photo_req: Optional[bool] = None
    photo: Optional[List[TaskPhotoCreate]] = None
    weekly_days: Optional[List[str]] = None
    monthly_dates: Optional[List[int]] = None
    fixed_date: Optional[str] = None
    duration_minutes: Optional[int] = None
    description: Optional[str] = None

    _check_weekly_days = field_validator("weekly_days")(_validate_task_weekly_days)
    _check_monthly_dates = field_validator("monthly_dates")(_validate_task_monthly_dates)
    _check_fixed_date = field_validator("fixed_date")(_validate_task_fixed_date)
    _check_duration_minutes = field_validator("duration_minutes")(_validate_task_duration_minutes)

class CleaningTaskResponse(BaseModel):
    id: str
    name: str
    frequency_type: Optional[str] = "every_visit"
    is_photo_req: bool = False
    photo: List[TaskPhotoResponse] = Field(default_factory=list)
    total_photos_required: int = 0
    weekly_days: Optional[List[str]] = None
    monthly_dates: Optional[List[int]] = None
    fixed_date: Optional[str] = None
    duration_minutes: Optional[int] = None
    description: Optional[str] = None

class PendingAdditionalTaskResponse(CleaningTaskResponse):
    """
    A client-submitted "additional task" request awaiting manager review.
    Same shape as a real task (see CleaningTaskResponse) plus the approval
    workflow fields below. Stays in this list even after being reviewed
    (status flips to approved/rejected) so it doubles as an audit trail —
    an approved one is ALSO copied into the plan's real `additional_tasks`.
    """
    status: str = Field(default="pending", json_schema_extra={"example": "pending"})  # pending | approved | rejected
    requested_by: Optional[str] = None
    requested_by_name: Optional[str] = None
    requested_at: Optional[Union[str, datetime]] = None
    reviewed_by: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[Union[str, datetime]] = None
    rejection_reason: Optional[str] = None

class RejectPendingAdditionalTaskRequest(BaseModel):
    reason: Optional[str] = Field(default=None, json_schema_extra={"example": "Not covered under current contract scope"})

class CleaningPlanCreate(BaseModel):
    plan_name: str = Field(..., json_schema_extra={"example": "Daily Office Hygiene"})
    tasks: Optional[List[CleaningTaskCreate]] = Field(default_factory=list)

class CleaningPlanUpdate(BaseModel):
    plan_name: Optional[str] = None

class CleaningPlanResponse(BaseModel):
    id: str
    plan_name: str
    task_count: int
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)

class ContactPaginatedResponse(BasePaginatedResponse):
    contacts: List[ContactResponse]

class LocationPaginatedResponse(BasePaginatedResponse):
    locations: List[LocationResponse]

class ContractPaginatedResponse(BasePaginatedResponse):
    contracts: List[ContractResponse]

class CleaningPlanPaginatedResponse(BasePaginatedResponse):
    cleaning_plans: List[CleaningPlanResponse]

# --- Reports ---
class ReportResponse(BaseModel):
    id: str
    title: str
    generated_at: str
    summary: Dict[str, Any]
    pdf_url: Optional[str] = None

class ReportPaginatedResponse(BasePaginatedResponse):
    reports: List[ReportResponse]

# --- Overview Summary ---
class ClientOverviewResponse(BaseModel):
    company_name: str
    industry: str
    status: str
    contract_expiry: Optional[str] = None
    locations_count: int
    contacts_count: int
    active_tasks_count: int
    contract_status: str

# --- Client List Base ---
class ClientListCreate(BaseModel):
    company_name: str = Field(..., json_schema_extra={"example": "Prince Group"})
    industry: str = Field(..., json_schema_extra={"example": "Polytechnic Institute"})
    primary_contact_name: str = Field(..., json_schema_extra={"example": "Nasir Alam"})
    email: EmailStr = Field(..., json_schema_extra={"example": "c3@yopmail.com"})
    phone: str = Field(..., json_schema_extra={"example": "+8801318531875"})
    license_expiration_date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-08-23"})
    # Manager-created clients (this schema) are active immediately — their underlying
    # user account is created already approved/active, so this must not default to
    # "pending" (that status is reserved for self-signup clients awaiting approval).
    status: Optional[str] = Field(default="active", json_schema_extra={"example": "active"})

    model_config = {
        "json_schema_extra": {
            "example": {
                "company_name": "Prince Group",
                "industry": "Polytechnic Institute",
                "primary_contact_name": "Nasir Alam",
                "email": "c3@yopmail.com",
                "phone": "+8801318531875",
                "license_expiration_date": "2026-08-23"
            }
        }
    }

class ClientListUpdate(BaseModel):
    company_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Betopia Group"})
    industry: Optional[str] = Field(default=None, json_schema_extra={"example": "Cleaning Services"})
    primary_contact_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Mahfuz Alam"})
    email: Optional[EmailStr] = Field(default=None, json_schema_extra={"example": "c1@yopmail.com"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801318532935"})
    status: Optional[str] = Field(default=None, json_schema_extra={"example": "active"})
    is_signup: Optional[bool] = Field(default=None, json_schema_extra={"example": True})

class AdminInfo(BaseModel):
    id: str
    name: str
    profile_picture: Optional[str] = None

class ClientListResponse(BaseModel):
    id: str
    _id: str
    admin: AdminInfo
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    name: Optional[str] = None
    email: EmailStr
    phone: str
    is_signup: bool
    temporary_password: Optional[str] = None
    locations_count: int = 0
    contract_status: str = "no_contract"
    license_expiration_date: Optional[str] = None
    locations: List[LocationResponse] = Field(default_factory=list)
    contacts: List[ContactResponse] = Field(default_factory=list)
    contracts: List[ContractResponse] = Field(default_factory=list)
    cleaning_plans: List[CleaningPlanResponse] = Field(default_factory=list)
    reports: List[ReportResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "name" not in data and "primary_contact_name" in data:
            data["name"] = data["primary_contact_name"]
        elif "primary_contact_name" not in data and "name" in data:
            data["primary_contact_name"] = data["name"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        from_attributes = True

class ClientListPaginatedResponse(BasePaginatedResponse):
    clients: List[ClientListResponse]

class ClientGridDropdownItem(BaseModel):
    id: str
    primary_contact_name: str
    company_name: str
    is_signup: bool = False

class ClientGridDropdownPaginatedResponse(BasePaginatedResponse):
    clients: List[ClientGridDropdownItem]

class ClientOverviewItemResponse(BaseModel):
    id: str
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    email: EmailStr
    phone: str
    is_signup: bool = False
    locations_count: int = 0
    contract_status: str = "no_contract"
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ClientOverviewListPaginatedResponse(BasePaginatedResponse):
    clients: List[ClientOverviewItemResponse]

class ClientOverviewDetailResponse(BaseModel):
    id: str
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    email: EmailStr
    phone: str
    is_signup: bool = False
    locations_count: int = 0
    contract_status: str = "no_contract"
    contract_expiry_date: Optional[str] = None
    contacts_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class GlobalLocationResponse(BaseModel):
    id: str = ""
    client_id: str = ""
    company_name: str = ""
    name: str = ""
    type: str = "office"
    address: str = ""
    floor: int = 1
    number_of_rooms: int = 0
    description: str = ""
    image_url: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    client_company_name: Optional[str] = None
    total_rooms_count: Optional[int] = 0
    cleaning_plans_count: Optional[int] = 0

    def __init__(self, **data):
        if "location_id" in data and not data.get("id"):
            data["id"] = data["location_id"]
        if "location_name" in data and not data.get("name"):
            data["name"] = data["location_name"]
        if "client_company_name" in data and not data.get("company_name"):
            data["company_name"] = data["client_company_name"]
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        if isinstance(data.get("updated_at"), datetime):
            data["updated_at"] = data["updated_at"].isoformat()
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

class GlobalLocationPaginatedResponse(BasePaginatedResponse):
    locations: List[GlobalLocationResponse]

class LocationDropdownItemResponse(BaseModel):
    id: str = ""
    name: str = ""
    client_id: str = ""
    company_name: str = ""
    address: Optional[str] = None
    total_rooms_count: Optional[int] = 0

    def __init__(self, **data):
        if "location_id" in data and not data.get("id"):
            data["id"] = data["location_id"]
        if "location_name" in data and not data.get("name"):
            data["name"] = data["location_name"]
        if "client_name" in data and not data.get("company_name"):
            data["company_name"] = data["client_name"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "ignore"

class LocationDropdownPaginatedResponse(BasePaginatedResponse):
    locations: List[LocationDropdownItemResponse]

class RoomDropdownItemResponse(BaseModel):
    id: str = ""
    room_name: str = ""
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    floor: Optional[int] = 1
    cleaning_type: Optional[str] = "standard"
    duration: Optional[int] = 30
    monthly_cleaning_frequency: int = Field(default=0, description="Dynamic frequency: how many times a month this room is cleaned.")

    def __init__(self, **data):
        if "room_id" in data and not data.get("id"):
            data["id"] = data["room_id"]
        if "name" in data and not data.get("room_name"):
            data["room_name"] = data["name"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "ignore"

class RoomDropdownPaginatedResponse(BasePaginatedResponse):
    rooms: List[RoomDropdownItemResponse]

# --- Required Photo Schemas ---
class RequiredPhotoCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., json_schema_extra={"example": "Before cleaning photo"})
    frequency_type: Optional[str] = Field(default="every_visit", json_schema_extra={"example": "every_visit"}) # every_visit, weekly, monthly, yearly

class RequiredPhotoResponse(BaseModel):
    id: str
    name: str
    frequency_type: Optional[str] = "every_visit"

# --- Room Management Schemas ---
class RoomCreate(BaseModel):
    room_name: str = Field(..., json_schema_extra={"example": "Ware House"})
    room_type: str = Field(..., json_schema_extra={"example": "suite"})  # standard, deluxe, suite, junior_suite
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "location_id_here"})
    floor: int = Field(default=1, json_schema_extra={"example": 1})
    duration: int = Field(default=90, json_schema_extra={"example": 90})  # minutes
    monthly_cleaning_frequency: int = Field(default=0, json_schema_extra={"example": 0})
    clean_type: str = Field(default="standard", json_schema_extra={"example": "standard"})  # standard, premium
    tasks: Optional[List[CleaningTaskCreate]] = Field(default_factory=list)
    required_photos: Optional[List[RequiredPhotoCreate]] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "clean_type": "standard",
                "duration": 90,
                "monthly_cleaning_frequency": 0,
                "room_name": "Ware House",
                "room_type": "suite",
                "tasks": [
                    {
                        "frequency_type": "every_visit",
                        "name": "Deep Floor Scrubbing",
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "after Deep Floor scrubbing"
                            },
                            {
                                "name": "Before Deep Floor scrubbing"
                            }
                        ]
                    },
                    {
                        "frequency_type": "weekly",
                        "name": "Clean the ceiling",
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "after Clean the ceiling"
                            },
                            {
                                "name": "Clean the ceiling"
                            }
                        ]
                    }
                ]
            }
        }
    }

class RoomUpdate(BaseModel):
    room_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Ware House Updated"})
    room_type: Optional[str] = Field(default=None, json_schema_extra={"example": "suite"})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "loc_099832da9e"})
    floor: Optional[int] = Field(default=None, json_schema_extra={"example": 1})
    duration: Optional[int] = Field(default=None, json_schema_extra={"example": 90})
    monthly_cleaning_frequency: Optional[int] = Field(default=None, json_schema_extra={"example": 4})
    clean_type: Optional[str] = Field(default=None, json_schema_extra={"example": "standard"})
    required_photos: Optional[List[RequiredPhotoCreate]] = None
    tasks: Optional[List[CleaningTaskCreate]] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "room_name": "Ware House Updated",
                "room_type": "suite",
                "duration": 90,
                "floor": 1,
                "monthly_cleaning_frequency": 0,
                "clean_type": "standard",
                "tasks": [
                    {
                        "id": "b386fe2b",
                        "name": "Deep Floor Scrubbing",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "photo": [
                            {
                                "id": "9ad40712",
                                "name": "after Deep Floor scrubbing"
                            },
                            {
                                "name": "Before Deep Floor scrubbing"
                            }
                        ]
                    }
                ]
            }
        }
    }

class RoomResponse(BaseModel):
    id: str
    room_name: str
    room_type: str = "standard"
    client_id: Optional[str] = ""
    company_name: Optional[str] = ""
    location_id: Optional[str] = ""
    location_name: Optional[str] = ""
    floor: int = 1
    duration: int = 30
    monthly_cleaning_frequency: int = 0
    required_photos: List[RequiredPhotoResponse] = Field(default_factory=list)
    photo_number: int = 0
    total_photos_required: int = 0
    task_number: int = 0
    clean_type: str = "standard"
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "room_91eb4b2e51",
                "room_name": "Ware House",
                "room_type": "suite",
                "client_id": "cli_46f0aebbeb",
                "company_name": "Kafa Automation",
                "location_id": "loc_099832da9e",
                "location_name": "Main HQ Warehouse",
                "floor": 1,
                "duration": 90,
                "monthly_cleaning_frequency": 0,
                "photo_number": 4,
                "total_photos_required": 4,
                "task_number": 2,
                "clean_type": "standard",
                "tasks": [
                    {
                        "id": "b386fe2b",
                        "name": "Deep Floor Scrubbing",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "total_photos_required": 2,
                        "photo": [
                            {
                                "id": "9ad40712",
                                "name": "after Deep Floor scrubbing"
                            },
                            {
                                "id": "00176973",
                                "name": "Before Deep Floor scrubbing"
                            }
                        ]
                    },
                    {
                        "id": "6d8c84c6",
                        "name": "Clean the ceiling",
                        "frequency_type": "weekly",
                        "is_photo_req": True,
                        "total_photos_required": 2,
                        "photo": [
                            {
                                "id": "f8f37b0d",
                                "name": "after Clean the ceiling"
                            },
                            {
                                "id": "788a3b20",
                                "name": "Clean the ceiling"
                            }
                        ]
                    }
                ],
                "required_photos": [
                    {
                        "id": "9ad40712",
                        "name": "after Deep Floor scrubbing",
                        "frequency_type": "every_visit"
                    },
                    {
                        "id": "00176973",
                        "name": "Before Deep Floor scrubbing",
                        "frequency_type": "every_visit"
                    },
                    {
                        "id": "f8f37b0d",
                        "name": "after Clean the ceiling",
                        "frequency_type": "weekly"
                    },
                    {
                        "id": "788a3b20",
                        "name": "Clean the ceiling",
                        "frequency_type": "weekly"
                    }
                ],
                "created_at": "2026-08-17T04:33:30.656000Z",
                "updated_at": "2026-08-17T04:33:30.656000Z"
            }
        }
    }

class RoomPaginatedResponse(BasePaginatedResponse):
    rooms: List[RoomResponse]

class AdminRoomLocationDropdownItem(BaseModel):
    id: str
    name: str
    total_rooms: int

class AdminRoomLocationDropdownResponse(BaseModel):
    locations: List[AdminRoomLocationDropdownItem]

# --- Admin Cleaning Plan Management Schemas ---
class CleaningPlanRoomInput(BaseModel):
    room_id: str = Field(..., json_schema_extra={"example": "2ac45be9-9443-4378-8286-152ce553a90b"})
    additional_tasks: Optional[List[CleaningTaskCreate]] = Field(
        default=None,
        json_schema_extra={"example": [{"name": "Deep floor scrubbing"}, {"name": "Sanitize executive desk"}]}
    )
    additional_photo_requirements: Optional[List[RequiredPhotoCreate]] = Field(
        default=None,
        json_schema_extra={"example": [{"name": "Before cleaning photo"}, {"name": "After sanitization photo"}]}
    )
    custom_room_name: Optional[str] = Field(default=None, json_schema_extra={"example": "VIP Executive Suite 301"})
    duration: Optional[int] = Field(default=None, json_schema_extra={"example": 45})
    clean_type: Optional[str] = Field(default=None, json_schema_extra={"example": "premium"})
    tasks: Optional[List[CleaningTaskCreate]] = Field(default=None)
    required_photos: Optional[List[RequiredPhotoCreate]] = Field(default=None)

class GlobalCleaningPlanCreate(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "Daily Enterprise Hygiene & Sanitization Plan"})
    client_id: str = Field(..., json_schema_extra={"example": "6a606f9503747d147cf27abc"})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "abb9d8b2-0ba6-457d-9553-c652e2d07cde"})
    rooms: List[CleaningPlanRoomInput] = Field(..., json_schema_extra={"example": [{"room_id": "2ac45be9-9443-4378-8286-152ce553a90b"}]})

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Daily Enterprise Hygiene & Sanitization Plan",
                "client_id": "6a606f9503747d147cf27abc",
                "location_id": "abb9d8b2-0ba6-457d-9553-c652e2d07cde",
                "rooms": [
                    {
                        "room_id": "2ac45be9-9443-4378-8286-152ce553a90b",
                        "custom_room_name": "Executive Suite 301 (Aqua Tower)",
                        "additional_tasks": [
                            {"name": "Sanitize executive desk"}
                        ],
                        "additional_photo_requirements": [
                            {"name": "After desk sanitization photo"}
                        ]
                    },
                    {
                        "room_id": "d3709251-b6d7-4963-9ddb-c81579380a19",
                        "custom_room_name": "Main Clinic Waiting Room",
                        "additional_tasks": [
                            {"name": "Disinfect waiting chairs"}
                        ],
                        "additional_photo_requirements": [
                            {"name": "Disinfected seating area photo"}
                        ]
                    }
                ]
            }
        }

class GlobalCleaningPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated Hygiene Plan"})
    client_id: Optional[str] = Field(default=None, json_schema_extra={"example": "6a606f9503747d147cf27abc"})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "abb9d8b2-0ba6-457d-9553-c652e2d07cde"})
    rooms: Optional[List[CleaningPlanRoomInput]] = Field(default=None)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Updated Daily Hygiene Plan",
                "rooms": [
                    {
                        "room_id": "2ac45be9-9443-4378-8286-152ce553a90b",
                        "custom_room_name": "Renovated Executive Suite 301",
                        "duration": 50,
                        "required_photos": [
                            {"name": "Updated carpet photo"}
                        ]
                    }
                ]
            }
        }

class CleaningPlanRoomDetail(BaseModel):
    room_id: str
    custom_room_name: str
    room_type: str
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    floor: Optional[int] = 1
    clean_type: str
    duration: int
    required_photos: List[RequiredPhotoResponse] = Field(default_factory=list)
    photo_number: int = 0
    task_number: int = 0
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)

class GlobalCleaningPlanResponse(BaseModel):
    id: str
    name: str
    client_id: str
    client_name: str
    location_id: Optional[str] = None
    client_location_name: Optional[str] = None
    rooms: List[CleaningPlanRoomDetail] = Field(default_factory=list)
    total_duration: int
    total_photo_required: int
    total_tasks_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class CleaningPlanRoomSummary(BaseModel):
    room_id: str
    custom_room_name: str
    room_type: str

class GlobalCleaningPlanListItemResponse(BaseModel):
    id: str = ""
    name: str = ""
    client_id: str = ""
    client_name: str = ""
    location_id: Optional[str] = None
    client_location_name: Optional[str] = None
    location_name: Optional[str] = None
    rooms: List[CleaningPlanRoomSummary] = Field(default_factory=list)
    rooms_count: int = 0
    total_duration: int = 0
    total_photo_required: int = 0
    total_tasks_count: int = 0
    created_at: Any = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Any = Field(default_factory=lambda: datetime.now(timezone.utc))
    plan_id: Optional[str] = None
    title: Optional[str] = None
    total_photo_requirements: Optional[int] = None
    estimated_duration_hours: Optional[float] = None

    def __init__(self, **data):
        if "plan_id" in data and not data.get("id"):
            data["id"] = data["plan_id"]
        if "title" in data and not data.get("name"):
            data["name"] = data["title"]
        if "location_name" in data and not data.get("client_location_name"):
            data["client_location_name"] = data["location_name"]
        if "total_photo_requirements" in data and not data.get("total_photo_required"):
            data["total_photo_required"] = data["total_photo_requirements"]
        if "estimated_duration_hours" in data and not data.get("total_duration"):
            data["total_duration"] = int(data.get("estimated_duration_hours", 0) * 60)
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

class GlobalCleaningPlanPaginatedResponse(BasePaginatedResponse):
    cleaning_plans: List[GlobalCleaningPlanListItemResponse] = Field(default_factory=list)
    plans: Optional[List[GlobalCleaningPlanListItemResponse]] = None

    def __init__(self, **data):
        if "plans" in data and not data.get("cleaning_plans"):
            data["cleaning_plans"] = data["plans"]
        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"


# --- Client Detailed Dashboard Schemas (Image 2 - Image 5) ---
class ClientDashboardOverviewResponse(BaseModel):
    client_id: str
    company_name: str
    industry: str
    status: str
    subtitle_contract_status: str
    contract_status: str
    contract_expiry_date: Optional[str] = None
    contract_expiry_formatted: Optional[str] = None
    locations_count: int = 0
    contacts_count: int = 0
    active_tasks_count: int = 0

class ClientContactItem(BaseModel):
    id: str
    name: str
    role: str
    email: EmailStr
    phone: str

class ClientContactPaginatedResponse(BasePaginatedResponse):
    contacts: List[ClientContactItem] = Field(default_factory=list)

class ClientLocationItem(BaseModel):
    id: str
    name: str
    address: str

class ClientLocationPaginatedResponse(BasePaginatedResponse):
    locations: List[ClientLocationItem] = Field(default_factory=list)

class ClientContractDetailsResponse(BaseModel):
    client_id: str
    company_name: str
    status: str
    expiry_date: Optional[str] = None
    expiry_date_formatted: Optional[str] = None
    pdf_url: Optional[str] = None


# --- Client Cleaning Plan (Task Builder) & Reports Schemas (Image 1 & Image 2) ---
class ClientCleaningPlanTaskItem(BaseModel):
    task_id: str
    name: str
    is_completed: bool = False

class ClientCleaningPlanResponse(BaseModel):
    client_id: str
    company_name: str
    plan_name: str
    tasks: List[ClientCleaningPlanTaskItem] = Field(default_factory=list)
    assigned_location_ids: List[str] = Field(default_factory=list)

class ClientCleaningPlanUpdate(BaseModel):
    plan_name: Optional[str] = "Standard Cleaning Checklist"
    tasks: List[str] = Field(default_factory=list)

class AssignLocationRequest(BaseModel):
    location_id: str

class ClientReportItem(BaseModel):
    id: str
    title: str
    date_formatted: str
    date_iso: str
    status: str
    download_url: Optional[str] = None

class ClientReportsListResponse(BaseModel):
    total_count: int
    reports: List[ClientReportItem] = Field(default_factory=list)

class SendReportEmailRequest(BaseModel):
    report_id: Optional[str] = None
    email: Optional[EmailStr] = None


# --- Admin Location Management Schemas (Image 1 - Image 5) ---
class AdminLocationCreate(BaseModel):
    name: str
    client_id: str
    address: str
    number_of_floors: int = 1
    number_of_rooms: int = 1
    required_hours_per_month: float = 0.0
    assigned_worker_ids: List[str] = Field(default_factory=list)

class AdminLocationGridItem(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    client_id: str
    company_name: Optional[str] = None
    client_company_name: Optional[str] = None
    address: str
    floors: int = 1
    rooms: int = 0
    required_hours_label: str = "0h"
    required_hours_numeric: float = 0.0
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "id" in data and not data.get("location_id"):
            data["location_id"] = data["id"]
        elif "location_id" in data and not data.get("id"):
            data["id"] = data["location_id"]

        if "name" in data and not data.get("location_name"):
            data["location_name"] = data["name"]
        elif "location_name" in data and not data.get("name"):
            data["name"] = data["location_name"]

        if "company_name" in data and not data.get("client_company_name"):
            data["client_company_name"] = data["company_name"]
        elif "client_company_name" in data and not data.get("company_name"):
            data["company_name"] = data["client_company_name"]

        super().__init__(**data)

    class Config:
        populate_by_name = True
        extra = "allow"

class AdminLocationGridPaginatedResponse(BasePaginatedResponse):
    locations: List[AdminLocationGridItem] = Field(default_factory=list)

class AssignedEmployeeItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    status: str = "Assigned"

class LocationDrawerOverviewResponse(BaseModel):
    location_id: str
    location_code: str
    location_name: str
    client_name: str
    subtitle: str
    address: str
    floors: int
    rooms: int
    required_hours_month: str
    assigned_employees: List[AssignedEmployeeItem] = Field(default_factory=list)
    assigned_employees_count: int = 0
    coverage_label: str

class LocationDrawerRoomItem(BaseModel):
    room_id: str
    title: str
    floor: int
    cleaning_type_label: str
    status: str = "Active"

class LocationDrawerRoomsResponse(BaseModel):
    location_id: str
    location_name: str
    total_rooms_count: int
    rooms: List[LocationDrawerRoomItem] = Field(default_factory=list)

class LocationBulkImportResult(BaseModel):
    total_rows: int
    imported_count: int
    failed_count: int
    errors: List[str] = Field(default_factory=list)

class ClientBulkImportResult(BaseModel):
    total_rows: int
    imported_count: int
    failed_count: int
    errors: List[str] = Field(default_factory=list)


# --- Admin Global Rooms & Global Cleaning Plans Schemas (Image 1 - Image 5) ---
class AdminRoomCreate(BaseModel):
    name: str
    room_type: str = "Standard"
    location_id: str
    floor: int = 1
    est_cleaning_duration_minutes: int = 45
    monthly_cleaning_frequency: int = 0
    required_photos_count: int = 4
    tasks_count: int = 12
    cleaning_plan_name: Optional[str] = "Standard Clean"

class AdminRoomGridItem(BaseModel):
    room_id: str
    room_name: str
    room_type: str = "standard"
    client_id: Optional[str] = ""
    company_name: Optional[str] = ""
    location_id: Optional[str] = ""
    location_name: Optional[str] = ""
    monthly_cleaning_frequency: int = 0
    photo_number: int = 0
    total_photos_required: int = 0
    task_number: int = 0
    clean_type: str = "standard"
    updated_at: Union[str, datetime]

    class Config:
        populate_by_name = True
        from_attributes = True

class AdminRoomGridPaginatedResponse(BasePaginatedResponse):
    rooms: List[AdminRoomGridItem] = Field(default_factory=list)

class RoomDrawerDetailResponse(BaseModel):
    room_id: str
    room_code: str
    room_name: str
    room_type: str
    floor_label: str
    location_name: str
    cleaning_plan_name: str
    duration_minutes: int
    monthly_cleaning_frequency: int = Field(default=0, description="Dynamic frequency: how many times a month this room is cleaned.")
    required_photos_count: int
    tasks_count: int

class AdminCleaningPlanCreate(BaseModel):
    plan_name: str
    client_id: str
    location_id: str
    duration_minutes: int = 45
    required_photos_count: int = 4
    checklist_tasks: List[str] = Field(default_factory=list)
    photo_requirements: List[str] = Field(default_factory=list)

class AdminCleaningPlanGridItem(BaseModel):
    plan_id: str
    plan_code: str
    plan_name: str
    client_company_name: str
    location_name: str
    room_pills: List[str] = Field(default_factory=list)
    duration_minutes: int = 45
    required_photos_count: int = 4
    tasks_count: int = 12

class AdminCleaningPlanGridPaginatedResponse(BasePaginatedResponse):
    plans: List[AdminCleaningPlanGridItem] = Field(default_factory=list)

# --- Cleaning Plan Dropdown Schemas ---
class CleaningPlanRoomDropdownItem(BaseModel):
    room_id: str
    room_name: str
    room_type: str = "standard"
    photo_number: int = 0
    task_number: int = 0

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "room_id": "room_91eb4b2e51",
                "room_name": "Ware House",
                "room_type": "suite",
                "photo_number": 4,
                "task_number": 3
            }
        }
    }

class CleaningPlanRoomDropdownPaginatedResponse(BasePaginatedResponse):
    rooms: List[CleaningPlanRoomDropdownItem] = Field(default_factory=list)

# --- Manager Cleaning Plan CRUD Schemas ---

class CleaningPlanRoomDetail(BaseModel):
    room_id: str
    room_name: str
    room_type: str = "standard"
    floor: int = 1
    duration: int = 30
    monthly_cleaning_frequency: int = 0
    clean_type: str = "standard"
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    photo_number: int = 0
    total_photos_required: int = 0
    task_number: int = 0

class CleaningPlanWorkerDetail(BaseModel):
    worker_id: str
    name: str
    email: Optional[str] = None
    role: Optional[str] = "worker"
    worker_type: Optional[str] = "employee"
    position: Optional[str] = "normal"  # teamleader, co_leader, normal
    phone: Optional[str] = None
    profile_photo: Optional[str] = None

class CleaningPlanClientDetail(BaseModel):
    client_id: str
    company_name: str
    primary_contact_name: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    rooms_count: int = 0

class CleaningPlanManagerDetail(BaseModel):
    manager_id: str
    name: str
    email: str
    role: str = "manager"
    phone: Optional[str] = None
    profile_photo: Optional[str] = None

class ManagerCleaningPlanCreate(BaseModel):
    title: str = Field(..., json_schema_extra={"example": "Kafa Automation Cleaning plan & Betopia Group"})
    room_ids: List[str] = Field(default_factory=list, json_schema_extra={"example": ["room_a366ecf17c", "room_848a13ff0c"]})
    date: Optional[str] = Field(default="2026-08-17", json_schema_extra={"example": "2026-08-17"})
    start_time: Optional[str] = Field(default="08:00 AM", json_schema_extra={"example": "08:00 AM"})
    repeat_shift: Optional[str] = Field(default="Standard working week", json_schema_extra={"example": "Monthly"})
    repeat_until: Optional[str] = Field(default="2026-12-31", json_schema_extra={"example": "2026-12-31"})
    working_days: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["sun"]})
    shift_notes: Optional[str] = Field(default="", json_schema_extra={"example": "Monthly Sunday deep cleaning"})
    description: Optional[str] = Field(default="", json_schema_extra={"example": "Love to Wash"})
    additional_tasks: Optional[List[CleaningTaskCreate]] = Field(default_factory=list)
    duration_minutes: Optional[int] = Field(default=None, json_schema_extra={"example": 330})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "loc_db28f5a3f6"})
    frequency_type: Optional[str] = Field(default=None, json_schema_extra={"example": "monthly"})
    timezone: Optional[str] = Field(default="Europe/Amsterdam", json_schema_extra={"example": "Europe/Amsterdam"})

    def __init__(self, **data):
        if "tasks" in data and ("additional_tasks" not in data or not data["additional_tasks"]):
            data["additional_tasks"] = data["tasks"]
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Kafa Automation Cleaning plan & Betopia Group",
                "room_ids": [
                    "room_a366ecf17c",
                    "room_848a13ff0c"
                ],
                "date": "2026-08-17",
                "start_time": "08:00 AM",
                "repeat_shift": "Monthly",
                "repeat_until": "2026-12-31",
                "working_days": [
                    "sun"
                ],
                "shift_notes": "Monthly Sunday deep cleaning",
                "timezone": "Europe/Amsterdam",
                "additional_tasks": [
                    {
                        "name": "Deep Floor Scrubbing",
                        "frequency_type": "every_visit",
                        "duration_minutes": 15,
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "After Deep Floor scrubbing"
                            },
                            {
                                "name": "Before Deep Floor scrubbing"
                            }
                        ]
                    },
                    {
                        "name": "Clean the ceiling",
                        "frequency_type": "weekly",
                        "weekly_days": ["mon", "thu"],
                        "duration_minutes": 20,
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "After Clean the ceiling"
                            },
                            {
                                "name": "Before Clean the ceiling"
                            }
                        ]
                    },
                    {
                        "name": "Task 1",
                        "frequency_type": "fixed_date",
                        "fixed_date": "2026-10-09",
                        "duration_minutes": 10,
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "proof photo"
                            }
                        ]
                    }
                ]
            }
        }
    }

class ManagerCleaningPlanUpdate(BaseModel):
    title: Optional[str] = None
    room_ids: Optional[List[str]] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    repeat_shift: Optional[str] = None
    repeat_until: Optional[str] = None
    working_days: Optional[List[str]] = None
    shift_notes: Optional[str] = None
    worker_ids: Optional[List[str]] = None
    duration_minutes: Optional[int] = None
    description: Optional[str] = None
    additional_tasks: Optional[List[CleaningTaskCreate]] = None
    location_id: Optional[str] = None
    frequency_type: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None

    def __init__(self, **data):
        if "tasks" in data and ("additional_tasks" not in data or not data["additional_tasks"]):
            data["additional_tasks"] = data["tasks"]
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Kafa Automation Cleaning plan & Betopia Group",
                "shift_notes": "Updated monthly deep cleaning instructions",
                "additional_tasks": [
                    {
                        "name": "Deep Floor Scrubbing",
                        "frequency_type": "every_visit",
                        "duration_minutes": 15,
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "After Deep Floor scrubbing"
                            }
                        ]
                    },
                    {
                        "name": "Task 1",
                        "frequency_type": "fixed_date",
                        "fixed_date": "2026-10-09",
                        "duration_minutes": 10,
                        "is_photo_req": True,
                        "photo": [
                            {
                                "name": "proof photo"
                            }
                        ]
                    }
                ]
            }
        }
    }

class ManagerCleaningPlanDetailResponse(BaseModel):
    id: str
    title: str
    shift_notes: Optional[str] = ""
    client_id: Optional[str] = None
    company_name: Optional[str] = None
    clients_count: int = 0
    clients: List[CleaningPlanClientDetail] = Field(default_factory=list)
    manager: Optional[CleaningPlanManagerDetail] = None
    # Additive — was accepted on create/update and stored on the plan doc, but never
    # actually returned. Manager-side detail now surfaces it, matching the client side.
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    room_ids: List[str] = Field(default_factory=list)
    rooms: List[CleaningPlanRoomDetail] = Field(default_factory=list)
    rooms_count: int = 0
    worker_ids: List[str] = Field(default_factory=list)
    workers: List[CleaningPlanWorkerDetail] = Field(default_factory=list)
    workers_count: int = 0
    additional_tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    # Additive — client-submitted additional-task requests awaiting manager approve/reject.
    # Kept even after review (status flips to approved/rejected) as an audit trail.
    pending_additional_tasks: List[PendingAdditionalTaskResponse] = Field(default_factory=list)
    total_tasks_count: int = 0
    total_photos_count: int = 0
    date: str = "2026-08-17"
    start_time: str = "08:00 AM"
    end_time: str = "01:30 PM"
    duration_minutes: int = 60
    repeat_shift: str = "Does not repeat"
    repeat_until: Optional[str] = None
    working_days: List[str] = Field(default_factory=list)
    timezone: Optional[str] = "Europe/Amsterdam"
    status: Optional[str] = "draft"
    is_active: bool = True
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

class ManagerCleaningPlanListItemResponse(BaseModel):
    id: str
    title: str
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    clients_count: int = 0
    client_names: List[str] = Field(default_factory=list)
    rooms_count: int = 0
    room_names: List[str] = Field(default_factory=list)
    workers_count: int = 0
    worker_names: List[str] = Field(default_factory=list)
    total_tasks_count: int = 0
    total_photos_count: int = 0
    date: str = "2026-08-17"
    start_time: str = "08:00 AM"
    end_time: str = "01:30 PM"
    duration_minutes: int = 60
    repeat_shift: str = "Does not repeat"
    repeat_until: Optional[str] = None
    working_days: List[str] = Field(default_factory=list)
    timezone: Optional[str] = "Europe/Amsterdam"
    status: Optional[str] = "draft"
    is_active: bool = True
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

class ManagerCleaningPlanPaginatedResponse(BasePaginatedResponse):
    plans: List[ManagerCleaningPlanListItemResponse] = Field(default_factory=list)

class LocationDrawerCleaningPlanItem(BaseModel):
    plan_id: str
    title: str
    subtitle: str
    status: str = "Active"

class LocationDrawerCleaningPlansResponse(BaseModel):
    location_id: str
    location_name: str
    total_plans_count: int
    plans: List[LocationDrawerCleaningPlanItem] = Field(default_factory=list)


# --- Global Quality Control Reports Schemas (Image Mockup) ---
class ShiftTrendDataPoint(BaseModel):
    label: str
    count: int

class PhotoQualityDistributionData(BaseModel):
    approved: int = 67
    pending: int = 12
    rejected: int = 8

class QualityControlReportResponse(BaseModel):
    timeframe: Literal["week", "month", "quarter", "year"] = "month"
    total_shifts: int = 1245
    total_photos_approved: int = 67
    escalations_count: int = 23
    shift_trends: List[ShiftTrendDataPoint] = Field(default_factory=list)
    photo_quality_distribution: PhotoQualityDistributionData
    pdf_download_url: str = "/admin/reports/quality-control/pdf?timeframe=month"


class CleaningPlanWorkerDropdownItem(BaseModel):
    worker_id: str
    name: str
    profile_photo: Optional[str] = None
    worker_type: str = "employee"
    position: Optional[str] = "Cleaner"
    email: Optional[str] = None
    phone: Optional[str] = None
    is_available: bool = True
    unavailable_reason: Optional[str] = None
    avg_daily_work_minutes: int = 0
    total_shifts_this_month: int = 0
    total_work_minutes_this_month: int = 0
    formatted_avg_work: str = "0 mins/day"
    last_work_end_time: Optional[str] = None
    last_work_ended_ago: Optional[str] = None
    minutes_since_last_work: Optional[int] = None

class CleaningPlanWorkerDropdownPaginatedResponse(BasePaginatedResponse):
    plan_id: str
    plan_date: str
    plan_time_window: str
    workers: List[CleaningPlanWorkerDropdownItem] = Field(default_factory=list)

class WorkerAssignmentItem(BaseModel):
    worker_id: str = Field(..., json_schema_extra={"example": "w_101"})
    position: Optional[str] = Field(default="normal", json_schema_extra={"example": "teamleader"})  # teamleader, co_leader, normal

class AssignWorkersToCleaningPlanRequest(BaseModel):
    workers: List[WorkerAssignmentItem] = Field(
        ...,
        min_length=1,
        json_schema_extra={
            "example": [
                {"worker_id": "w_101", "position": "teamleader"},
                {"worker_id": "w_102", "position": "co_leader"},
                {"worker_id": "w_103", "position": "normal"}
            ]
        }
    )
    action: Optional[str] = Field(
        default="append",
        json_schema_extra={"example": "append"}
    )  # "append" (default: merges without removing existing), or "replace"




