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
        player_ids: Optional[List[str]] = None,
        plan_id: Optional[str] = None,
        data: Optional[dict] = None
    ) -> dict:
        db = get_database()
        notification = NotificationDB(
            user_id=user_id,
            recipient_type=recipient_type,
            title=title,
            message=message,
            notification_type=notification_type,
            plan_id=plan_id,
            data=data or {}
        )
        doc = notification.model_dump(by_alias=True, exclude={"id"})
        res = await db["notifications"].insert_one(doc)
        doc["_id"] = str(res.inserted_id)

        # Trigger push notification asynchronously via OneSignal
        try:
            push_data = dict(data or {})
            if plan_id:
                push_data["plan_id"] = plan_id
            push_data["notification_type"] = notification_type
            
            is_broadcast = (recipient_type == "all" and not user_id)
            ext_ids = [str(user_id)] if user_id else None

            await self.onesignal.send_notification(
                headings=title,
                contents=message,
                player_ids=player_ids,
                external_user_ids=ext_ids,
                is_broadcast=is_broadcast,
                data=push_data
            )
        except Exception as e:
            print(f"Error sending push notification: {e}")

        return doc

    async def get_user_notifications(self, user_id: str, recipient_type: str, page: int = 1, limit: int = 10) -> dict:
        db = get_database()
        u_str = str(user_id) if user_id else ""
        user_matches = [
            {"user_id": u_str},
            {"user_ids": u_str}
        ]
        if ObjectId.is_valid(u_str):
            user_matches.append({"user_id": ObjectId(u_str)})
            user_matches.append({"user_ids": ObjectId(u_str)})

        query = {
            "$and": [
                {
                    "$or": user_matches + [
                        {"user_id": {"$in": [None, ""]}, "recipient_type": recipient_type},
                        {"user_id": {"$in": [None, ""]}, "recipient_type": "all"},
                        {"user_id": {"$exists": False}, "recipient_type": recipient_type},
                        {"user_id": {"$exists": False}, "recipient_type": "all"}
                    ]
                },
                {
                    "deleted_by": {"$nin": [u_str, ObjectId(u_str)] if ObjectId.is_valid(u_str) else [u_str]}
                }
            ]
        }
        cursor = db["notifications"].find(query).sort("created_at", -1)
        all_notifications = []
        unread_count = 0
        async for doc in cursor:
            doc_uid = str(doc.get("user_id") or "")
            # Strict security isolation: If doc targets another specific user, reject immediately
            if doc_uid and doc_uid != u_str and (not ObjectId.is_valid(u_str) or doc_uid != str(ObjectId(u_str))):
                continue

            doc["_id"] = str(doc["_id"])
            if doc_uid == u_str or (ObjectId.is_valid(u_str) and doc_uid == str(ObjectId(u_str))):
                is_read = doc.get("is_read", False)
            else:
                is_read = u_str in doc.get("read_by", [])
            
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
            if ObjectId.is_valid(notification_id):
                query = {"$or": [{"_id": ObjectId(notification_id)}, {"_id": notification_id}, {"id": notification_id}]}
            else:
                query = {"$or": [{"_id": notification_id}, {"id": notification_id}]}
            doc = await db["notifications"].find_one(query)
            if not doc:
                return False

            if doc.get("user_id") == user_id:
                await db["notifications"].delete_one({"_id": doc["_id"]})
            else:
                await db["notifications"].update_one(
                    {"_id": doc["_id"]},
                    {"$addToSet": {"deleted_by": user_id}}
                )
            return True
        except Exception:
            return False

    async def delete_notification(self, notification_id: str, user_id: str) -> bool:
        return await self.delete_user_notification(notification_id, user_id)

    async def get_notification_detail(self, notification_id: str, user_id: str) -> Optional[dict]:
        db = get_database()
        try:
            obj_id = ObjectId(notification_id)
            query = {"$or": [{"_id": obj_id}, {"_id": notification_id}, {"id": notification_id}]}
        except Exception:
            query = {"$or": [{"_id": notification_id}, {"id": notification_id}]}

        doc = await db["notifications"].find_one(query)
        if not doc:
            return None

        rec_type = doc.get("recipient_type", "all")
        doc_user_id = doc.get("user_id")
        if doc_user_id and str(doc_user_id) != str(user_id):
            if rec_type not in ["all", "worker", "workers", "client", "clients"]:
                return None

        doc["_id"] = str(doc.get("_id"))
        is_read = doc.get("is_read", False)
        if user_id in doc.get("read_by", []):
            is_read = True
        doc["is_read"] = is_read

        # Auto-mark as read when opened
        await self.mark_notification_as_read(notification_id, user_id)
        doc["is_read"] = True

        return doc

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
        worker = await db["users"].find_one({"_id": ObjectId(worker_id)}) if ObjectId.is_valid(worker_id) else await db["users"].find_one({"_id": worker_id})
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

    async def notify_escalation_status_changed(
        self,
        escalation: dict,
        new_status: str,
        manager_user,
        notes: Optional[str] = None
    ) -> Optional[dict]:
        """
        Dispatches targeted push notification, database notification, and WebSocket alert
        to the reporter worker when an escalation status is updated or resolved.
        """
        db = get_database()
        reporter = escalation.get("reporter") or {}
        worker_id = str(reporter.get("worker_id") or escalation.get("reporter_id") or escalation.get("worker_id") or "")
        
        if not worker_id:
            print("[Escalation Notification] No worker_id found on escalation. Skipping worker push.")
            return None

        # Look up worker to get device push token
        worker_doc = None
        if ObjectId.is_valid(worker_id):
            worker_doc = await db["users"].find_one({"_id": ObjectId(worker_id)})
        if not worker_doc:
            worker_doc = await db["users"].find_one({"$or": [{"_id": worker_id}, {"id": worker_id}]})

        player_ids = []
        if worker_doc and worker_doc.get("onesignal_player_id"):
            player_ids = [worker_doc["onesignal_player_id"]]

        manager_name = getattr(manager_user, "full_name", None) or "Manager"
        esc_title = escalation.get("title") or "Issue Report"
        esc_id = escalation.get("escalation_id") or str(escalation.get("_id"))

        if new_status == "resolved":
            heading = f"Escalation Resolved: {esc_title}"
            body = f"Your escalation report has been resolved by {manager_name}."
            if notes:
                body += f" Note: {notes}"
            notif_type = "escalation_resolved"
        elif new_status == "in_progress":
            heading = f"Escalation In Progress: {esc_title}"
            body = f"{manager_name} is actively working on your escalation report."
            if notes:
                body += f" Note: {notes}"
            notif_type = "escalation_in_progress"
        elif new_status == "closed":
            heading = f"Escalation Closed: {esc_title}"
            body = f"Your escalation report has been closed by {manager_name}."
            if notes:
                body += f" Note: {notes}"
            notif_type = "escalation_closed"
        else:
            heading = f"Escalation Status Updated: {esc_title}"
            body = f"Status updated to '{new_status}' by {manager_name}."
            if notes:
                body += f" Note: {notes}"
            notif_type = "escalation_updated"

        extra_data = {
            "escalation_id": esc_id,
            "status": new_status,
            "notes": notes,
            "manager_name": manager_name,
            "shift_id": escalation.get("shift_id")
        }

        # 1. Create in-app notification & send OneSignal push
        notif_doc = await self.create_notification(
            title=heading,
            message=body,
            notification_type=notif_type,
            recipient_type="worker",
            user_id=worker_id,
            player_ids=player_ids if player_ids else None,
            data=extra_data
        )

        # 2. Broadcast real-time WebSocket event to the worker
        try:
            from app.api.chat import ws_manager
            await ws_manager.broadcast_to_users(
                {
                    "event": "escalation_resolved" if new_status == "resolved" else "escalation_status_updated",
                    "data": {
                        "escalation_id": esc_id,
                        "status": new_status,
                        "notes": notes,
                        "resolved_by": manager_name if new_status == "resolved" else None,
                        "updated_by": manager_name,
                        "notification": notif_doc
                    }
                },
                [worker_id]
            )
        except Exception as ws_err:
            print(f"[WebSocket Alert Warning] Failed to broadcast escalation event: {ws_err}")

        return notif_doc

    async def notify_escalation_created(self, escalation: dict, reporter_user) -> Optional[dict]:
        """
        Dispatches in-app notification, push notifications, and WebSocket alert
        to all active managers when a new escalation is submitted by a worker.
        """
        db = get_database()
        esc_title = escalation.get("title") or "New Issue"
        esc_id = escalation.get("escalation_id") or str(escalation.get("_id"))
        reporter_name = getattr(reporter_user, "full_name", None) or "A worker"

        heading = f"New Escalation: {esc_title}"
        body = f"{reporter_name} reported an issue: {escalation.get('description', '')[:120]}"
        
        extra_data = {
            "escalation_id": esc_id,
            "severity": escalation.get("severity", "high"),
            "status": "open",
            "shift_id": escalation.get("shift_id")
        }

        # Collect manager player IDs
        cursor = db["users"].find(
            {"role": {"$in": ["manager", "admin"]}, "onesignal_player_id": {"$ne": None}, "is_active": True},
            {"onesignal_player_id": 1}
        )
        player_ids = [u["onesignal_player_id"] async for u in cursor if u.get("onesignal_player_id")]

        notif_doc = await self.create_notification(
            title=heading,
            message=body,
            notification_type="escalation_created",
            recipient_type="manager",
            player_ids=player_ids if player_ids else None,
            data=extra_data
        )

        # Broadcast WebSocket event to all managers
        try:
            from app.api.chat import ws_manager
            manager_cursor = db["users"].find({"role": {"$in": ["manager", "admin"]}}, {"_id": 1})
            manager_ids = [str(m["_id"]) async for m in manager_cursor]
            if manager_ids:
                await ws_manager.broadcast_to_users(
                    {
                        "event": "new_escalation",
                        "data": {
                            "escalation_id": esc_id,
                            "title": esc_title,
                            "severity": escalation.get("severity", "high"),
                            "reporter_name": reporter_name,
                            "notification": notif_doc
                        }
                    },
                    manager_ids
                )
        except Exception as ws_err:
            print(f"[WebSocket Alert Warning] Failed to broadcast new escalation: {ws_err}")

        return notif_doc
