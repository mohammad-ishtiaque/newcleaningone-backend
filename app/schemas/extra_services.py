from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse

class ExtraServiceTaskItem(BaseModel):
    id: str
    name: str
    is_completed: bool = False
    completed_at: Optional[datetime] = None

class ExtraServicePhotoRequirement(BaseModel):
    id: str
    name: str
    photo_url: Optional[str] = None
    is_uploaded: bool = False
    uploaded_at: Optional[datetime] = None

class ExtraServiceCreate(BaseModel):
    title: str = Field(..., json_schema_extra={"example": "Window Cleaning"})
    preferred_date: str = Field(..., json_schema_extra={"example": "2026-07-10"})
    priority: Literal["High Priority", "Medium Priority", "Low Priority", "high", "medium", "low"] = "Medium Priority"
    description: str = Field(..., json_schema_extra={"example": "All exterior windows on floors 2-4 need cleaning before client visit."})
    location_id: Optional[str] = None
    room_id: Optional[str] = None
    task_list: List[str] = Field(default_factory=list, json_schema_extra={"example": ["Clean exterior glass", "Wipe window sills"]})

class ExtraServiceUpdate(BaseModel):
    title: Optional[str] = None
    preferred_date: Optional[str] = None
    priority: Optional[Literal["High Priority", "Medium Priority", "Low Priority", "high", "medium", "low"]] = None
    description: Optional[str] = None
    location_id: Optional[str] = None
    room_id: Optional[str] = None
    task_list: Optional[List[str]] = None

class ExtraServiceApproveRequest(BaseModel):
    worker_ids: List[str] = Field(..., json_schema_extra={"example": ["worker_123"]})
    required_photos: List[str] = Field(default_factory=list, json_schema_extra={"example": ["Clean exterior window photo"]})
    estimated_hours: float = Field(2.0, description="Estimated duration in hours for this service")
    admin_notes: Optional[str] = None

class ExtraServiceRejectRequest(BaseModel):
    reason: str = Field(..., json_schema_extra={"example": "Service requested is outside operational scope."})

class ExtraServiceWorkerDetail(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None

class ClientInfo(BaseModel):
    id: str
    name: str

class LocationInfo(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None

class RoomInfo(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None

class ExtraServiceResponse(BaseModel):
    id: str
    title: str
    preferred_date: str
    priority: str
    description: str
    status: Literal["pending", "under_review", "approved", "rejected", "in_progress", "submitted_for_completion", "completed"] = "under_review"
    client_id: str
    client_name: str
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    room_id: Optional[str] = None
    room_name: Optional[str] = None
    client: Optional[ClientInfo] = None
    location: Optional[LocationInfo] = None
    room: Optional[RoomInfo] = None
    date_submitted: str
    rejection_reason: Optional[str] = None
    assigned_workers: List[ExtraServiceWorkerDetail] = Field(default_factory=list)
    tasks: List[ExtraServiceTaskItem] = Field(default_factory=list)
    required_photos: List[ExtraServicePhotoRequirement] = Field(default_factory=list)
    estimated_hours: float = 0.0
    actual_start_time: Optional[datetime] = None
    actual_finish_time: Optional[datetime] = None
    hours_credited: Optional[float] = None
    created_at: datetime
    updated_at: datetime

class ExtraServicePaginatedResponse(BasePaginatedResponse):
    requests: List[ExtraServiceResponse] = Field(default_factory=list)

