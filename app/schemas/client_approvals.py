from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.schemas.user import UserResponse

class ClientApproveRequest(BaseModel):
    license_expiration_date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-08-23"})

class ClientRejectRequest(BaseModel):
    reject_reason: Optional[str] = Field(default=None, json_schema_extra={"example": "Did not meet requirements."})

class PendingClientApprovalsPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    has_more: bool
    users: List[UserResponse] = Field(default_factory=list)

class ClientApprovalHistoryResponse(BaseModel):
    id: str
    client_name: str
    client_email: str
    action: str  # "approved" or "rejected"
    manager_id: str
    manager_name: str
    reject_reason: Optional[str] = None
    created_at: datetime

class ClientApprovalHistoryPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    has_more: bool
    history: List[ClientApprovalHistoryResponse] = Field(default_factory=list)
