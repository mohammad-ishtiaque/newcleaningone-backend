from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime
from app.schemas.common import BasePaginatedResponse

# ---- FAQ Schemas ----
class FAQListItem(BaseModel):
    id: int = Field(alias="serial_no")
    question: str

    class Config:
        populate_by_name = True

class FAQDetailResponse(BaseModel):
    id: int = Field(alias="serial_no")
    question: str
    answer: str
    
    class Config:
        populate_by_name = True

class FAQListResponse(BasePaginatedResponse):
    faqs: List[FAQListItem] = Field(default_factory=list)

class FAQCreate(BaseModel):
    question: str = Field(..., json_schema_extra={"example": "How do I request time off?"})
    answer: str = Field(..., json_schema_extra={"example": "Submit a leave request in your app profile settings."})
    serial_no: Optional[int] = Field(default=None, json_schema_extra={"example": 1})

class FAQUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    serial_no: Optional[int] = None

class FAQResponse(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    serial_no: int
    question: str
    answer: str
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

# ---- Support Message Schemas ----
class SupportMessageRequest(BaseModel):
    subject: str = Field(..., json_schema_extra={"example": "Issue with shift"})
    description: str = Field(..., json_schema_extra={"example": "I cannot view my upcoming shift details."})

class SupportReplyRequest(BaseModel):
    admin_reply: str = Field(..., json_schema_extra={"example": "We have updated your shift visibility. Please check again."})
    status: Optional[str] = Field(default="resolved", json_schema_extra={"example": "resolved"})
    is_resolved: Optional[bool] = Field(default=True, json_schema_extra={"example": True})

class SupportMessageResponse(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    worker_id: str
    worker_name: Optional[str] = None
    worker_email: Optional[str] = None
    subject: str
    description: str
    admin_reply: Optional[str] = None
    status: str = "pending"
    is_resolved: bool = False
    is_read_by_admin: bool = False
    is_read_by_worker: bool = True
    created_at: datetime
    updated_at: datetime
    replied_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        from_attributes = True

class AdminSupportListResponse(BaseModel):
    total_count: int
    unread_count: int
    messages: List[SupportMessageResponse]

class WorkerSupportListResponse(BaseModel):
    total_count: int
    messages: List[SupportMessageResponse]

# ---- Legal Document Schemas ----
class LegalDocumentResponse(BaseModel):
    type: str = "privacy_policy"
    title: str
    content: str
    updated_at: str

class LegalDocumentUpdate(BaseModel):
    title: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated Privacy Policy"})
    content: str = Field(..., json_schema_extra={"example": "This is our updated privacy policy content..."})

# ---- Company Profile Schemas ----
class CompanyProfileResponse(BaseModel):
    company_name: Optional[str] = "Cleaning One"
    email: Optional[EmailStr] = "admin@cleaningone.com"
    phone: Optional[str] = "+8801319414320"
    address: Optional[str] = "Mohakhali, Dhaka"
    website: Optional[str] = "www.cleaningone.com"
    updated_at: Optional[datetime] = None

class CompanyProfileUpdate(BaseModel):
    company_name: Optional[str] = Field(default=None, json_schema_extra={"example": "Cleaning One Corporation"})
    email: Optional[EmailStr] = Field(default=None, json_schema_extra={"example": "admin@cleaningone.com"})
    phone: Optional[str] = Field(default=None, json_schema_extra={"example": "+8801319414320"})
    address: Optional[str] = Field(default=None, json_schema_extra={"example": "House 12, Road 5, Mohakhali, Dhaka"})
    website: Optional[str] = Field(default=None, json_schema_extra={"example": "https://www.cleaningone.com"})
