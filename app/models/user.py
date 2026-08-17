from enum import Enum
from typing import Optional, List
from datetime import datetime, timezone, date
from pydantic import BaseModel, EmailStr, Field

class RoleEnum(str, Enum):
    worker = "worker"
    client = "client"
    manager = "manager"
    admin = "admin"


class WorkerTypeEnum(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contractor = "contractor"
    freelancer = "freelancer"
    employee = "employee"

class UserInDB(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    hashed_password: Optional[str] = None
    role: RoleEnum
    is_active: bool = True
    is_verified: bool = False
    
    # Worker Approval & Origin Flags
    is_admin_created: bool = False
    is_approved: bool = True
    approval_status: str = "approved"  # approved, pending, rejected
    rejection_reason: Optional[str] = None

    # OTP fields
    otp_code: Optional[str] = None
    otp_expires_at: Optional[datetime] = None

    # Global profile fields
    profile_photo: Optional[str] = None
    
    # Admin fields
    address: Optional[str] = None
    website: Optional[str] = None

    # Client fields
    company_name: Optional[str] = None
    
    # Worker fields
    employee_id: Optional[str] = None
    isagree_condition: Optional[bool] = False
    dob: Optional[str] = None # Or date, but str is easier for JSON serialization sometimes
    nationality: Optional[str] = None
    worker_type: Optional[WorkerTypeEnum] = None
    position: Optional[str] = None
    location: Optional[str] = None
    base_location: Optional[str] = None
    languages: List[str] = Field(default_factory=list)
    employee_contract_pdf: Optional[str] = None
    
    # Draft storage for onboarding
    onboarding_draft: dict = Field(default_factory=dict)
    
    # Worker Onboarding Flags
    onboarding_complete1: bool = False
    is_profile_completed: bool = False
    
    # Worker Documents (S3 keys or URLs)
    id_card_front: Optional[str] = None
    id_card_back: Optional[str] = None
    certificates: List[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None
    
    # Push Notifications
    push_notifications_enabled: bool = True
    onesignal_player_id: Optional[str] = None

    # Client settings & preferences
    email_notifications: bool = True
    sms_cleaning_alerts: bool = False
    portal_language: str = "English (US)"
    last_password_changed_at: Optional[datetime] = None
    member_since: Optional[str] = None
    contract_type: Optional[str] = None
    account_status: Optional[str] = None

    class Config:
        populate_by_name = True
