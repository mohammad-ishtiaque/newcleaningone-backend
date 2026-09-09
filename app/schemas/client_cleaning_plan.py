from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from app.schemas.client_list import (
    CleaningPlanRoomDetail, CleaningPlanWorkerDetail,
    CleaningTaskResponse, RequiredPhotoResponse, TaskPhotoResponse,
    PendingAdditionalTaskResponse
)


class ClientCleaningPlanListItem(BaseModel):
    id: str = Field(..., description="Unique Cleaning Plan or Extra Service ID", json_schema_extra={"example": "plan_1129452756"})
    title: str = Field(..., description="Title / Name of the cleaning plan or extra service", json_schema_extra={"example": "New Cleaning plan"})
    service_kind: str = Field(default="cleaning_plan", description="Kind of service: 'cleaning_plan' or 'extra_service'", json_schema_extra={"example": "cleaning_plan"})
    priority: Optional[str] = Field(default=None, description="Priority level (for extra services)", json_schema_extra={"example": "High Priority"})
    date: Optional[str] = Field(default=None, description="Service date (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    start_time: Optional[str] = Field(default="08:00 AM", description="Scheduled start time", json_schema_extra={"example": "09:15 AM"})
    end_time: Optional[str] = Field(default="10:00 AM", description="Scheduled end time", json_schema_extra={"example": "10:45 AM"})
    duration_minutes: int = Field(default=60, description="Total planned duration in minutes", json_schema_extra={"example": 90})
    status: str = Field(default="scheduled", description="Current status: 'scheduled', 'in_progress', 'completed', 'assigned', 'draft', 'under_review', 'approved', 'rejected'", json_schema_extra={"example": "in_progress"})
    location_id: Optional[str] = Field(default=None, description="Facility / office location ID", json_schema_extra={"example": "loc_0510d4c547"})
    location_name: Optional[str] = Field(default=None, description="Facility / office location name", json_schema_extra={"example": "Mirpur"})
    rooms_count: int = Field(default=0, description="Total rooms included in this plan", json_schema_extra={"example": 1})
    room_names: List[str] = Field(default_factory=list, description="Names of all rooms included", json_schema_extra={"example": ["Bachelor Room"]})
    workers_count: int = Field(default=0, description="Number of assigned cleaners/workers", json_schema_extra={"example": 1})
    worker_names: List[str] = Field(default_factory=list, description="Names of assigned cleaners/workers", json_schema_extra={"example": ["Sadim Hasan"]})
    total_tasks_count: int = Field(default=0, description="Total cleaning tasks count", json_schema_extra={"example": 5})
    completed_tasks_count: int = Field(default=0, description="Total completed cleaning tasks", json_schema_extra={"example": 2})
    total_photos_count: int = Field(default=0, description="Total required proof photos count", json_schema_extra={"example": 11})
    overall_progress_percentage: float = Field(default=0.0, description="Real-time cleaning completion percentage (0-100%)", json_schema_extra={"example": 40.0})
    repeat_shift: Optional[str] = Field(default="Does not repeat", description="Recurrence rule: 'Does not repeat', 'Every day', 'Standard working week', 'Weekly', 'Monthly'", json_schema_extra={"example": "Every day"})
    repeat_until: Optional[str] = Field(default=None, description="End date of recurrence (YYYY-MM-DD)", json_schema_extra={"example": "2026-09-30"})
    working_days: List[str] = Field(default_factory=list, description="Days of week when plan runs: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']", json_schema_extra={"example": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
    timezone: str = Field(default="Europe/Amsterdam", description="Timezone of the cleaning plan schedule", json_schema_extra={"example": "Europe/Amsterdam"})
    is_active: bool = Field(default=True, description="Whether the plan is currently active", json_schema_extra={"example": True})
    created_at: Optional[Union[datetime, str]] = Field(default=None, description="Record creation timestamp", json_schema_extra={"example": "2026-08-26T03:02:46.055000Z"})
    updated_at: Optional[Union[datetime, str]] = Field(default=None, description="Last update timestamp", json_schema_extra={"example": "2026-08-26T03:03:00.546000Z"})


class ClientCleaningPlanPaginatedResponse(BaseModel):
    total_count: int = Field(..., description="Total cleaning plans and extra services matching filters", json_schema_extra={"example": 4})
    page: int = Field(..., description="Current page number", json_schema_extra={"example": 1})
    limit: int = Field(..., description="Items per page", json_schema_extra={"example": 10})
    has_more: bool = Field(default=False, description="Whether there are more pages available", json_schema_extra={"example": False})
    plans: List[ClientCleaningPlanListItem] = Field(..., description="List of client cleaning plans and extra services")


class ClientCleaningPlanDetailResponse(BaseModel):
    id: str = Field(..., description="Unique Cleaning Plan or Extra Service ID", json_schema_extra={"example": "plan_1129452756"})
    title: str = Field(..., description="Title / Name of the cleaning plan or extra service", json_schema_extra={"example": "New Cleaning plan"})
    service_kind: str = Field(default="cleaning_plan", description="Kind of service: 'cleaning_plan' or 'extra_service'", json_schema_extra={"example": "cleaning_plan"})
    shift_notes: Optional[str] = Field(default=None, description="Instructions / notes for the cleaning crew", json_schema_extra={"example": "Notes"})
    priority: Optional[str] = Field(default=None, description="Priority level (for extra services)", json_schema_extra={"example": "High Priority"})
    client_id: str = Field(..., description="Client ID", json_schema_extra={"example": "cli_0cbe945846"})
    company_name: str = Field(..., description="Client company name", json_schema_extra={"example": "Bam Bula"})
    location_id: Optional[str] = Field(default=None, description="Facility / office location ID", json_schema_extra={"example": "loc_0510d4c547"})
    location_name: Optional[str] = Field(default=None, description="Facility / office location name", json_schema_extra={"example": "Mirpur"})
    room_ids: List[str] = Field(default_factory=list, description="IDs of all included rooms", json_schema_extra={"example": ["room_6ff38b0f16"]})
    rooms: List[CleaningPlanRoomDetail] = Field(default_factory=list, description="Hierarchical room details with checklist tasks and photo requirements")
    rooms_count: int = Field(default=0, description="Total rooms included", json_schema_extra={"example": 1})
    worker_ids: List[str] = Field(default_factory=list, description="IDs of assigned cleaners", json_schema_extra={"example": ["6a8e52db69390732dca5a657"]})
    workers: List[CleaningPlanWorkerDetail] = Field(default_factory=list, description="Assigned cleaners with position, contact, and profile details")
    workers_count: int = Field(default=0, description="Total assigned cleaners count", json_schema_extra={"example": 1})
    additional_tasks: List[CleaningTaskResponse] = Field(default_factory=list, description="Additional cleaning tasks with task-level required photos")
    pending_additional_tasks: List[PendingAdditionalTaskResponse] = Field(default_factory=list, description="Additional-task requests this client submitted, with their approve/reject status")
    total_tasks_count: int = Field(default=0, description="Total cleaning tasks across all rooms and additional items", json_schema_extra={"example": 5})
    completed_tasks_count: int = Field(default=0, description="Total completed cleaning tasks", json_schema_extra={"example": 2})
    total_photos_count: int = Field(default=0, description="Total required proof photos", json_schema_extra={"example": 11})
    overall_progress_percentage: float = Field(default=0.0, description="Real-time cleaning completion percentage (0-100%)", json_schema_extra={"example": 40.0})
    date: Optional[str] = Field(default=None, description="Service date (YYYY-MM-DD)", json_schema_extra={"example": "2026-08-26"})
    start_time: Optional[str] = Field(default="08:00 AM", description="Scheduled start time", json_schema_extra={"example": "09:15 AM"})
    end_time: Optional[str] = Field(default="10:00 AM", description="Scheduled end time", json_schema_extra={"example": "10:45 AM"})
    duration_minutes: int = Field(default=60, description="Total planned duration in minutes", json_schema_extra={"example": 90})
    repeat_shift: Optional[str] = Field(default="Does not repeat", description="Recurrence rule: 'Does not repeat', 'Every day', 'Standard working week', 'Weekly', 'Monthly'", json_schema_extra={"example": "Every day"})
    repeat_until: Optional[str] = Field(default=None, description="End date of recurrence (YYYY-MM-DD)", json_schema_extra={"example": "2026-09-30"})
    working_days: List[str] = Field(default_factory=list, description="Days of week when plan runs", json_schema_extra={"example": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
    timezone: str = Field(default="Europe/Amsterdam", description="Timezone of the cleaning plan schedule", json_schema_extra={"example": "Europe/Amsterdam"})
    status: str = Field(default="draft", description="Plan execution status", json_schema_extra={"example": "assigned"})
    is_active: bool = Field(default=True, description="Whether the plan is active", json_schema_extra={"example": True})
    created_at: Optional[Union[datetime, str]] = Field(default=None, description="Record creation timestamp", json_schema_extra={"example": "2026-08-26T03:02:46.055000Z"})
    updated_at: Optional[Union[datetime, str]] = Field(default=None, description="Last update timestamp", json_schema_extra={"example": "2026-08-26T03:03:00.546000Z"})
