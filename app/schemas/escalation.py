from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal

class EscalationCreate(BaseModel):
    title: str = Field(..., json_schema_extra={"example": "Broken mirror in Room 305"})
    description: str = Field(..., json_schema_extra={"example": "A large bathroom mirror appears cracked. It is unclear whether it concerns existing damage."})
    location_name: Optional[str] = Field(None, json_schema_extra={"example": "NH Hotel Amsterdam"})
    room_name: Optional[str] = Field(None, json_schema_extra={"example": "Kamer 305"})
    severity: Literal["high", "medium", "low"] = Field("high", json_schema_extra={"example": "high"})
    photo_url: Optional[str] = Field(None, json_schema_extra={"example": "/uploads/escalations/sample.jpg"})

class EscalationStatusUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"] = Field(..., json_schema_extra={"example": "resolved"})
    notes: Optional[str] = Field(None, json_schema_extra={"example": "Maintenance team dispatched and mirror replaced."})

class EscalationReporterDetail(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None

class EscalationAssigneeDetail(BaseModel):
    admin_id: str
    name: str

class EscalationItem(BaseModel):
    escalation_id: str
    shift_id: Optional[str] = None
    title: str
    subtitle: str  # e.g., "NH Hotel Amsterdam - Kamer 305"
    description: str
    severity: Literal["high", "medium", "low"] = "high"
    reporter: EscalationReporterDetail
    assigned_to: Optional[EscalationAssigneeDetail] = None
    status: Literal["open", "in_progress", "resolved", "closed"] = "open"
    status_label: str  # "Open", "In Progress", "Resolved", "Closed"
    photo_url: Optional[str] = None
    created_at: datetime

class EscalationPaginatedResponse(BaseModel):
    total_count: int
    open_count: int
    in_progress_count: int
    resolved_count: int
    page: int
    limit: int
    escalations: List[EscalationItem] = Field(default_factory=list)

class EscalationDrawerResponse(BaseModel):
    escalation_id: str
    shift_id: Optional[str] = None
    title: str
    subtitle: str
    description: str
    severity: str
    reporter: EscalationReporterDetail
    assigned_to: Optional[EscalationAssigneeDetail] = None
    status: str
    photo_url: Optional[str] = None
    created_at: datetime
    notes: Optional[str] = None
