import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.notification import (
    AdminNotificationCenterResponse, AdminNotificationItem,
    NotificationBulkDeleteRequest, NotificationBulkDeleteResponse
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

    # Manager-facing notifications are created under a mix of recipient_type
    # values ("admin", "manager") plus broadcast "all" messages (e.g. legal
    # document updates) - matching only "admin" silently hid every
    # recipient_type="manager" notification (escalations, support tickets, etc.)
    # from this center even though push/WebSocket alerts still fired for them.
    recipient_filter = {"recipient_type": {"$in": ["admin", "manager", "all"]}}
    total_count = await db["notifications"].count_documents(recipient_filter)
    unread_count = await db["notifications"].count_documents({**recipient_filter, "is_read": False})

    skip = (page - 1) * limit
    cursor = db["notifications"].find(recipient_filter).sort("created_at", -1).skip(skip).limit(limit)
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
            route_type=n.get("route_type", "general"),
            data=n.get("data") or {},
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
        {"recipient_type": {"$in": ["admin", "manager", "all"]}, "is_read": False},
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

@admin_notifications_router.post("/notifications/bulk-delete", response_model=NotificationBulkDeleteResponse, summary="Bulk Delete Notifications")
async def bulk_delete_admin_notifications(
    payload: NotificationBulkDeleteRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()

    if payload.delete_all:
        query = {"recipient_type": {"$in": ["admin", "manager", "all"]}}
    else:
        ids = payload.notification_ids or []
        if not ids:
            return NotificationBulkDeleteResponse(deleted_count=0, message="0 notification(s) deleted")
        obj_ids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
        or_clauses = [{"_id": {"$in": ids}}, {"id": {"$in": ids}}]
        if obj_ids:
            or_clauses.append({"_id": {"$in": obj_ids}})
        query = {"$or": or_clauses}

    result = await db["notifications"].delete_many(query)
    return NotificationBulkDeleteResponse(deleted_count=result.deleted_count, message=f"{result.deleted_count} notification(s) deleted")
