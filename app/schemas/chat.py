from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse

class ParticipantInfo(BaseModel):
    user_id: str
    name: str
    role: str
    profile_picture: Optional[str] = None

class LastMessageInfo(BaseModel):
    text: str
    sender_id: str
    sender_name: str
    timestamp: str

class ConversationCreate(BaseModel):
    type: Optional[str] = Field(default="direct", json_schema_extra={"example": "direct"})
    target_user_id: Optional[str] = Field(None, json_schema_extra={"example": "admin_123"})
    participant_ids: List[str] = Field(default_factory=list, json_schema_extra={"example": ["admin_123", "worker_456"]})
    shift_id: Optional[str] = Field(None, json_schema_extra={"example": "shift_456"})
    cleaning_plan_id: Optional[str] = Field(None, json_schema_extra={"example": "plan_789"})
    title: Optional[str] = Field(None, json_schema_extra={"example": "Clean Ones"})
    subtitle: Optional[str] = Field(None, json_schema_extra={"example": "Private conversation"})

class ConversationListItemResponse(BaseModel):
    id: str
    type: str = Field(default="direct worker", json_schema_extra={"example": "Direct clients"})  # "direct worker" | "Direct clients" | "Group"
    title: str
    subtitle: Optional[str] = None
    shift_id: Optional[str] = None
    cleaning_plan_id: Optional[str] = None
    avatar_url: Optional[str] = None
    participants_count: int = 0
    last_message: Optional[LastMessageInfo] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

class ConversationDetailResponse(BaseModel):
    id: str
    type: str = Field(default="direct worker", json_schema_extra={"example": "Direct clients"})  # "direct worker" | "Direct clients" | "Group"
    title: str
    subtitle: Optional[str] = None
    shift_id: Optional[str] = None
    cleaning_plan_id: Optional[str] = None
    avatar_url: Optional[str] = None
    participants_count: int = 0
    participants: List[ParticipantInfo] = Field(default_factory=list)
    last_message: Optional[LastMessageInfo] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

ConversationResponse = ConversationDetailResponse

class ReadByInfo(BaseModel):
    user_id: str
    read_at: datetime

class MessageCreate(BaseModel):
    content: str = Field(..., json_schema_extra={"example": "Hello! I have a question about my schedule."})
    attachment_url: Optional[str] = None
    attachment_type: Optional[Literal["image", "document"]] = None

class MessageUpdate(BaseModel):
    content: str = Field(..., json_schema_extra={"example": "Updated message text."})

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_name: str
    sender_role: str
    sender_avatar: Optional[str] = None
    content: str
    attachment_url: Optional[str] = None
    attachment_type: Optional[str] = None
    status: Literal["sent", "delivered", "seen"] = "sent"
    read_by: List[ReadByInfo] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class PaginatedMessagesResponse(BasePaginatedResponse):
    messages: List[MessageResponse] = Field(default_factory=list)

class ParticipantProfileResponse(BaseModel):
    user_id: str
    name: str
    role: str
    role_label: str
    email: str
    phone: str
    current_location_name: str
    client_name: str
    account_status: str = "Active client"
    is_online: bool = True
    profile_picture: Optional[str] = None

class ParticipantDetailItem(BaseModel):
    user_id: str
    name: str
    role: str
    email: Optional[str] = None
    phone: Optional[str] = None
    profile_picture: Optional[str] = None
    position: Optional[str] = None
    company_name: Optional[str] = None
    is_online: bool = True

class ConversationParticipantsResponse(BaseModel):
    conversation_id: str
    conversation_title: str
    total_participants: int
    participants: List[ParticipantDetailItem] = Field(default_factory=list)

class AttachmentUploadResponse(BaseModel):
    attachment_url: str
    attachment_type: str = "image"
    filename: Optional[str] = None
    file_name: Optional[str] = None

    def __init__(self, **data):
        if "file_name" in data and "filename" not in data:
            data["filename"] = data["file_name"]
        elif "filename" in data and "file_name" not in data:
            data["file_name"] = data["filename"]
        super().__init__(**data)

class AddParticipantsRequest(BaseModel):
    user_ids: List[str] = Field(..., json_schema_extra={"example": ["user_123", "worker_456"]})

class RemoveParticipantResponse(BaseModel):
    message: str
    conversation_id: str
    removed_user_id: str
    remaining_participants_count: int

class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, json_schema_extra={"example": "Kafa Automation Cleaning plan & Betopia Group"})
    subtitle: Optional[str] = Field(None, json_schema_extra={"example": "Updated shift instructions"})
    avatar_url: Optional[str] = Field(None, json_schema_extra={"example": "https://example.com/avatar.png"})

class DeleteConversationResponse(BaseModel):
    message: str
    conversation_id: str

class PaginatedConversationsResponse(BasePaginatedResponse):
    conversations: List[ConversationListItemResponse] = Field(default_factory=list)



