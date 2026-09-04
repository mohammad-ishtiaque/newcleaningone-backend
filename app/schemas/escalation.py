from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse

# --- Supported Enums & Literals ---
EscalationStatusType = Literal["open", "in_progress", "resolved", "closed"]
EscalationSeverityType = Literal["high", "medium", "low"]
EscalationCategoryType = Literal[
    "maintenance", "safety", "access", "cleaning", "equipment", "client_dispute", "other"
]

class EscalationCreate(BaseModel):
    """Payload schema for creating a general escalation."""
    title: str = Field(
        ...,
        description="Short summary of the incident or problem.",
        json_schema_extra={"example": "Broken mirror in Room 305"}
    )
    description: str = Field(
        ...,
        description="Detailed description of the problem, circumstances, and immediate risks.",
        json_schema_extra={"example": "A large bathroom mirror appears cracked. It is unclear whether it concerns existing damage."}
    )
    location_name: Optional[str] = Field(
        None,
        description="Name of hotel, building, or client facility.",
        json_schema_extra={"example": "NH Hotel Amsterdam"}
    )
    room_name: Optional[str] = Field(
        None,
        description="Room number or specific area description.",
        json_schema_extra={"example": "Room 305"}
    )
    severity: EscalationSeverityType = Field(
        "high",
        description="Severity level. Supported values: 'high', 'medium', 'low'.",
        json_schema_extra={"example": "high"}
    )
    category: Optional[str] = Field(
        "maintenance",
        description="Incident category. Supported: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'.",
        json_schema_extra={"example": "maintenance"}
    )
    photo_url: Optional[str] = Field(
        None,
        description="Primary evidence image URL or uploaded path.",
        json_schema_extra={"example": "https://s3.eu-central-1.amazonaws.com/cleanones-bucket/uploads/sample.jpg"}
    )
    photo_urls: Optional[List[str]] = Field(
        default_factory=list,
        description="List of all uploaded photo URLs for this escalation.",
        json_schema_extra={"example": ["https://s3.eu-central-1.amazonaws.com/cleanones-bucket/uploads/sample.jpg"]}
    )

class EscalationStatusUpdate(BaseModel):
    """Payload schema for managers updating an escalation's status and resolution notes."""
    status: EscalationStatusType = Field(
        ...,
        description="Target status for the escalation. Supported values: 'open', 'in_progress', 'resolved', 'closed'.",
        json_schema_extra={"example": "resolved"}
    )
    notes: Optional[str] = Field(
        None,
        description="Resolution notes, instructions, or context added by the manager. Dispatched to the reporter worker.",
        json_schema_extra={"example": "Maintenance team dispatched and mirror replaced. Safe to resume cleaning."}
    )

class EscalationReporterDetail(BaseModel):
    """Information about the worker or staff member who reported the escalation."""
    worker_id: str = Field(..., description="Unique worker user ID.", json_schema_extra={"example": "6a8e522e69390732dca5a656"})
    name: str = Field(..., description="Full name of the reporting worker.", json_schema_extra={"example": "Sadim Hasan"})
    profile_picture: Optional[str] = Field(None, description="URL to reporter avatar or photo.", json_schema_extra={"example": "https://example.com/avatar.jpg"})
    phone: Optional[str] = Field(None, description="Contact phone number of the worker.", json_schema_extra={"example": "+31612345678"})
    email: Optional[str] = Field(None, description="Worker email address.", json_schema_extra={"example": "w1@yopmail.com"})

class EscalationAssigneeDetail(BaseModel):
    """Manager or admin user assigned to or resolving the escalation."""
    admin_id: str = Field(..., description="User ID of the assigned manager/admin.", json_schema_extra={"example": "6a8d37faca8fe06e719d9556"})
    name: str = Field(..., description="Full name of the assigned manager/admin.", json_schema_extra={"example": "Manager One"})
    email: Optional[str] = Field(None, description="Manager email address.", json_schema_extra={"example": "m1@yopmail.com"})

class EscalationItem(BaseModel):
    """Item structure for Admin Escalations Grid."""
    escalation_id: str = Field(..., description="Unique escalation ID.", json_schema_extra={"example": "esc_8d2ae6d1b3"})
    shift_id: Optional[str] = Field(None, description="Associated shift or execution ID if linked.", json_schema_extra={"example": "exec_plan_123_2026-09-01"})
    title: str = Field(..., description="Escalation summary title.", json_schema_extra={"example": "Broken bathroom door lock"})
    subtitle: str = Field(..., description="Facility and room name formatted for display.", json_schema_extra={"example": "NH Hotel Amsterdam - Room 305"})
    description: str = Field(..., description="Detailed description of the issue.", json_schema_extra={"example": "Door handle is detached."})
    category: Optional[str] = Field("maintenance", description="Issue category.", json_schema_extra={"example": "maintenance"})
    severity: EscalationSeverityType = Field("high", description="High, medium, or low priority.", json_schema_extra={"example": "high"})
    reporter: EscalationReporterDetail = Field(..., description="Details of the worker who filed this escalation.")
    assigned_to: Optional[EscalationAssigneeDetail] = Field(None, description="Assigned manager or resolution owner.")
    status: EscalationStatusType = Field("open", description="Current status: open, in_progress, resolved, closed.", json_schema_extra={"example": "open"})
    status_label: str = Field("Open", description="Display formatted status label: 'Open', 'In Progress', 'Resolved', 'Closed'.", json_schema_extra={"example": "Open"})
    photo_url: Optional[str] = Field(None, description="Primary photo URL thumbnail.", json_schema_extra={"example": "https://example.com/door.jpg"})
    created_at: datetime = Field(..., description="UTC creation timestamp.")

class EscalationPaginatedResponse(BasePaginatedResponse):
    """Response schema for Admin Escalations Grid with counters for all statuses."""
    open_count: int = Field(0, description="Total count of open escalations across the platform.", json_schema_extra={"example": 12})
    in_progress_count: int = Field(0, description="Total count of in-progress escalations.", json_schema_extra={"example": 3})
    resolved_count: int = Field(0, description="Total count of resolved escalations.", json_schema_extra={"example": 25})
    closed_count: int = Field(0, description="Total count of closed escalations.", json_schema_extra={"example": 8})
    escalations: List[EscalationItem] = Field(default_factory=list, description="Paginated list of escalation records.")

class EscalationDrawerResponse(BaseModel):
    """Detailed response schema for the Escalation Drawer view."""
    escalation_id: str = Field(..., description="Unique escalation ID.", json_schema_extra={"example": "esc_8d2ae6d1b3"})
    shift_id: Optional[str] = Field(None, description="Associated shift ID.", json_schema_extra={"example": "exec_plan_123_2026-09-01"})
    title: str = Field(..., description="Escalation title.", json_schema_extra={"example": "Broken Keycard / Inaccessible Room"})
    subtitle: str = Field(..., description="Location and Room display string.", json_schema_extra={"example": "NH Hotel Amsterdam - Room 204"})
    description: str = Field(..., description="Full description of the incident.", json_schema_extra={"example": "Room 204 electronic card lock is flashing red."})
    category: Optional[str] = Field("maintenance", description="Category: maintenance, safety, access, cleaning, equipment, other.", json_schema_extra={"example": "access"})
    severity: str = Field("high", description="Severity: high, medium, low.", json_schema_extra={"example": "high"})
    reporter: EscalationReporterDetail = Field(..., description="Reporting worker details.")
    assigned_to: Optional[EscalationAssigneeDetail] = Field(None, description="Assigned manager or resolution handler.")
    status: str = Field("open", description="Current status: open, in_progress, resolved, closed.", json_schema_extra={"example": "open"})
    status_label: str = Field("Open", description="Formatted status label.", json_schema_extra={"example": "Open"})
    photo_url: Optional[str] = Field(None, description="Primary photo URL.", json_schema_extra={"example": "https://example.com/lock.jpg"})
    photo_urls: List[str] = Field(default_factory=list, description="All uploaded photo URLs.", json_schema_extra={"example": ["https://example.com/lock.jpg"]})
    location_name: Optional[str] = Field(None, description="Client facility name.", json_schema_extra={"example": "NH Hotel Amsterdam"})
    room_name: Optional[str] = Field(None, description="Room number or specific area.", json_schema_extra={"example": "Room 204"})
    client_name: Optional[str] = Field(None, description="Client company or account name.", json_schema_extra={"example": "NH Hotel Group"})
    created_at: datetime = Field(..., description="Timestamp when reported.")
    updated_at: Optional[datetime] = Field(None, description="Last modification timestamp.")
    resolved_at: Optional[datetime] = Field(None, description="Timestamp when resolved by manager.")
    notes: Optional[str] = Field(None, description="Manager notes or resolution comments.", json_schema_extra={"example": "Locksmith dispatched and battery replaced."})

# --- Worker-facing Schemas ---

class WorkerEscalationCreate(BaseModel):
    """Payload schema for workers submitting an escalation report."""
    shift_id: Optional[str] = Field(
        None,
        description="Shift ID if this escalation occurred during a scheduled cleaning shift.",
        json_schema_extra={"example": "exec_plan_f1c779b8f7_2026-09-01"}
    )
    room_id: Optional[str] = Field(
        None,
        description="Room ID if this escalation is tied to a specific room.",
        json_schema_extra={"example": "room_01"}
    )
    title: str = Field(
        ...,
        description="Short summary of the issue.",
        json_schema_extra={"example": "Broken Keycard / Inaccessible Room"}
    )
    category: Optional[str] = Field(
        "maintenance",
        description="Incident category. Supported: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'.",
        json_schema_extra={"example": "maintenance"}
    )
    severity: EscalationSeverityType = Field(
        "high",
        description="Urgency level: 'high', 'medium', or 'low'.",
        json_schema_extra={"example": "high"}
    )
    description: str = Field(
        ...,
        description="Full explanation of the problem encountered.",
        json_schema_extra={"example": "Door handle is detached and room cannot be entered."}
    )
    location_name: Optional[str] = Field(
        None,
        description="Optional facility name if not linked to a shift.",
        json_schema_extra={"example": "Grand Hotel Central"}
    )
    room_name: Optional[str] = Field(
        None,
        description="Optional room name or number if not linked to a shift.",
        json_schema_extra={"example": "Room 102"}
    )
    photo_urls: Optional[List[str]] = Field(
        default_factory=list,
        description="List of uploaded image URLs demonstrating the issue.",
        json_schema_extra={"example": ["https://s3.eu-central-1.amazonaws.com/cleanones-bucket/uploads/door.jpg"]}
    )
    photo_url: Optional[str] = Field(
        None,
        description="Single photo URL fallback.",
        json_schema_extra={"example": "https://s3.eu-central-1.amazonaws.com/cleanones-bucket/uploads/door.jpg"}
    )

class WorkerEscalationResponse(BaseModel):
    """Response returned upon successful creation of a worker escalation."""
    id: str = Field(..., description="Unique escalation identifier.", json_schema_extra={"example": "esc_e33598137b"})
    escalation_id: str = Field(..., description="Unique escalation identifier.", json_schema_extra={"example": "esc_e33598137b"})
    shift_id: Optional[str] = Field(None, description="Linked shift ID.", json_schema_extra={"example": "shift_123"})
    room_id: Optional[str] = Field(None, description="Linked room ID.", json_schema_extra={"example": "room_01"})
    title: str = Field(..., description="Issue title.", json_schema_extra={"example": "Broken Keycard / Inaccessible Room"})
    category: str = Field("maintenance", description="Issue category.", json_schema_extra={"example": "maintenance"})
    severity: str = Field("high", description="Severity level.", json_schema_extra={"example": "high"})
    description: str = Field(..., description="Detailed description.", json_schema_extra={"example": "Door handle is detached."})
    status: str = Field("open", description="Initial status is 'open'.", json_schema_extra={"example": "open"})
    photo_url: Optional[str] = Field(None, description="Primary photo URL.")
    photo_urls: List[str] = Field(default_factory=list, description="All photo URLs.")
    created_at: datetime = Field(..., description="Creation timestamp.")
    message: str = Field("Escalation report submitted successfully", description="Status message.")

class WorkerEscalationDetail(BaseModel):
    """Full detail of a single escalation viewed by a worker."""
    id: str = Field(..., description="Unique escalation ID.", json_schema_extra={"example": "esc_e33598137b"})
    escalation_id: str = Field(..., description="Unique escalation ID.", json_schema_extra={"example": "esc_e33598137b"})
    shift_id: Optional[str] = Field(None, description="Associated shift ID.", json_schema_extra={"example": "shift_123"})
    room_id: Optional[str] = Field(None, description="Associated room ID.", json_schema_extra={"example": "room_01"})
    title: str = Field(..., description="Escalation title.", json_schema_extra={"example": "Broken Keycard / Inaccessible Room"})
    category: str = Field("maintenance", description="Category: maintenance, safety, access, cleaning, equipment, other.")
    severity: str = Field("high", description="Severity: high, medium, low.")
    description: str = Field(..., description="Full problem description.")
    status: str = Field("open", description="Current status: open, in_progress, resolved, closed.")
    status_label: str = Field("Open", description="Human-readable status label: 'Open', 'In Progress', 'Resolved', 'Closed'.")
    location_name: Optional[str] = Field(None, description="Facility or site name.")
    room_name: Optional[str] = Field(None, description="Room number or specific area.")
    photo_url: Optional[str] = Field(None, description="Primary photo URL.")
    photo_urls: List[str] = Field(default_factory=list, description="All photo URLs.")
    reporter: Optional[EscalationReporterDetail] = Field(None, description="Reporter details.")
    notes: Optional[str] = Field(None, description="Resolution notes from manager upon resolution.", json_schema_extra={"example": "Fixed by locksmith."})
    created_at: datetime = Field(..., description="Creation timestamp.")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp.")
    resolved_at: Optional[datetime] = Field(None, description="Timestamp when resolved.")

class WorkerEscalationListResponse(BaseModel):
    """Paginated list of escalations for the authenticated worker."""
    total_count: int = Field(..., description="Total count of escalations matching query.", json_schema_extra={"example": 5})
    page: int = Field(1, description="Current page number.", json_schema_extra={"example": 1})
    limit: int = Field(10, description="Number of items per page.", json_schema_extra={"example": 10})
    has_more: bool = Field(False, description="Whether additional pages exist.", json_schema_extra={"example": False})
    open_count: int = Field(0, description="Worker's open escalations count.", json_schema_extra={"example": 2})
    resolved_count: int = Field(0, description="Worker's resolved escalations count.", json_schema_extra={"example": 3})
    escalations: List[WorkerEscalationDetail] = Field(default_factory=list, description="List of worker's escalation records.")
