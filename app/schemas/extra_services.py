from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse
from app.schemas.client_list import (
    CleaningTaskCreate, TaskPhotoCreate, TaskPhotoResponse, CleaningPlanWorkerDropdownItem
)

class ExtraServiceTaskItem(BaseModel):
    id: str
    name: str
    frequency_type: Optional[str] = "every_visit"
    is_photo_req: bool = False
    photo: List[TaskPhotoResponse] = Field(default_factory=list)
    total_photos_required: int = 0
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
    tasks: Optional[List[CleaningTaskCreate]] = Field(default_factory=list)
    task_list: Optional[List[str]] = None

    def __init__(self, **data):
        # Backward-compat: if task_list provided but no tasks, convert task_list strings to tasks
        if "task_list" in data and data["task_list"] and ("tasks" not in data or not data["tasks"]):
            data["tasks"] = [CleaningTaskCreate(name=t) for t in data["task_list"]]
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Window Cleaning",
                "preferred_date": "2026-07-10",
                "priority": "Medium Priority",
                "description": "All exterior windows on floors 2-4 need cleaning before client visit.",
                "location_id": "loc_db28f5a3f6",
                "room_id": "room_a366ecf17c",
                "tasks": [
                    {
                        "name": "Clean exterior glass",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "photo": [
                            {"name": "After exterior glass cleaning"},
                            {"name": "Before exterior glass cleaning"}
                        ]
                    },
                    {
                        "name": "Wipe window sills",
                        "frequency_type": "every_visit",
                        "is_photo_req": False,
                        "photo": []
                    }
                ]
            }
        }
    }

class ExtraServiceUpdate(BaseModel):
    title: Optional[str] = None
    preferred_date: Optional[str] = None
    priority: Optional[Literal["High Priority", "Medium Priority", "Low Priority", "high", "medium", "low"]] = None
    description: Optional[str] = None
    location_id: Optional[str] = None
    room_id: Optional[str] = None
    tasks: Optional[List[CleaningTaskCreate]] = None
    task_list: Optional[List[str]] = None

    def __init__(self, **data):
        if "task_list" in data and data["task_list"] is not None and ("tasks" not in data or data["tasks"] is None):
            data["tasks"] = [CleaningTaskCreate(name=t) for t in data["task_list"]]
        super().__init__(**data)

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Window Cleaning",
                "preferred_date": "2026-07-15",
                "priority": "High Priority",
                "description": "Updated window cleaning scope",
                "tasks": [
                    {
                        "name": "Clean exterior glass",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "photo": [
                            {"name": "After exterior glass cleaning"}
                        ]
                    }
                ]
            }
        }
    }

class AssignWorkerItem(BaseModel):
    worker_id: str = Field(..., json_schema_extra={"example": "worker_123"})
    position: Optional[str] = Field(default="normal", json_schema_extra={"example": "teamleader"})  # teamleader, co_leader, normal

class AssignWorkersToExtraServiceRequest(BaseModel):
    workers: List[AssignWorkerItem] = Field(..., json_schema_extra={"example": [{"worker_id": "worker_123", "position": "teamleader"}]})
    action: Optional[Literal["append", "replace"]] = "append"
    estimated_hours: Optional[float] = None
    admin_notes: Optional[str] = None

class ExtraServiceApproveRequest(BaseModel):
    worker_ids: Optional[List[str]] = Field(default_factory=list, json_schema_extra={"example": ["worker_123"]})
    workers: Optional[List[AssignWorkerItem]] = Field(default_factory=list, json_schema_extra={"example": [{"worker_id": "worker_123", "position": "teamleader"}]})
    action: Optional[Literal["append", "replace"]] = "append"
    required_photos: Optional[List[str]] = Field(default_factory=list, json_schema_extra={"example": ["Clean exterior window photo"]})
    estimated_hours: float = Field(2.0, description="Estimated duration in hours for this service")
    admin_notes: Optional[str] = None

class ExtraServiceRejectRequest(BaseModel):
    reason: str = Field(..., json_schema_extra={"example": "Service requested is outside operational scope."})

class ExtraServiceWorkerDetail(BaseModel):
    worker_id: str
    name: str
    email: Optional[str] = None
    role: Optional[str] = "worker"
    worker_type: Optional[str] = "employee"
    position: Optional[str] = "normal"  # teamleader, co_leader, normal
    phone: Optional[str] = None
    profile_photo: Optional[str] = None
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
    total_tasks_count: int = 0
    total_photos_count: int = 0
    required_photos: List[ExtraServicePhotoRequirement] = Field(default_factory=list)
    estimated_hours: float = 0.0
    actual_start_time: Optional[datetime] = None
    actual_finish_time: Optional[datetime] = None
    hours_credited: Optional[float] = None
    created_at: datetime
    updated_at: datetime

class ExtraServicePaginatedResponse(BasePaginatedResponse):
    requests: List[ExtraServiceResponse] = Field(default_factory=list)

class ExtraServiceWorkerDropdownPaginatedResponse(BasePaginatedResponse):
    request_id: str
    preferred_date: str
    time_window: Optional[str] = None
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    workers: List[CleaningPlanWorkerDropdownItem] = Field(default_factory=list)
