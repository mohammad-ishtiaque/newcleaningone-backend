import logging
from pymongo import ASCENDING, DESCENDING
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

async def ensure_database_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Creates essential compound and single indexes on all active collections.
    This converts full collection scans (COLLSCAN) into index scans (IXSCAN),
    reducing query latency from seconds to milliseconds.
    Individual collection indexing is isolated so one issue does not prevent others.
    """
    index_plans = [
        # 1. Users collection
        ("users", [
            ([("email", ASCENDING)], {"unique": True, "sparse": True, "background": True}),
            ([("role", ASCENDING), ("account_status", ASCENDING), ("is_approved", ASCENDING)], {"background": True}),
            ([("worker_type", ASCENDING)], {"background": True}),
            ([("full_name", ASCENDING)], {"background": True}),
        ]),
        # 2. Shifts collection
        ("shifts", [
            ([("date", ASCENDING), ("status", ASCENDING)], {"background": True}),
            ([("workers.worker_id", ASCENDING)], {"background": True}),
            ([("client_id", ASCENDING)], {"background": True}),
            ([("location_id", ASCENDING)], {"background": True}),
            ([("created_at", DESCENDING)], {"background": True}),
        ]),
        # 3. Shift Executions collection
        ("shift_executions", [
            ([("date", ASCENDING), ("status", ASCENDING)], {"background": True}),
            ([("assigned_workers.worker_id", ASCENDING)], {"background": True}),
            ([("plan_id", ASCENDING), ("date", ASCENDING)], {"background": True}),
            ([("client_id", ASCENDING)], {"background": True}),
            ([("location_id", ASCENDING)], {"background": True}),
        ]),
        # 4. Cleaning Plans collection
        ("cleaning_plans", [
            ([("status", ASCENDING), ("is_active", ASCENDING)], {"background": True}),
            ([("client_id", ASCENDING)], {"background": True}),
            ([("location_id", ASCENDING)], {"background": True}),
            ([("created_at", DESCENDING)], {"background": True}),
        ]),
        # 5. Locations collection
        ("locations", [
            ([("client_id", ASCENDING), ("is_active", ASCENDING)], {"background": True}),
            ([("name", ASCENDING)], {"background": True}),
        ]),
        # 6. Rooms collection
        ("rooms", [
            ([("location_id", ASCENDING), ("is_active", ASCENDING)], {"background": True}),
            ([("name", ASCENDING)], {"background": True}),
        ]),
        # 7. Client List collection
        ("client_list", [
            ([("account_status", ASCENDING)], {"background": True}),
            ([("approval_status", ASCENDING)], {"background": True}),
            ([("is_active", ASCENDING)], {"background": True}),
            ([("company_name", ASCENDING)], {"background": True}),
        ]),
        # 8. Photo Reviews collection
        ("photo_reviews", [
            ([("status", ASCENDING), ("date_submitted", DESCENDING)], {"background": True}),
            ([("shift_id", ASCENDING)], {"background": True}),
            ([("review_id", ASCENDING)], {"background": True}),
        ]),
        # 9. Escalations collection
        ("escalations", [
            ([("status", ASCENDING), ("created_at", DESCENDING)], {"background": True}),
            ([("escalation_id", ASCENDING)], {"background": True}),
        ]),
        # 10. Chat Messages & Conversations
        ("chat_messages", [
            ([("conversation_id", ASCENDING), ("created_at", DESCENDING)], {"background": True}),
        ]),
        ("conversations", [
            ([("type", ASCENDING), ("updated_at", DESCENDING)], {"background": True}),
            ([("participants.user_id", ASCENDING)], {"background": True}),
        ]),
        # 11. Notifications collection
        ("notifications", [
            ([("recipient_type", ASCENDING), ("is_read", ASCENDING), ("created_at", DESCENDING)], {"background": True}),
            ([("user_id", ASCENDING), ("created_at", DESCENDING)], {"background": True}),
        ]),
        # 12. Extra Services & Worker Invoices
        ("extra_services", [
            ([("status", ASCENDING), ("created_at", DESCENDING)], {"background": True}),
        ]),
        ("worker_invoices", [
            ([("worker_id", ASCENDING), ("month", ASCENDING)], {"background": True}),
        ]),
        ("shift_drafts", [
            ([("created_at", DESCENDING)], {"background": True}),
        ]),
    ]

    total_created = 0
    for collection_name, indexes in index_plans:
        for keys, kwargs in indexes:
            try:
                await db[collection_name].create_index(keys, **kwargs)
                total_created += 1
            except Exception as e:
                logger.warning(f"Error creating index {keys} on '{collection_name}': {e}")

    logger.info(f"MongoDB database indexes successfully created/verified ({total_created} verified).")

