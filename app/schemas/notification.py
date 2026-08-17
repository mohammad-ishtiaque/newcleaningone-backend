from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class NotificationResponse(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: Optional[str] = None
    recipient_type: str
    title: str
    message: str
    notification_type: str
    plan_id: Optional[str] = None
    data: Optional[dict] = None
    is_read: bool = False
    created_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class NotificationListResponse(BaseModel):
    total_count: int
    unread_count: int
    page: int
    limit: int
    notifications: List[NotificationResponse]

class AdminNotificationItem(BaseModel):
    id: str
    title: str
    message: str
    time_ago: str
    notification_type: str
    is_read: bool = False
    created_at: datetime

class AdminNotificationCenterResponse(BaseModel):
    total_count: int
    unread_count: int
    page: int
    limit: int
    notifications: List[AdminNotificationItem] = Field(default_factory=list)

