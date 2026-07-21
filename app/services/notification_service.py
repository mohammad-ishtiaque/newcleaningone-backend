from typing import List, Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.core.database import get_database
from app.models.notification import NotificationDB
from app.services.onesignal_service import OneSignalService

class NotificationService:
    def __init__(self):
        self.onesignal = OneSignalService()

    async def create_notification(
        self,
        title: str,
        message: str,
        notification_type: str = "general",
        recipient_type: str = "all",
        user_id: Optional[str] = None,
        player_ids: Optional[List[str]] = None
    ) -> dict:
        db = get_database()
        notification = NotificationDB(
            user_id=user_id,
            recipient_type=recipient_type,
            title=title,
            message=message,
            notification_type=notification_type
        )
        doc = notification.model_dump(by_alias=True, exclude={"id"})
        res = await db["notifications"].insert_one(doc)
        doc["_id"] = str(res.inserted_id)

        # Trigger push notification asynchronously via OneSignal
        try:
            await self.onesignal.send_notification(
                headings=title,
                contents=message,
                player_ids=player_ids
            )
        except Exception as e:
            print(f"Error sending push notification: {e}")

        return doc

    async def get_user_notifications(self, user_id: str, recipient_type: str, page: int = 1, limit: int = 10) -> dict:
        db = get_database()
        query = {
            "$and": [
                {
                    "$or": [
                        {"user_id": user_id},
                        {"recipient_type": recipient_type},
                        {"recipient_type": "all"}
                    ]
                },
                {
                    "deleted_by": {"$ne": user_id}
                }
            ]
        }
        cursor = db["notifications"].find(query).sort("created_at", -1)
        all_notifications = []
        unread_count = 0
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("user_id") == user_id:
                is_read = doc.get("is_read", False)
            else:
                is_read = user_id in doc.get("read_by", [])
            
            doc["is_read"] = is_read
            if not is_read:
                unread_count += 1
            all_notifications.append(doc)

        total_count = len(all_notifications)
        start = (page - 1) * limit
        end = start + limit
        paginated = all_notifications[start:end]

        return {
            "total_count": total_count,
            "unread_count": unread_count,
            "page": page,
            "limit": limit,
            "notifications": paginated
        }

    async def mark_notification_as_read(self, notification_id: str, user_id: str) -> bool:
        db = get_database()
        try:
            obj_id = ObjectId(notification_id)
        except Exception:
            return False

        doc = await db["notifications"].find_one({"_id": obj_id})
        if not doc:
            return False

        if doc.get("user_id") == user_id:
            await db["notifications"].update_one(
                {"_id": obj_id},
                {"$set": {"is_read": True}}
            )
        else:
            await db["notifications"].update_one(
                {"_id": obj_id},
                {"$addToSet": {"read_by": user_id}}
            )
        return True

    async def delete_user_notification(self, notification_id: str, user_id: str) -> bool:
        db = get_database()
        try:
            obj_id = ObjectId(notification_id)
        except Exception:
            return False

        doc = await db["notifications"].find_one({"_id": obj_id})
        if not doc:
            return False

        if doc.get("user_id") == user_id:
            await db["notifications"].delete_one({"_id": obj_id})
        else:
            await db["notifications"].update_one(
                {"_id": obj_id},
                {"$addToSet": {"deleted_by": user_id}}
            )
        return True

    async def notify_legal_document_update(self, doc_type: str, title: str):
        heading = f"Legal Update: {title}"
        body = f"Our {title} has been updated. Please review the latest terms."
        
        # Collect OneSignal player IDs for active workers and clients if available
        db = get_database()
        player_ids = []
        cursor = db["users"].find(
            {"onesignal_player_id": {"$ne": None}, "is_active": True},
            {"onesignal_player_id": 1}
        )
        async for u in cursor:
            pid = u.get("onesignal_player_id")
            if pid:
                player_ids.append(pid)

        await self.create_notification(
            title=heading,
            message=body,
            notification_type="legal_update",
            recipient_type="all",
            player_ids=player_ids if player_ids else None
        )

    async def notify_support_reply(self, worker_id: str, subject: str):
        db = get_database()
        worker = await db["users"].find_one({"_id": ObjectId(worker_id)})
        player_ids = None
        if worker and worker.get("onesignal_player_id"):
            player_ids = [worker["onesignal_player_id"]]

        heading = "Support Message Reply"
        body = f"Admin has replied to your support request: '{subject}'"
        
        await self.create_notification(
            title=heading,
            message=body,
            notification_type="support_reply",
            recipient_type="worker",
            user_id=worker_id,
            player_ids=player_ids
        )
