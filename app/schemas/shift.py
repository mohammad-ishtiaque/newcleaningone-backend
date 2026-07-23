from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

class ShiftDraftCreate(BaseModel):
    client_id: str = Field(..., json_schema_extra={"example": "6a61b7f68ad7764bf1032f67"})
    location_id: str = Field(..., json_schema_extra={"example": "2a78f050-4410-424f-86dd-d9a442b67816"})
    date: str = Field(..., json_schema_extra={"example": "2026-07-25"})
    start_time: str = Field(..., json_schema_extra={"example": "08:00"})
    end_time: str = Field(..., json_schema_extra={"example": "16:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Deep clean executive floor"})

class ShiftDraftResponse(BaseModel):
    id: str
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    date: str
    start_time: str
    end_time: str
    shift_notes: Optional[str] = None
    status: str = "draft"
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftDraftUpdate(BaseModel):
    client_id: Optional[str] = Field(default=None, json_schema_extra={"example": "6a61b7f68ad7764bf1032f67"})
    location_id: Optional[str] = Field(default=None, json_schema_extra={"example": "2a78f050-4410-424f-86dd-d9a442b67816"})
    date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-07-26"})
    start_time: Optional[str] = Field(default=None, json_schema_extra={"example": "09:00"})
    end_time: Optional[str] = Field(default=None, json_schema_extra={"example": "17:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated draft notes"})

class ShiftDraftPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    drafts: List[ShiftDraftResponse]

class WorkerDropdownItem(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    status: Literal["available", "on_shift"]
    worker_type: str

class WorkerDropdownPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    workers: List[WorkerDropdownItem]

class ShiftAssignRequest(BaseModel):
    draft_id: str = Field(..., json_schema_extra={"example": "draft_uuid_12345"})
    worker_ids: List[str] = Field(..., json_schema_extra={"example": ["worker_id_1", "worker_id_2"]})

class ShiftWorkerDetail(BaseModel):
    worker_id: str
    name: str
    profile_picture: Optional[str] = None
    worker_type: str

class ShiftResponse(BaseModel):
    id: str
    draft_id: Optional[str] = None
    client_id: str
    client_name: str
    location_id: str
    location_name: str
    date: str
    start_time: str
    end_time: str
    shift_notes: Optional[str] = None
    status: str
    workers: List[ShiftWorkerDetail] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class ShiftPaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    shifts: List[ShiftResponse]

class ShiftUpdate(BaseModel):
    date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-07-26"})
    start_time: Optional[str] = Field(default=None, json_schema_extra={"example": "09:00"})
    end_time: Optional[str] = Field(default=None, json_schema_extra={"example": "17:00"})
    shift_notes: Optional[str] = Field(default=None, json_schema_extra={"example": "Updated shift notes"})
    status: Optional[str] = Field(default=None, json_schema_extra={"example": "published"})
    worker_ids: Optional[List[str]] = Field(default=None, json_schema_extra={"example": ["worker_id_1"]})

class DayShiftGroup(BaseModel):
    date: str
    total_shifts: int
    shifts: List[ShiftResponse] = Field(default_factory=list)

class ShiftOverviewResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    total_shifts: int
    overview: List[DayShiftGroup] = Field(default_factory=list)
