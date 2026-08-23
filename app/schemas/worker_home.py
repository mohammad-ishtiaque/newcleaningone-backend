from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List

class WorkerActiveShiftCard(BaseModel):
    shift_id: str
    client_name: str = Field(..., json_schema_extra={"example": "Hilton Amsterdam"})
    location_name: str = Field(..., json_schema_extra={"example": "Main"})
    location_address: str = Field(..., json_schema_extra={"example": "Apollolaan 138"})
    time_range: str = Field(..., json_schema_extra={"example": "08:00 - 16:00"})
    rooms_progress_str: str = Field(..., json_schema_extra={"example": "7 / 12 rooms"})
    completed_rooms: int = 7
    total_rooms: int = 12
    overall_progress_percentage: float = 62.0
    status: str = "running"

class WorkerHomeCounters(BaseModel):
    todays_shifts: int = 0
    completed_rooms: int = 0
    pending_rooms: int = 0

class QuickActionItem(BaseModel):
    id: str
    title: str
    action_type: str
    icon_type: str

class WorkerNextShiftCard(BaseModel):
    shift_id: str
    location_name: str = Field(..., json_schema_extra={"example": "Office Building A"})
    time_until_start: str = Field(..., json_schema_extra={"example": "In 3 hours"})
    time_range: str = Field(..., json_schema_extra={"example": "2:00 PM - 6:00 PM"})
    address_district: str = Field(..., json_schema_extra={"example": "Downtown Business District"})
    date: str = Field(..., json_schema_extra={"example": "2026-08-03"})

class ActivityFeedItem(BaseModel):
    id: str
    title: str = Field(..., json_schema_extra={"example": "Photo uploaded for Room 105"})
    subtitle: str = Field(..., json_schema_extra={"example": "Saved to inspection report."})
    time_ago: str = Field(..., json_schema_extra={"example": "1h ago"})
    activity_type: str = Field(..., json_schema_extra={"example": "photo_upload"})

class WorkerHomeScreenResponse(BaseModel):
    worker_name: str = "Kaz Putters"
    profile_photo: Optional[str] = None
    active_shift: Optional[WorkerActiveShiftCard] = None
    counters: WorkerHomeCounters
    quick_actions: List[QuickActionItem] = Field(default_factory=list)
    next_shift: Optional[WorkerNextShiftCard] = None
    recent_activity: List[ActivityFeedItem] = Field(default_factory=list)
