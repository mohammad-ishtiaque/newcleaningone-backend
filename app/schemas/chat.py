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
    type: Literal["direct", "group"] = Field(..., json_schema_extra={"example": "direct"})
    target_user_id: Optional[str] = Field(None, json_schema_extra={"example": "admin_123"})
    shift_id: Optional[str] = Field(None, json_schema_extra={"example": "shift_456"})
    title: Optional[str] = Field(None, json_schema_extra={"example": "Clean Ones"})
    subtitle: Optional[str] = Field(None, json_schema_extra={"example": "Private conversation"})

class ConversationResponse(BaseModel):
    id: str
    type: Literal["direct", "group"]
    title: str
    subtitle: Optional[str] = None
    shift_id: Optional[str] = None
    participants: List[ParticipantInfo] = Field(default_factory=list)
    last_message: Optional[LastMessageInfo] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

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

class AttachmentUploadResponse(BaseModel):
    attachment_url: str
    attachment_type: str = "image"
    filename: str

class PaginatedConversationsResponse(BasePaginatedResponse):
    conversations: List[ConversationResponse] = Field(default_factory=list)


