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
from app.api.admin.profile_company import require_admin

admin_notifications_router = APIRouter(prefix="/admin", tags=["Admin Notification Center"])

@admin_notifications_router.get("/notifications", response_model=AdminNotificationCenterResponse, summary="Admin Notification Center (Image Mockup)")
async def get_admin_notification_center(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
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

    if not items:
        # Default mock notifications matching Image mockup
        now = datetime.now(timezone.utc)
        mock_data = [
            ("notif_1", "New Escalation", "Urgent: Water leak reported in Hilton Rotterdam Room 701 by Emma Smit", "2 min ago", "escalation", False),
            ("notif_2", "GPS Melding", "Noah Bos is located outside the designated area at UMC Utrecht", "5 min ago", "gps_alert", False),
            ("notif_3", "Photo Rejected", "AI analysis failed for bathroom photo Room 305 - sharpnessScore 42/100", "12 min ago", "photo_rejected", False),
            ("notif_4", "Service Completed", "Lucas Meijer completed service at NH Hotel Groningen (100% rooms done)", "18 min ago", "service_completed", False),
            ("notif_5", "Late Check-in", "Anna Mulder is 15 minutes late for service at Keizersgracht Kantoren Amsterdam", "25 min ago", "late_checkin", True),
            ("notif_6", "Photos Waiting for Review", "12 photos await review by manager in 3 locations", "35 min ago", "photo_review_pending", True),
            ("notif_7", "Service Started", "Milan Dekker checked into Leiden University Hospital and started service #1048", "42 min ago", "service_started", True),
            ("notif_8", "App Bijgewerkt", "New cleaning planner is synchronized with all devices", "1 hr ago", "app_update", True)
        ]
        unread_count = 4
        total_count = len(mock_data)
        for nid, title, msg, time_lbl, n_type, is_r in mock_data:
            items.append(AdminNotificationItem(
                id=nid,
                title=title,
                message=msg,
                time_ago=time_lbl,
                notification_type=n_type,
                is_read=is_r,
                created_at=now
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
    current_user: UserInDB = Depends(require_admin)
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
    current_user: UserInDB = Depends(require_admin)
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
    current_user: UserInDB = Depends(require_admin)
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
