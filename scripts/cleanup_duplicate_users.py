import asyncio
import logging
from bson import ObjectId
from app.core.database import get_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup_duplicates")

async def cleanup_duplicate_users():
    db = get_database()
    
    # 1. Identify all duplicate emails
    pipeline = [
        {"$match": {"email": {"$ne": None, "$exists": True}}},
        {"$group": {
            "_id": "$email",
            "count": {"$sum": 1},
            "docs": {"$push": "$$ROOT"}
        }},
        {"$match": {"count": {"$gt": 1}}}
    ]
    
    duplicate_groups = await db.users.aggregate(pipeline).to_list(100)
    if not duplicate_groups:
        logger.info("No duplicate emails found in users collection.")
        return

    logger.info(f"Found {len(duplicate_groups)} duplicate email groups in users collection.")
    
    # Collections that might reference user IDs
    # Mapping of collection name -> list of fields that can contain a user_id
    ref_fields = {
        "shifts": ["workers.worker_id", "client_id"],
        "shift_executions": ["assigned_workers.worker_id", "client_id"],
        "cleaning_plans": ["client_id"],
        "conversations": ["participants.user_id"],
        "notifications": ["user_id"],
        "worker_availability": ["worker_id"],
        "worker_approval_history": ["worker_id", "user_id"],
        "worker_invoices": ["worker_id"],
        "extra_services": ["client_id", "worker_id"],
        "escalations": ["worker_id", "client_id"],
        "photo_reviews": ["worker_id"],
        "client_notes": ["client_id"],
        "client_reviews": ["client_id"],
    }
    
    total_deleted = 0
    total_migrated = 0
    
    for group in duplicate_groups:
        email = group["_id"]
        docs = group["docs"]
        
        # Sort docs: prioritize approved/active ones, then latest created_at
        # Preferred: account_status == 'active' or approval_status == 'approved'
        def sort_key(d):
            is_approved = 1 if d.get("approval_status") == "approved" or d.get("is_approved") is True else 0
            is_active = 1 if d.get("is_active") is True else 0
            created_at = str(d.get("created_at") or "")
            return (is_approved, is_active, created_at)
        
        docs_sorted = sorted(docs, key=sort_key, reverse=True)
        primary_doc = docs_sorted[0]
        primary_id = primary_doc["_id"]
        duplicate_docs = docs_sorted[1:]
        duplicate_ids = [d["_id"] for d in duplicate_docs]
        
        logger.info(f"Email '{email}': Preserving ID {primary_id} ({primary_doc.get('role')}), removing {len(duplicate_ids)} duplicates.")
        
        # Migrate references for each duplicate ID to primary_id
        for dup_id in duplicate_ids:
            for col_name, fields in ref_fields.items():
                if col_name not in await db.list_collection_names():
                    continue
                col = db[col_name]
                for field in fields:
                    # Check both ObjectId and string formats
                    candidates = [dup_id]
                    if isinstance(dup_id, ObjectId):
                        candidates.append(str(dup_id))
                    elif isinstance(dup_id, str):
                        try:
                            candidates.append(ObjectId(dup_id))
                        except Exception:
                            pass
                    
                    for cand in candidates:
                        # Determine if field is array-nested
                        if "." in field:
                            # e.g. workers.worker_id or participants.user_id
                            array_name, subfield = field.split(".", 1)
                            # Update array elements where subfield == cand
                            # Replace matching elements' subfield with primary_id
                            # In MongoDB positional update: array_name.$.subfield
                            res = await col.update_many(
                                {f"{array_name}.{subfield}": cand},
                                {"$set": {f"{array_name}.$.{subfield}": primary_id}}
                            )
                            if res.modified_count > 0:
                                logger.info(f"Updated {res.modified_count} docs in {col_name}.{field} from {cand} to {primary_id}")
                                total_migrated += res.modified_count
                        else:
                            # Direct field e.g. client_id
                            target_val = primary_id if isinstance(cand, type(primary_id)) else str(primary_id)
                            res = await col.update_many(
                                {field: cand},
                                {"$set": {field: target_val}}
                            )
                            if res.modified_count > 0:
                                logger.info(f"Updated {res.modified_count} docs in {col_name}.{field} from {cand} to {target_val}")
                                total_migrated += res.modified_count

        # Delete duplicates from users collection
        del_res = await db.users.delete_many({"_id": {"$in": duplicate_ids}})
        logger.info(f"Deleted {del_res.deleted_count} duplicate user documents for '{email}'.")
        total_deleted += del_res.deleted_count

    logger.info(f"Cleanup finished! Total duplicate users deleted: {total_deleted}, total references migrated: {total_migrated}")

if __name__ == "__main__":
    asyncio.run(cleanup_duplicate_users())
