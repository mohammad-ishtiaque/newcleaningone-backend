import logging
from pymongo import ASCENDING, DESCENDING
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

async def ensure_database_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Creates essential compound and single indexes on all active collections.
    This converts full collection scans (COLLSCAN) into index scans (IXSCAN),
    reducing query latency from seconds to milliseconds.
    """
    try:
        # 1. Users collection
        await db["users"].create_index([("email", ASCENDING)], unique=True, sparse=True, background=True)
        await db["users"].create_index([("role", ASCENDING), ("account_status", ASCENDING), ("is_approved", ASCENDING)], background=True)
        await db["users"].create_index([("worker_type", ASCENDING)], background=True)
        await db["users"].create_index([("full_name", ASCENDING)], background=True)

        # 2. Shifts collection
        await db["shifts"].create_index([("date", ASCENDING), ("status", ASCENDING)], background=True)
        await db["shifts"].create_index([("workers.worker_id", ASCENDING)], background=True)
        await db["shifts"].create_index([("client_id", ASCENDING)], background=True)
        await db["shifts"].create_index([("location_id", ASCENDING)], background=True)
        await db["shifts"].create_index([("created_at", DESCENDING)], background=True)

        # 3. Shift Executions collection
        await db["shift_executions"].create_index([("date", ASCENDING), ("status", ASCENDING)], background=True)
        await db["shift_executions"].create_index([("assigned_workers.worker_id", ASCENDING)], background=True)
        await db["shift_executions"].create_index([("plan_id", ASCENDING), ("date", ASCENDING)], background=True)
        await db["shift_executions"].create_index([("client_id", ASCENDING)], background=True)
        await db["shift_executions"].create_index([("location_id", ASCENDING)], background=True)

        # 4. Cleaning Plans collection
        await db["cleaning_plans"].create_index([("status", ASCENDING), ("is_active", ASCENDING)], background=True)
        await db["cleaning_plans"].create_index([("client_id", ASCENDING)], background=True)
        await db["cleaning_plans"].create_index([("location_id", ASCENDING)], background=True)
        await db["cleaning_plans"].create_index([("created_at", DESCENDING)], background=True)

        # 5. Locations collection
        await db["locations"].create_index([("client_id", ASCENDING), ("is_active", ASCENDING)], background=True)
        await db["locations"].create_index([("name", ASCENDING)], background=True)

        # 6. Rooms collection
        await db["rooms"].create_index([("location_id", ASCENDING), ("is_active", ASCENDING)], background=True)
        await db["rooms"].create_index([("name", ASCENDING)], background=True)

        # 7. Client List collection
        await db["client_list"].create_index([("account_status", ASCENDING)], background=True)
        await db["client_list"].create_index([("approval_status", ASCENDING)], background=True)
        await db["client_list"].create_index([("is_active", ASCENDING)], background=True)
        await db["client_list"].create_index([("company_name", ASCENDING)], background=True)

        # 8. Photo Reviews collection
        await db["photo_reviews"].create_index([("status", ASCENDING), ("date_submitted", DESCENDING)], background=True)
        await db["photo_reviews"].create_index([("shift_id", ASCENDING)], background=True)
        await db["photo_reviews"].create_index([("review_id", ASCENDING)], background=True)

        # 9. Escalations collection
        await db["escalations"].create_index([("status", ASCENDING), ("created_at", DESCENDING)], background=True)
        await db["escalations"].create_index([("escalation_id", ASCENDING)], background=True)

        # 10. Chat Messages & Conversations
        await db["chat_messages"].create_index([("conversation_id", ASCENDING), ("created_at", DESCENDING)], background=True)
        await db["conversations"].create_index([("type", ASCENDING), ("updated_at", DESCENDING)], background=True)
        await db["conversations"].create_index([("participants.user_id", ASCENDING)], background=True)

        # 11. Notifications collection
        await db["notifications"].create_index([("recipient_type", ASCENDING), ("is_read", ASCENDING), ("created_at", DESCENDING)], background=True)
        await db["notifications"].create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], background=True)

        # 12. Extra Services & Worker Invoices
        await db["extra_services"].create_index([("status", ASCENDING), ("created_at", DESCENDING)], background=True)
        await db["worker_invoices"].create_index([("worker_id", ASCENDING), ("month", ASCENDING)], background=True)
        await db["shift_drafts"].create_index([("created_at", DESCENDING)], background=True)

        logger.info("MongoDB database indexes successfully created/verified.")
    except Exception as e:
        logger.warning(f"Error ensuring database indexes: {e}")
