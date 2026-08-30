from typing import Union, List, Set, Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.models.user import UserInDB


async def resolve_client_id_aliases(client_identifier: Union[UserInDB, str, None], db) -> List[str]:
    """
    Collects all possible database identifier aliases for a client across 'users', 'client_list', and 'locations'.
    Seamlessly handles both BSON ObjectId and string representations, matching user IDs (e.g. 6a8ad0436c19168573191e1b),
    legacy client_list IDs (e.g. cli_5f58146bc7), email, and company_name links.
    """
    if not client_identifier:
        return []

    client_ids: Set[str] = set()
    email: Optional[str] = None
    company_name: Optional[str] = None

    if isinstance(client_identifier, UserInDB):
        uid = str(getattr(client_identifier, "id", None) or getattr(client_identifier, "_id", None) or "")
        if uid:
            client_ids.add(uid)
        email = getattr(client_identifier, "email", None)
        company_name = getattr(client_identifier, "company_name", None)
    elif isinstance(client_identifier, str):
        cid = str(client_identifier).strip()
        if cid:
            client_ids.add(cid)

        # Check users collection with both string and ObjectId matching
        user_queries = [{"_id": cid}, {"id": cid}]
        if ObjectId.is_valid(cid):
            user_queries.append({"_id": ObjectId(cid)})

        u_doc = await db["users"].find_one({"$or": user_queries})
        if u_doc:
            if "_id" in u_doc:
                client_ids.add(str(u_doc["_id"]))
            if "id" in u_doc and u_doc["id"]:
                client_ids.add(str(u_doc["id"]))
            email = u_doc.get("email")
            company_name = u_doc.get("company_name")

    # Check client_list collection for any matching ID, user_id, email, or company_name
    query_parts = []
    if client_ids:
        c_list_ids = list(client_ids)
        query_parts.append({"_id": {"$in": c_list_ids}})
        query_parts.append({"id": {"$in": c_list_ids}})
        query_parts.append({"user_id": {"$in": c_list_ids}})
        oid_list = [ObjectId(x) for x in c_list_ids if ObjectId.is_valid(x)]
        if oid_list:
            query_parts.append({"_id": {"$in": oid_list}})
            query_parts.append({"user_id": {"$in": oid_list}})
    if email:
        query_parts.append({"email": email})
    if company_name:
        query_parts.append({"company_name": company_name})

    if query_parts:
        c_cursor = db["client_list"].find({"$or": query_parts})
        c_docs = await c_cursor.to_list(length=30)
        for c in c_docs:
            if "_id" in c:
                client_ids.add(str(c["_id"]))
            if "id" in c and c["id"]:
                client_ids.add(str(c["id"]))
            if "user_id" in c and c["user_id"]:
                client_ids.add(str(c["user_id"]))

    # Check locations collection to find any legacy client_id linked to this company_name
    if company_name:
        loc_cursor = db["locations"].find({"company_name": company_name})
        loc_docs = await loc_cursor.to_list(length=20)
        for l in loc_docs:
            loc_cid = l.get("client_id")
            if loc_cid:
                client_ids.add(str(loc_cid))

    return list(client_ids)


async def get_or_sync_client_doc(client_identifier: Union[UserInDB, str], db) -> Optional[dict]:
    """
    Finds or synthesizes a client document in 'client_list' for any valid client identifier or UserInDB.
    Supports ObjectId and string lookups. Ensures Admin/Manager endpoints can manage locations and plans.
    """
    if isinstance(client_identifier, UserInDB):
        client_id = str(getattr(client_identifier, "id", None) or getattr(client_identifier, "_id", None) or "")
        email = getattr(client_identifier, "email", None)
        company_name = getattr(client_identifier, "company_name", None)
        user_doc = getattr(client_identifier, "model_dump", None) and client_identifier.model_dump() or None
    else:
        client_id = str(client_identifier).strip()
        email = None
        company_name = None
        user_doc = None

    # Check client_list first
    c_query = [{"_id": client_id}, {"id": client_id}, {"user_id": client_id}]
    if ObjectId.is_valid(client_id):
        c_query.append({"_id": ObjectId(client_id)})
        c_query.append({"user_id": ObjectId(client_id)})
    if email:
        c_query.append({"email": email})
    if company_name:
        c_query.append({"company_name": company_name})

    c_doc = await db["client_list"].find_one({"$or": c_query})
    if c_doc:
        return c_doc

    # Look up in users collection if not passed directly
    if not user_doc:
        user_queries = [{"_id": client_id}, {"id": client_id}]
        if ObjectId.is_valid(client_id):
            user_queries.append({"_id": ObjectId(client_id)})
        user_doc = await db["users"].find_one({"$or": user_queries})

    if user_doc and (user_doc.get("role") == "client" or str(user_doc.get("role")) == "RoleEnum.client"):
        u_email = user_doc.get("email")
        u_company = user_doc.get("company_name")

        # Check client_list by email or company_name again
        search_cl = []
        if u_email:
            search_cl.append({"email": u_email})
        if u_company:
            search_cl.append({"company_name": u_company})
        if search_cl:
            c_doc = await db["client_list"].find_one({"$or": search_cl})
            if c_doc:
                return c_doc

        # Synthesize a client_list record from user_doc
        u_id = str(user_doc.get("_id") or user_doc.get("id") or client_id)
        now = datetime.now(timezone.utc)
        synced_doc = {
            "_id": u_id,
            "id": u_id,
            "user_id": u_id,
            "company_name": user_doc.get("company_name") or user_doc.get("full_name") or "Client",
            "primary_contact_name": user_doc.get("full_name") or "Client",
            "email": user_doc.get("email", ""),
            "phone": user_doc.get("phone", "") or user_doc.get("phone_number", ""),
            "created_at": user_doc.get("created_at") or now,
            "updated_at": now,
            "is_signup": True,
            "status": "active",
            "total_locations_count": 0
        }
        await db["client_list"].insert_one(synced_doc)
        return synced_doc

    return None
