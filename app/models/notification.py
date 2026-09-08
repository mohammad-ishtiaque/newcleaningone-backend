from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

class NotificationDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    user_id: Optional[str] = None # None if target is all users or specific recipient_type
    recipient_type: str = "all" # worker, client, all, specific
    title: str
    message: str
    notification_type: str = "general" # legal_update, support_reply, shift_assignment, general
    route_type: str = "general" # broad screen category for frontend routing: chat, schedule, escalation, review, support, account, invoice, general
    plan_id: Optional[str] = None
    data: Optional[dict] = Field(default_factory=dict)
    is_read: bool = False
    read_by: list[str] = Field(default_factory=list) # For broadcast notifications, track user IDs who read it
    deleted_by: list[str] = Field(default_factory=list) # For broadcast notifications, track user IDs who deleted it
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
