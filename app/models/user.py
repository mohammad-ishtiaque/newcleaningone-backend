from enum import Enum
from typing import Optional, List
from datetime import datetime, timezone, date
from pydantic import BaseModel, EmailStr, Field

class RoleEnum(str, Enum):
    worker = "worker"
    client = "client"
    admin = "admin"
    super_admin = "super_admin"

class WorkerTypeEnum(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contractor = "contractor"

class UserInDB(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    hashed_password: str
    role: RoleEnum
    is_active: bool = True
    is_verified: bool = False
    
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

    class Config:
        populate_by_name = True
