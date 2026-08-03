from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List

class DateStripItem(BaseModel):
    day_name: str = Field(..., json_schema_extra={"example": "MON"})
    day_number: int = Field(..., json_schema_extra={"example": 20})
    date_str: str = Field(..., json_schema_extra={"example": "2026-04-20"})
    is_selected: bool = False

class RosterCardItem(BaseModel):
    shift_id: str
    location_name: str = Field(..., json_schema_extra={"example": "Hilton Hotel"})
    status: str = Field(..., json_schema_extra={"example": "completed"})
    status_label: str = Field(..., json_schema_extra={"example": "Completed"})
    time_range: str = Field(..., json_schema_extra={"example": "2:00 PM - 6:00 PM"})
    address_district: str = Field(..., json_schema_extra={"example": "Downtown Business District"})
    rooms_count: int = Field(..., json_schema_extra={"example": 20})
    rooms_count_str: str = Field(..., json_schema_extra={"example": "20 rooms"})
    admin_contact_name: str = Field(..., json_schema_extra={"example": "John Smith"})
    admin_contact_phone: str = Field(..., json_schema_extra={"example": "+31 20 555 7200"})
    can_start_shift: bool = False

class WorkerRosterScreenResponse(BaseModel):
    screen_title: str = "My Roster"
    week_number: int = 17
    week_label: str = "Week 17"
    month_year_label: str = "April 2026"
    date_strip: List[DateStripItem] = Field(default_factory=list)
    shifts: List[RosterCardItem] = Field(default_factory=list)

class WorkerRosterDetailResponse(BaseModel):
    shift_id: str
    location_name: str
    address_district: str
    date_str: str
    time_range: str
    status: str
    status_label: str
    total_rooms: int
    admin_contact_name: str
    admin_contact_phone: str
    assigned_workers_count: int
