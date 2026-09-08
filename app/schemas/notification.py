from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from app.schemas.common import BasePaginatedResponse

class NotificationResponse(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: Optional[str] = None
    recipient_type: str
    title: str
    message: str
    notification_type: str
    route_type: str = "general"
    plan_id: Optional[str] = None
    data: Optional[dict] = None
    is_read: bool = False
    created_at: datetime

    class Config:
        populate_by_name = True
        from_attributes = True

class NotificationListResponse(BasePaginatedResponse):
    unread_count: int = 0
    notifications: List[NotificationResponse] = Field(default_factory=list)

class AdminNotificationItem(BaseModel):
    id: str
    title: str
    message: str
    time_ago: str
    notification_type: str
    route_type: str = "general"
    data: Optional[dict] = None
    is_read: bool = False
    created_at: datetime

class AdminNotificationCenterResponse(BasePaginatedResponse):
    unread_count: int = 0
    notifications: List[AdminNotificationItem] = Field(default_factory=list)

class NotificationBulkDeleteRequest(BaseModel):
    notification_ids: Optional[List[str]] = Field(default=None, description="Specific notification IDs to delete. Ignored if delete_all is true.")
    delete_all: bool = Field(default=False, description="Delete every notification visible to the caller, ignoring notification_ids.")

class NotificationBulkDeleteResponse(BaseModel):
    deleted_count: int
    message: str

