import uuid
import asyncio
from datetime import datetime, timezone, date
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.client_list import (
    ClientListCreate, ClientListUpdate, ClientListResponse, ClientListPaginatedResponse,
    ContractRenewRequest, ClientGridDropdownItem, ClientGridDropdownPaginatedResponse,
    ClientOverviewListPaginatedResponse, ClientOverviewItemResponse, ClientDashboardOverviewResponse, ClientContractDetailsResponse
)
from app.schemas.client_approvals import (
    ClientApproveRequest, ClientRejectRequest, PendingClientApprovalsPaginatedResponse
)
from app.models.user import UserInDB, RoleEnum
from app.schemas.user import UserResponse
from app.api.admin.profile_company import require_manager
from app.security.password import get_password_hash, generate_temporary_password

client_mgmt_router = APIRouter(prefix="/manager", tags=["Manager Client Management"])

def _format_date_human(d_val) -> str:
    if not d_val:
        return "31 Dec 2026"
    try:
        if isinstance(d_val, str):
            dt = datetime.strptime(d_val, "%Y-%m-%d")
        elif isinstance(d_val, datetime):
            dt = d_val
        elif isinstance(d_val, date):
            dt = datetime.combine(d_val, datetime.min.time())
        else:
            return "31 Dec 2026"
        return dt.strftime("%d %b %Y")
    except Exception:
        return "31 Dec 2026"

def _format_client_response(doc: dict, temporary_password: Optional[str] = None) -> ClientListResponse:
    c_id = str(doc.get("_id") or doc.get("id"))
    c_at = doc.get("created_at") if isinstance(doc.get("created_at"), datetime) else datetime.now(timezone.utc)
    u_at = doc.get("updated_at") if isinstance(doc.get("updated_at"), datetime) else datetime.now(timezone.utc)

    admin_info = doc.get("admin")
    if not admin_info:
        admin_info = {"id": "unknown", "name": "Admin", "profile_picture": None}

    contract_status = doc.get("contract_status", "active")
    exp_date_str = doc.get("license_expiration_date")
    if exp_date_str:
        try:
            exp_date = datetime.strptime(exp_date_str.replace('.', '-'), "%Y-%m-%d").date()
            if exp_date < datetime.now(timezone.utc).date():
                contract_status = "expired"
            else:
                contract_status = "active"
        except Exception:
            pass

    return ClientListResponse(
        id=c_id,
        _id=c_id,
        admin=admin_info,
        company_name=doc.get("company_name") or "",
        industry=doc.get("industry", "Corporate"),
        status=doc.get("status", "active"),
        primary_contact_name=doc.get("primary_contact_name") or "",
        name=doc.get("primary_contact_name") or "",
        email=doc.get("email") or "",
        phone=doc.get("phone") or "",
        is_signup=doc.get("is_signup", False),
        temporary_password=temporary_password,
        locations_count=doc.get("total_locations_count", 0),
        contract_status=contract_status,
        license_expiration_date=doc.get("license_expiration_date"),
        created_at=c_at,
        updated_at=u_at
    )

# ================================
# 1. Base Client CRUD & Grid (Image 1)
# ================================

@client_mgmt_router.get("/dropdowns/clients", response_model=ClientGridDropdownPaginatedResponse, summary="List All Clients")
async def list_clients(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    is_signup: Optional[bool] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    conditions = [{"status": {"$ne": "deleted"}}]
    
    if is_signup is not None:
        signed_in_client_emails = await db["users"].distinct("email", {"role": "client", "last_login": {"$ne": None}})
        if is_signup:
            conditions.append({
                "$or": [
                    {"is_signup": True},
                    {"email": {"$in": signed_in_client_emails}}
                ]
            })
        else:
            conditions.append({
                "$and": [
                    {"is_signup": {"$ne": True}},
                    {"email": {"$nin": signed_in_client_emails}}
                ]
            })

    if search:
        conditions.append({
            "$or": [
                {"company_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}},
                {"industry": {"$regex": search, "$options": "i"}}
            ]
        })

    query = {"$and": conditions} if len(conditions) > 1 else conditions[0]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    emails = [c.get("email").lower().strip() for c in raw_clients if c.get("email")]
    user_signup_map = {}
    if emails:
        async for u in db["users"].find({"email": {"$in": emails}, "role": "client"}):
            u_email = (u.get("email") or "").lower().strip()
            user_signup_map[u_email] = bool(u.get("last_login"))

    clients = []
    for c in raw_clients:
        c_email = (c.get("email") or "").lower().strip()
        client_is_signup = bool(c.get("is_signup") is True or user_signup_map.get(c_email, False))

        clients.append(ClientGridDropdownItem(
            id=str(c.get("_id") or c.get("id")),
            primary_contact_name=c.get("primary_contact_name", ""),
            company_name=c.get("company_name", ""),
            is_signup=client_is_signup
        ))

    return ClientGridDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients
    )

@client_mgmt_router.post("/clients", response_model=ClientListResponse, status_code=status.HTTP_201_CREATED, summary="Add New Client (Image 1 Modal)")
async def create_client(
    client_in: ClientListCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    email_clean = client_in.email.lower().strip()
    existing = await db["client_list"].find_one({"email": email_clean})
    if existing:
        raise HTTPException(status_code=400, detail="Client with this email already exists")

    user_existing = await db["users"].find_one({"email": email_clean})
    now = datetime.now(timezone.utc)
    temp_pwd = None
    
    # Auto-approve existing user if they are pending to avoid identity split / lockout
    if user_existing and user_existing.get("role") == "client":
        if user_existing.get("approval_status") != "approved":
            await db["users"].update_one(
                {"_id": user_existing["_id"]},
                {"$set": {
                    "is_approved": True,
                    "approval_status": "approved",
                    "is_active": True,
                    "updated_at": now
                }}
            )
        is_signup = bool(user_existing.get("last_login"))
    elif not user_existing:
        # Create client user login credentials in users collection
        temp_pwd = generate_temporary_password()
        hashed_pwd = get_password_hash(temp_pwd)
        client_user_doc = {
            "full_name": client_in.primary_contact_name,
            "email": email_clean,
            "phone": client_in.phone,
            "company_name": client_in.company_name,
            "hashed_password": hashed_pwd,
            "role": "client",
            "is_active": True,
            "is_verified": True,
            "is_approved": True,
            "approval_status": "approved",
            "is_admin_created": True,
            "is_temporary_password": True,
            "temporary_password_created_at": now,
            "created_at": now,
            "updated_at": now,
            "last_login": None
        }
        await db["users"].insert_one(client_user_doc)
        is_signup = False
    else:
        is_signup = bool(user_existing.get("last_login"))

    client_id = f"cli_{uuid.uuid4().hex[:10]}"
    doc = {
        "_id": client_id,
        "id": client_id,
        "admin": {
            "id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
            "name": current_user.full_name,
            "profile_picture": current_user.profile_photo
        },
        "company_name": client_in.company_name,
        "industry": client_in.industry or "Corporate",
        "primary_contact_name": client_in.primary_contact_name,
        "email": email_clean,
        "phone": client_in.phone,
        "status": client_in.status if client_in.status else ("active" if is_signup else "pending"),
        "is_signup": is_signup,
        "license_expiration_date": client_in.license_expiration_date,
        "is_active": True,
        "total_locations_count": 0,
        "active_contracts_count": 1,
        "created_at": now,
        "updated_at": now
    }

    await db["client_list"].insert_one(doc)

    # Send credentials email asynchronously in background
    if temp_pwd:
        from app.services.email_service import EmailService
        asyncio.create_task(EmailService.send_credentials_email(
            to_email=email_clean,
            full_name=client_in.primary_contact_name,
            role="Client",
            password=temp_pwd
        ))

    return _format_client_response(doc, temporary_password=temp_pwd)


@client_mgmt_router.get("/clients", response_model=ClientOverviewListPaginatedResponse, summary="List All Clients Grid (Image 1)")
async def list_clients_grid(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    is_signup: Optional[bool] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    conditions = [{"status": {"$ne": "deleted"}}]
    
    if is_signup is not None:
        signed_in_client_emails = await db["users"].distinct("email", {"role": "client", "last_login": {"$ne": None}})
        if is_signup:
            conditions.append({
                "$or": [
                    {"is_signup": True},
                    {"email": {"$in": signed_in_client_emails}}
                ]
            })
        else:
            conditions.append({
                "$and": [
                    {"is_signup": {"$ne": True}},
                    {"email": {"$nin": signed_in_client_emails}}
                ]
            })

    if search:
        conditions.append({
            "$or": [
                {"company_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}}
            ]
        })

    query = {"$and": conditions} if len(conditions) > 1 else conditions[0]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    client_ids = [str(c.get("_id") or c.get("id")) for c in raw_clients]
    all_emails = [c.get("email").lower().strip() for c in raw_clients if c.get("email")]

    # Batch count locations by client_id in a single aggregation
    loc_count_map = {}
    if client_ids:
        pipeline = [
            {"$match": {"client_id": {"$in": client_ids}}},
            {"$group": {"_id": "$client_id", "count": {"$sum": 1}}}
        ]
        async for doc in db["locations"].aggregate(pipeline):
            loc_count_map[str(doc["_id"])] = doc["count"]

    # Batch lookup users for signup status (has user signed in at least once?)
    user_signup_map = {}
    if all_emails:
        async for u in db["users"].find({"email": {"$in": all_emails}, "role": "client"}):
            u_email = (u.get("email") or "").lower().strip()
            user_signup_map[u_email] = bool(u.get("last_login"))

    items = []
    for c in raw_clients:
        cid = str(c.get("_id") or c.get("id"))
        loc_count = loc_count_map.get(cid, 0)
        
        contract_status = c.get("contract_status", "active")
        exp_date_str = c.get("license_expiration_date")
        if exp_date_str:
            try:
                exp_date = datetime.strptime(exp_date_str.replace('.', '-'), "%Y-%m-%d").date()
                if exp_date < datetime.now(timezone.utc).date():
                    contract_status = "expired"
                else:
                    contract_status = "active"
            except Exception:
                pass

        c_email = (c.get("email") or "").lower().strip()
        user_has_logged_in = user_signup_map.get(c_email, False)
        is_client_signup = bool(c.get("is_signup") is True or user_has_logged_in)

        # Status:
        # If client has signed in (is_signup: True), ensure status is active
        # If client has not signed in, default status is pending
        st = c.get("status")
        if is_client_signup:
            st = st if (st and st != "pending") else "active"
        else:
            st = "pending" if st in ["pending", "active"] else (st or "pending")

        # Background self-heal for client_list document
        if c.get("is_signup") != is_client_signup or c.get("status") != st:
            asyncio.create_task(db["client_list"].update_one(
                {"_id": c["_id"]},
                {"$set": {"is_signup": is_client_signup, "status": st, "updated_at": datetime.now(timezone.utc)}}
            ))

        items.append(ClientOverviewItemResponse(
            id=cid,
            company_name=c.get("company_name", "Client Company"),
            industry=c.get("industry", "Corporate"),
            status=st,
            primary_contact_name=c.get("primary_contact_name", ""),
            email=c.get("email", ""),
            phone=c.get("phone", ""),
            is_signup=is_client_signup,
            locations_count=loc_count,
            contract_status=contract_status,
            created_at=c.get("created_at") if isinstance(c.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=c.get("updated_at") if isinstance(c.get("updated_at"), datetime) else datetime.now(timezone.utc)
        ))

    return ClientOverviewListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=items
    )

@client_mgmt_router.get("/clients/deleted-list", response_model=ClientOverviewListPaginatedResponse, summary="List Deleted Clients")
async def list_deleted_clients(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    is_signup: Optional[bool] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    conditions = [{"status": "deleted"}]
    if is_signup is not None:
        signed_in_client_emails = await db["users"].distinct("email", {"role": "client", "last_login": {"$ne": None}})
        if is_signup:
            conditions.append({
                "$or": [
                    {"is_signup": True},
                    {"email": {"$in": signed_in_client_emails}}
                ]
            })
        else:
            conditions.append({
                "$and": [
                    {"is_signup": {"$ne": True}},
                    {"email": {"$nin": signed_in_client_emails}}
                ]
            })

    if search:
        conditions.append({
            "$or": [
                {"company_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}}
            ]
        })

    query = {"$and": conditions} if len(conditions) > 1 else conditions[0]
    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    all_emails = [c.get("email").lower().strip() for c in raw_clients if c.get("email")]
    user_signup_map = {}
    if all_emails:
        async for u in db["users"].find({"email": {"$in": all_emails}, "role": "client"}):
            u_email = (u.get("email") or "").lower().strip()
            user_signup_map[u_email] = bool(u.get("last_login"))

    clients = []
    for c in raw_clients:
        c_email = (c.get("email") or "").lower().strip()
        deleted_is_signup = bool(c.get("is_signup") is True or user_signup_map.get(c_email, False))

        clients.append(ClientOverviewItemResponse(
            id=str(c.get("_id") or c.get("id")),
            company_name=c.get("company_name", ""),
            industry=c.get("industry", ""),
            status=c.get("status", "deleted"),
            primary_contact_name=c.get("primary_contact_name", ""),
            email=c.get("email", ""),
            phone=c.get("phone", ""),
            is_signup=deleted_is_signup,
            locations_count=c.get("total_locations_count", 0),
            contract_status=c.get("contract_status", "no_contract"),
            created_at=c.get("created_at") if isinstance(c.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=c.get("updated_at") if isinstance(c.get("updated_at"), datetime) else datetime.now(timezone.utc)
        ))

    return ClientOverviewListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients
    )


from app.schemas.client_list import (
    ClientListCreate, ClientListUpdate, ClientListResponse, ClientListPaginatedResponse,
    ContractRenewRequest, ClientGridDropdownItem, ClientGridDropdownPaginatedResponse,
    ClientOverviewListPaginatedResponse, ClientOverviewItemResponse, ClientDashboardOverviewResponse, ClientContractDetailsResponse,
    ClientBulkImportResult
)
from app.api.admin.client_csv_utils import (
    generate_client_csv_template,
    parse_and_validate_client_csv,
    export_clients_to_csv
)
from fastapi import Response

@client_mgmt_router.get("/clients/pending-approvals", response_model=PendingClientApprovalsPaginatedResponse, summary="List Pending Client Approvals")
async def list_pending_client_approvals(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"role": "client", "approval_status": "pending"}
    
    total_count = await db["users"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["users"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    users = await cursor.to_list(length=limit)
    
    for u in users:
        u["id"] = str(u.get("_id"))
    
    has_more = (skip + len(users)) < total_count
    
    return PendingClientApprovalsPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        users=[UserResponse(**u) for u in users]
    )

@client_mgmt_router.get("/clients/bulk-import/template", summary="Download Client CSV Template")
async def download_client_import_template(
    current_user: UserInDB = Depends(require_manager)
):
    template_content = generate_client_csv_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import_clients_template.csv"}
    )

@client_mgmt_router.post("/clients/bulk-import", response_model=ClientBulkImportResult, summary="Bulk Import Clients CSV")
async def bulk_import_clients_csv(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for client bulk import")

    content_bytes = await file.read()
    content_str = content_bytes.decode("utf-8-sig", errors="ignore")

    db = get_database()
    result = await parse_and_validate_client_csv(content_str, db)
    return result

@client_mgmt_router.get("/clients/export", summary="Export Clients to CSV")
async def export_clients_csv(
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    raw_clients = await db["client_list"].find({"status": {"$ne": "deleted"}}).sort("created_at", -1).to_list(length=1000)
    csv_content = export_clients_to_csv(raw_clients)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clients_export.csv"}
    )

@client_mgmt_router.get("/clients/{client_id}", response_model=ClientListResponse, summary="Get Client by ID")
async def get_client_by_id(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    doc = await db["client_list"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Client not found")
    return _format_client_response(doc)


@client_mgmt_router.patch("/clients/{client_id}", response_model=ClientListResponse, summary="Update Client Details")
async def update_client(
    client_id: str,
    client_in: ClientListUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    doc = await db["client_list"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Client not found")

    fields = client_in.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)

    await db["client_list"].update_one(query, {"$set": fields})
    updated = await db["client_list"].find_one(query)
    return _format_client_response(updated)


@client_mgmt_router.delete("/clients/{client_id}", status_code=status.HTTP_200_OK, summary="Delete Client")
async def delete_client(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": client_id}, {"id": client_id}]}

    # 1. Find the client first to get email
    client_doc = await db["client_list"].find_one(query)
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client not found")

    now = datetime.now(timezone.utc)

    # 2. Soft delete the client_list profile
    await db["client_list"].update_one(
        query,
        {"$set": {"status": "deleted", "is_active": False, "updated_at": now}}
    )

    # 3. Soft delete the user login profile (block signin)
    email = client_doc.get("email")
    if email:
        await db["users"].update_one(
            {"email": email, "role": "client"},
            {"$set": {"is_active": False, "updated_at": now}}
        )

    return {"message": "Client deleted successfully"}


@client_mgmt_router.post("/clients/{client_id}/restore", status_code=status.HTTP_200_OK, summary="Restore Deleted Client")
async def restore_client(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": "deleted"}
    
    client_doc = await db["client_list"].find_one(query)
    if not client_doc:
        raise HTTPException(status_code=404, detail="Deleted client not found")
        
    now = datetime.now(timezone.utc)
    
    # Restore client
    await db["client_list"].update_one(
        query,
        {"$set": {"status": "active", "is_active": True, "updated_at": now}}
    )
    
    # Restore user
    email = client_doc.get("email")
    if email:
        await db["users"].update_one(
            {"email": email, "role": "client"},
            {"$set": {"is_active": True, "updated_at": now}}
        )
        
    return {"message": "Client restored successfully"}

@client_mgmt_router.post("/clients/{user_id}/approve", response_model=ClientListResponse, summary="Approve Client Signup")
async def approve_client_signup(
    user_id: str, 
    approve_in: ClientApproveRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    user_query = {"$or": [{"_id": ObjectId(user_id)}, {"id": user_id}]} if ObjectId.is_valid(user_id) else {"id": user_id}
    user = await db["users"].find_one(user_query)
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("role") != "client" or user.get("approval_status") == "approved":
        raise HTTPException(status_code=400, detail="User is not pending approval")

    now = datetime.now(timezone.utc)
    
    # Update users collection
    await db["users"].update_one(
        user_query,
        {"$set": {
            "is_approved": True,
            "approval_status": "approved",
            "is_active": True,
            "updated_at": now
        }}
    )

    # Add to client_list or Update existing
    existing_client = await db["client_list"].find_one({"email": user.get("email")})
    if existing_client:
        await db["client_list"].update_one(
            {"_id": existing_client["_id"]},
            {"$set": {
                "is_signup": True,
                "status": "active",
                "is_active": True,
                "license_expiration_date": approve_in.license_expiration_date,
                "updated_at": now,
                "admin": {
                    "id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
                    "name": current_user.full_name,
                    "profile_picture": current_user.profile_photo
                }
            }}
        )
        doc = await db["client_list"].find_one({"_id": existing_client["_id"]})
    else:
        client_id = f"cli_{uuid.uuid4().hex[:10]}"
        doc = {
            "_id": client_id,
            "id": client_id,
            "admin": {
                "id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
                "name": current_user.full_name,
                "profile_picture": current_user.profile_photo
            },
            "company_name": user.get("company_name") or "",
            "industry": "Corporate",
            "primary_contact_name": user.get("full_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
            "status": "active",
            "is_signup": True,
            "license_expiration_date": approve_in.license_expiration_date,
            "is_active": True,
            "total_locations_count": 0,
            "active_contracts_count": 1,
            "created_at": now,
            "updated_at": now
        }
        await db["client_list"].insert_one(doc)

    # Log to approval history
    await db["client_approval_history"].insert_one({
        "client_id": str(user.get("_id", user_id)),
        "client_name": user.get("company_name", "") or user.get("full_name", ""),
        "client_email": user.get("email", ""),
        "action": "approved",
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "created_at": now
    })

    from app.services.notification_service import NotificationService
    from app.api.chat import ws_manager

    notif_service = NotificationService()
    player_id = user.get("onesignal_player_id")
    player_ids = [player_id] if player_id else None
    
    await notif_service.create_notification(
        title="Account Approved",
        message="Your client account has been approved by the admin. You can now login.",
        notification_type="account_approved",
        route_type="overview",
        recipient_type="client",
        user_id=str(user.get("_id", user_id)),
        player_ids=player_ids,
        data={
            "route": "/client/overview",
            "deeplink": "cleaningone://client/overview"
        }
    )
    
    await ws_manager.broadcast_to_users({
        "type": "account_approved",
        "message": "Your client account has been approved."
    }, [str(user.get("_id", user_id))])

    return _format_client_response(doc)

@client_mgmt_router.post("/clients/{user_id}/reject", status_code=status.HTTP_200_OK, summary="Reject Client Signup")
async def reject_client_signup(
    user_id: str, 
    reject_in: ClientRejectRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    user_query = {"$or": [{"_id": ObjectId(user_id)}, {"id": user_id}]} if ObjectId.is_valid(user_id) else {"id": user_id}
    user = await db["users"].find_one(user_query)
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("role") != "client" or user.get("approval_status") != "pending":
        raise HTTPException(status_code=400, detail="User is not pending approval")

    now = datetime.now(timezone.utc)
    
    # Update users collection
    await db["users"].update_one(
        user_query,
        {"$set": {
            "is_approved": False,
            "approval_status": "rejected",
            "is_active": False,
            "updated_at": now
        }}
    )

    # Log to approval history
    await db["client_approval_history"].insert_one({
        "client_id": str(user.get("_id", user_id)),
        "client_name": user.get("company_name", "") or user.get("full_name", ""),
        "client_email": user.get("email", ""),
        "action": "rejected",
        "manager_id": str(current_user.id) if getattr(current_user, "id", None) else "unknown",
        "manager_name": current_user.full_name,
        "reject_reason": reject_in.reject_reason,
        "created_at": now
    })

    from app.services.notification_service import NotificationService
    from app.api.chat import ws_manager

    notif_service = NotificationService()
    player_id = user.get("onesignal_player_id")
    player_ids = [player_id] if player_id else None
    
    reject_msg = f"Your client account has been rejected. Reason: {reject_in.reject_reason}" if reject_in.reject_reason else "Your client account has been rejected."
    
    await notif_service.create_notification(
        title="Account Rejected",
        message=reject_msg,
        notification_type="account_rejected",
        route_type="overview",
        recipient_type="client",
        user_id=str(user.get("_id", user_id)),
        player_ids=player_ids,
        data={
            "route": "/client/overview",
            "deeplink": "cleaningone://client/overview"
        }
    )

    await ws_manager.broadcast_to_users({
        "type": "account_rejected",
        "message": reject_msg
    }, [str(user.get("_id", user_id))])

    return {"message": "Client signup rejected successfully"}

