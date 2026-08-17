import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.notification import (
    AdminNotificationCenterResponse, AdminNotificationItem
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

admin_notifications_router = APIRouter(prefix="/manager", tags=["Manager Notification Center"])

@admin_notifications_router.get("/notifications", response_model=AdminNotificationCenterResponse, summary="Admin Notification Center (Image Mockup)")
async def get_admin_notification_center(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    admin_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "admin_1")

    query = {"recipient_type": "admin"}
    total_count = await db["notifications"].count_documents(query)
    unread_count = await db["notifications"].count_documents({"recipient_type": "admin", "is_read": False})

    skip = (page - 1) * limit
    cursor = db["notifications"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_notifs = await cursor.to_list(length=limit)

    items = []
    for n in raw_notifs:
        nid = str(n.get("_id") or n.get("id"))
        c_dt = n.get("created_at")
        if not isinstance(c_dt, datetime):
            c_dt = datetime.now(timezone.utc)

        t_str = n.get("time_ago", "Recently")

        items.append(AdminNotificationItem(
            id=nid,
            title=n.get("title", "Notification"),
            message=n.get("message", n.get("content", "")),
            time_ago=t_str,
            notification_type=n.get("notification_type", "general"),
            is_read=n.get("is_read", False),
            created_at=c_dt
        ))

    return AdminNotificationCenterResponse(
        total_count=total_count,
        unread_count=unread_count,
        page=page,
        limit=limit,
        notifications=items
    )

@admin_notifications_router.post("/notifications/mark-all-read", summary="Mark All Notifications as Read ('Mark all read' Button Image Mockup)")
async def mark_all_admin_notifications_read(
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)

    await db["notifications"].update_many(
        {"recipient_type": "admin", "is_read": False},
        {"$set": {"is_read": True, "read_at": now}}
    )

    return {
        "status": "success",
        "unread_count": 0,
        "message": "All notifications marked as read."
    }

@admin_notifications_router.patch("/notifications/{notification_id}/read", summary="Mark Single Notification as Read")
async def mark_single_admin_notification_read(
    notification_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)

    n_query = {"$or": [{"_id": notification_id}, {"id": notification_id}]}
    if ObjectId.is_valid(notification_id):
        n_query["$or"].append({"_id": ObjectId(notification_id)})

    await db["notifications"].update_one(
        n_query,
        {"$set": {"is_read": True, "read_at": now}}
    )

    return {
        "notification_id": notification_id,
        "is_read": True,
        "message": "Notification marked as read."
    }

@admin_notifications_router.delete("/notifications/{notification_id}", summary="Dismiss / Delete Notification ('x' Button Image Mockup)")
async def delete_admin_notification(
    notification_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()

    n_query = {"$or": [{"_id": notification_id}, {"id": notification_id}]}
    if ObjectId.is_valid(notification_id):
        n_query["$or"].append({"_id": ObjectId(notification_id)})

    await db["notifications"].delete_one(n_query)

    return {
        "notification_id": notification_id,
        "message": "Notification dismissed successfully."
    }
