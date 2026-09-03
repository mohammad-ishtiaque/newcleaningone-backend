from pydantic import BaseModel, EmailStr, Field, field_validator
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
    description: Optional[str] = Field(None, json_schema_extra={"example": "I cannot view my upcoming shift details."})
    message: Optional[str] = None

    def __init__(self, **data):
        if "description" not in data or not data.get("description"):
            data["description"] = data.get("message") or "Worker support request"
        super().__init__(**data)

class SupportReplyRequest(BaseModel):
    admin_reply: str = Field(..., json_schema_extra={"example": "We have updated your shift visibility. Please check again."})
    status: Optional[str] = Field(default="resolved", json_schema_extra={"example": "resolved"})
    is_resolved: Optional[bool] = Field(default=True, json_schema_extra={"example": True})

class SupportMessageResponse(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")

    @field_validator("id", mode="before")
    def convert_id(cls, v):
        if v is not None:
            return str(v)
        return v
    worker_id: Optional[str] = None
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

class ClientSupportMessageRequest(BaseModel):
    subject: str = Field(..., json_schema_extra={"example": "Inquiry about weekend service schedule"})
    category: Optional[str] = Field("general", json_schema_extra={"example": "general"})
    description: Optional[str] = Field(None, json_schema_extra={"example": "Could we schedule an additional deep clean next Saturday?"})
    message: Optional[str] = None

    def __init__(self, **data):
        if "description" not in data and "message" in data:
            data["description"] = data["message"]
        if not data.get("description"):
            data["description"] = data.get("message") or "Support inquiry"
        super().__init__(**data)

class ClientSupportMessageResponse(BaseModel):
    id: str
    client_id: str
    client_name: str
    client_email: str
    subject: str
    category: str = "general"
    description: str
    status: str = "pending"
    admin_reply: Optional[str] = None
    created_at: datetime

class ClientReviewCreate(BaseModel):
    shift_id: Optional[str] = Field(None, json_schema_extra={"example": "shift_123"})
    cleaner_name: Optional[str] = Field(None, json_schema_extra={"example": "Jan Jansen"})
    rating: int = Field(5, ge=1, le=5, json_schema_extra={"example": 5})
    quality_score: Optional[int] = Field(5, ge=1, le=5)
    punctuality_score: Optional[int] = Field(5, ge=1, le=5)
    review_text: Optional[str] = Field(None, json_schema_extra={"example": "Excellent deep cleaning on the 3rd floor office."})
    comment: Optional[str] = None

    def __init__(self, **data):
        if "rating" in data and isinstance(data["rating"], float):
            data["rating"] = int(data["rating"])
        if "review_text" not in data and "comment" in data:
            data["review_text"] = data["comment"]
        if not data.get("review_text"):
            data["review_text"] = data.get("comment") or "Client review"
        super().__init__(**data)

class ClientReviewResponse(BaseModel):
    id: str
    client_id: str
    client_name: str
    shift_id: Optional[str] = None
    cleaner_name: Optional[str] = None
    rating: int
    quality_score: Optional[int] = 5
    punctuality_score: Optional[int] = 5
    review_text: str
    created_at: datetime

class AdminSupportListResponse(BaseModel):
    total_count: int
    unread_count: int
    messages: List[SupportMessageResponse]

class WorkerSupportListResponse(BaseModel):
    total_count: int
    messages: List[SupportMessageResponse]

from typing import List, Optional, Union

# ---- Legal Document Schemas ----
class LegalDocumentResponse(BaseModel):
    type: str = "privacy_policy"
    title: str
    content: str
    updated_at: Optional[Union[str, datetime]] = None

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
