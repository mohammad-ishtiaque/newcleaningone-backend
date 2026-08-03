from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any, Union, Literal
from datetime import datetime, date
from enum import Enum

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
    number_of_rooms: int = Field(default=1, json_schema_extra={"example": 10})
    description: Optional[str] = Field(default="", json_schema_extra={"example": "Headquarters 3rd floor"})

class LocationUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    floor: Optional[int] = None
    number_of_rooms: Optional[int] = None
    description: Optional[str] = None

class LocationResponse(BaseModel):
    id: str
    name: str
    type: Optional[str] = "office"
    address: str
    floor: int = 1
    number_of_rooms: int = 1
    description: Optional[str] = ""
    image_url: Optional[str] = None
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
class CleaningTaskCreate(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "Deep Floor Scrubbing"})

class CleaningTaskUpdate(BaseModel):
    name: Optional[str] = None

class CleaningTaskResponse(BaseModel):
    id: str
    name: str

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

class ContactPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    contacts: List[ContactResponse]

class LocationPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    locations: List[LocationResponse]

class ContractPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    contracts: List[ContractResponse]

class CleaningPlanPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    cleaning_plans: List[CleaningPlanResponse]

# --- Reports ---
class ReportResponse(BaseModel):
    id: str
    title: str
    generated_at: str
    summary: Dict[str, Any]
    pdf_url: Optional[str] = None

class ReportPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
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
    company_name: str = Field(..., json_schema_extra={"example": "Betopia Group"})
    industry: str = Field(..., json_schema_extra={"example": "Cleaning Services"})
    primary_contact_name: str = Field(..., json_schema_extra={"example": "Mahfuz Alam"})
    email: EmailStr = Field(..., json_schema_extra={"example": "c1@yopmail.com"})
    phone: str = Field(..., json_schema_extra={"example": "+8801318532935"})
    status: Optional[str] = Field(default="pending", json_schema_extra={"example": "pending"})

class ClientListUpdate(BaseModel):
    company_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Betopia Group"})
    industry: Optional[str] = Field(default=None, json_schema_extra={"example": "Cleaning Services"})
    primary_contact_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Mahfuz Alam"})
    email: Optional[EmailStr] = Field(default=None, json_schema_extra={"example": "c1@yopmail.com"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801318532935"})
    status: Optional[str] = Field(default=None, json_schema_extra={"example": "active"})
    is_signup: Optional[bool] = Field(default=None, json_schema_extra={"example": True})

class ClientListResponse(BaseModel):
    id: str
    _id: str
    admin_name: str
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    email: EmailStr
    phone: str
    is_signup: bool
    locations_count: int = 0
    contract_status: str = "no_contract"
    locations: List[LocationResponse] = Field(default_factory=list)
    contacts: List[ContactResponse] = Field(default_factory=list)
    contracts: List[ContractResponse] = Field(default_factory=list)
    cleaning_plans: List[CleaningPlanResponse] = Field(default_factory=list)
    reports: List[ReportResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ClientListPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    clients: List[ClientListResponse]

class ClientOverviewItemResponse(BaseModel):
    id: str
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    email: EmailStr
    phone: str
    locations_count: int = 0
    contract_status: str = "no_contract"
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ClientOverviewListPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    clients: List[ClientOverviewItemResponse]

class ClientOverviewDetailResponse(BaseModel):
    id: str
    company_name: str
    industry: str
    status: str
    primary_contact_name: str
    email: EmailStr
    phone: str
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
    id: str
    client_id: str
    company_name: str
    name: str
    type: str
    address: str
    floor: int = 1
    number_of_rooms: int
    description: str
    image_url: Optional[str] = None
    created_at: str
    updated_at: str

class GlobalLocationPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    locations: List[GlobalLocationResponse]

class LocationDropdownItemResponse(BaseModel):
    id: str
    name: str
    client_id: str
    company_name: str
    floor: int = 1

class LocationDropdownPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    locations: List[LocationDropdownItemResponse]

class RoomDropdownItemResponse(BaseModel):
    id: str
    room_name: str

class RoomDropdownPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    rooms: List[RoomDropdownItemResponse]

# --- Required Photo Schemas ---
class RequiredPhotoCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., json_schema_extra={"example": "Before cleaning photo"})

class RequiredPhotoResponse(BaseModel):
    id: str
    name: str

# --- Room Management Schemas ---
class RoomCreate(BaseModel):
    room_name: str = Field(..., json_schema_extra={"example": "Room 301 - Executive Suite"})
    room_type: str = Field(..., json_schema_extra={"example": "suite"})  # standard, deluxe, suite, junior_suite
    location_id: str = Field(..., json_schema_extra={"example": "location_id_here"})
    floor: int = Field(default=1, json_schema_extra={"example": 3})
    duration: int = Field(default=30, json_schema_extra={"example": 45})  # minutes
    required_photos: Optional[List[RequiredPhotoCreate]] = Field(default_factory=list)
    clean_type: str = Field(default="standard", json_schema_extra={"example": "standard"})  # standard, premium
    tasks: Optional[List[CleaningTaskCreate]] = Field(default_factory=list)

class RoomUpdate(BaseModel):
    room_name: Optional[str] = None
    room_type: Optional[str] = None
    location_id: Optional[str] = None
    floor: Optional[int] = None
    duration: Optional[int] = None
    required_photos: Optional[List[RequiredPhotoCreate]] = None
    clean_type: Optional[str] = None
    tasks: Optional[List[CleaningTaskCreate]] = None

class RoomResponse(BaseModel):
    id: str
    room_name: str
    room_type: str
    client_id: str
    company_name: str
    location_id: str
    location_name: str
    floor: int
    duration: int
    required_photos: List[RequiredPhotoResponse] = Field(default_factory=list)
    photo_number: int = 0
    task_number: int = 0
    clean_type: str
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class RoomPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    rooms: List[RoomResponse]

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
    id: str
    name: str
    client_id: str
    client_name: str
    location_id: Optional[str] = None
    client_location_name: Optional[str] = None
    rooms: List[CleaningPlanRoomSummary] = Field(default_factory=list)
    total_duration: int
    total_photo_required: int
    total_tasks_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class GlobalCleaningPlanPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    cleaning_plans: List[GlobalCleaningPlanListItemResponse]


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

class ClientContactPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    contacts: List[ClientContactItem] = Field(default_factory=list)

class ClientLocationItem(BaseModel):
    id: str
    name: str
    address: str
    rooms_count: int = 0
    rooms_label: str = "0 rooms"

class ClientLocationPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
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
    location_id: str
    location_name: str
    client_id: str
    client_company_name: str
    address: str
    floors: int = 1
    rooms: int = 1
    required_hours_label: str = "0h"
    required_hours_numeric: float = 0.0
    created_at: datetime
    updated_at: datetime

class AdminLocationGridPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
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


# --- Admin Global Rooms & Global Cleaning Plans Schemas (Image 1 - Image 5) ---
class AdminRoomCreate(BaseModel):
    name: str
    room_type: str = "Standard"
    location_id: str
    floor: int = 1
    est_cleaning_duration_minutes: int = 45
    required_photos_count: int = 4
    tasks_count: int = 12
    cleaning_plan_name: Optional[str] = "Standard Clean"

class AdminRoomGridItem(BaseModel):
    room_id: str
    room_name: str
    room_type: str
    location_id: str
    location_name: str
    floor_label: str
    duration_minutes: int = 45
    required_photos_count: int = 4
    tasks_count: int = 12
    cleaning_plan_name: str = "Standard Clean"

class AdminRoomGridPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
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

class AdminCleaningPlanGridPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    plans: List[AdminCleaningPlanGridItem] = Field(default_factory=list)

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





