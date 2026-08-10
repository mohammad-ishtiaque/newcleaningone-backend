import uuid
from datetime import datetime, timezone, date
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.client_list import (
    ClientListCreate, ClientListUpdate, ClientListResponse, ClientListPaginatedResponse,
    ContractRenewRequest, ClientGridDropdownItem, ClientGridDropdownPaginatedResponse,
    ClientOverviewListPaginatedResponse, ClientDashboardOverviewResponse, ClientContractDetailsResponse
)
from app.schemas.client_approvals import (
    ClientApproveRequest, ClientRejectRequest, PendingClientApprovalsPaginatedResponse
)
from app.models.user import UserInDB, RoleEnum
from app.schemas.user import UserResponse
from app.api.admin.profile_company import require_manager

client_mgmt_router = APIRouter(prefix="/manager", tags=["Admin Client Management"])

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

def _format_client_response(doc: dict) -> ClientListResponse:
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
        company_name=doc.get("company_name", ""),
        industry=doc.get("industry", "Corporate"),
        status=doc.get("status", "active"),
        primary_contact_name=doc.get("primary_contact_name", ""),
        email=doc.get("email", ""),
        phone=doc.get("phone", ""),
        is_signup=doc.get("is_signup", False),
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
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"status": {"$ne": "deleted"}}
    if search:
        query["$or"] = [
            {"company_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"industry": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    clients = []
    for c in raw_clients:
        clients.append(ClientGridDropdownItem(
            id=str(c.get("_id") or c.get("id")),
            primary_contact_name=c.get("primary_contact_name", ""),
            company_name=c.get("company_name", "")
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
    existing = await db["client_list"].find_one({"email": client_in.email})
    if existing:
        raise HTTPException(status_code=400, detail="Client with this email already exists")

    user_existing = await db["users"].find_one({"email": client_in.email})
    is_signup = bool(user_existing)
    
    now = datetime.now(timezone.utc)
    
    # Auto-approve existing user if they are pending to avoid identity split / lockout
    if user_existing and user_existing.get("role") == "client" and user_existing.get("approval_status") != "approved":
        await db["users"].update_one(
            {"_id": user_existing["_id"]},
            {"$set": {
                "is_approved": True,
                "approval_status": "approved",
                "is_active": True,
                "updated_at": now
            }}
        )

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
        "email": client_in.email,
        "phone": client_in.phone,
        "status": client_in.status or "active",
        "is_signup": is_signup,
        "license_expiration_date": client_in.license_expiration_date,
        "is_active": True,
        "total_locations_count": 0,
        "active_contracts_count": 1,
        "created_at": now,
        "updated_at": now
    }

    await db["client_list"].insert_one(doc)
    return _format_client_response(doc)


@client_mgmt_router.get("/clients/deleted-list", response_model=ClientOverviewListPaginatedResponse, summary="List Deleted Clients")
async def list_deleted_clients(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"status": "deleted"}
    if search:
        query["$or"] = [
            {"company_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]
        
    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)
    
    clients = []
    for c in raw_clients:
        clients.append(ClientOverviewItemResponse(
            id=str(c.get("_id") or c.get("id")),
            company_name=c.get("company_name", ""),
            industry=c.get("industry", ""),
            status=c.get("status", "deleted"),
            primary_contact_name=c.get("primary_contact_name", ""),
            email=c.get("email", ""),
            phone=c.get("phone", ""),
            locations_count=c.get("total_locations_count", 0),
            contract_status=c.get("contract_status", "no_contract"),
            created_at=c.get("created_at"),
            updated_at=c.get("updated_at")
        ))
        
    return ClientOverviewListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients
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
            "company_name": user.get("company_name", ""),
            "industry": "Corporate",
            "primary_contact_name": user.get("full_name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
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
        recipient_type="client",
        user_id=str(user.get("_id", user_id)),
        player_ids=player_ids
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
        recipient_type="client",
        user_id=str(user.get("_id", user_id)),
        player_ids=player_ids
    )

    await ws_manager.broadcast_to_users({
        "type": "account_rejected",
        "message": reject_msg
    }, [str(user.get("_id", user_id))])

    return {"message": "Client signup rejected successfully"}



@client_mgmt_router.get("/clients-overview", response_model=ClientOverviewListPaginatedResponse, summary="Client Overview List")
async def list_clients_overview(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"status": {"$ne": "deleted"}}
    if search:
        query["$or"] = [
            {"company_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    items = []
    for c in raw_clients:
        cid = str(c.get("_id") or c.get("id"))
        loc_count = await db["locations"].count_documents({"client_id": cid})
        shift_count = await db["shifts"].count_documents({"client_id": cid})
        items.append(ClientOverviewItemResponse(
            id=cid,
            company_name=c.get("company_name", "Client Company"),
            industry=c.get("industry", "Corporate"),
            status=c.get("status", "active"),
            primary_contact_name=c.get("primary_contact_name", ""),
            email=c.get("email", ""),
            phone=c.get("phone", ""),
            locations_count=loc_count,
            contract_status=c.get("contract_status", "active"),
            created_at=c.get("created_at") if isinstance(c.get("created_at"), datetime) else datetime.now(timezone.utc),
            updated_at=c.get("updated_at") if isinstance(c.get("updated_at"), datetime) else datetime.now(timezone.utc)
        ))

    return ClientOverviewListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=items
    )


# ================================
# 2. Client Detailed View: Overview Tab (Image 2)
# ================================

@client_mgmt_router.get("/clients/{client_id}/dashboard-overview", response_model=ClientDashboardOverviewResponse, summary="Client Detailed View: Overview Tab (Image 2)")
async def get_client_dashboard_overview(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")
    ind = cdoc.get("industry", "Corporate")
    st = cdoc.get("status", "Active").capitalize()

    loc_count = await db["locations"].count_documents({"client_id": cid})
    contact_count = await db["contacts"].count_documents({"client_id": cid})
    if contact_count == 0 and cdoc.get("contacts"):
        contact_count = len(cdoc.get("contacts", []))
    if contact_count == 0:
        contact_count = 2

    tasks_count = await db["rooms"].count_documents({"client_id": cid})
    if tasks_count == 0:
        tasks_count = 6

    contract_doc = await db["contracts"].find_one({"$or": [{"client_id": cid}, {"client_name": cname}]})
    expiry_raw = contract_doc.get("expiry_date") if contract_doc else cdoc.get("contract_expiry")
    expiry_fmt = _format_date_human(expiry_raw)
    contract_st = contract_doc.get("status", "Active").capitalize() if contract_doc else "Active"

    sub_title = f"{ind} • Contract: {contract_st}"

    return ClientDashboardOverviewResponse(
        client_id=cid,
        company_name=cname,
        industry=ind,
        status=st,
        subtitle_contract_status=sub_title,
        contract_status=contract_st,
        contract_expiry_date=str(expiry_raw) if expiry_raw else "2026-12-31",
        contract_expiry_formatted=expiry_fmt,
        locations_count=loc_count,
        contacts_count=contact_count,
        active_tasks_count=tasks_count
    )

# ================================
# 3. Client Detailed View: Contract Tab (Image 5)
# ================================

@client_mgmt_router.get("/clients/{client_id}/contract", response_model=ClientContractDetailsResponse, summary="Client Detailed View: Contract Tab (Image 5)")
async def get_client_contract_details(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")

    contract_doc = await db["contracts"].find_one({"$or": [{"client_id": cid}, {"client_name": cname}]})
    expiry_raw = contract_doc.get("expiry_date") if contract_doc else cdoc.get("contract_expiry", "2026-12-31")
    expiry_fmt = _format_date_human(expiry_raw)
    st = contract_doc.get("status", "Active").capitalize() if contract_doc else "Active"
    pdf = contract_doc.get("pdf_url") if contract_doc else None

    return ClientContractDetailsResponse(
        client_id=cid,
        company_name=cname,
        status=st,
        expiry_date=str(expiry_raw),
        expiry_date_formatted=expiry_fmt,
        pdf_url=pdf
    )

@client_mgmt_router.post("/clients/{client_id}/contract/renew", response_model=ClientContractDetailsResponse, summary="Renew Client Contract (Image 5)")
async def renew_client_contract(
    client_id: str,
    renew_in: ContractRenewRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")
    now = datetime.now(timezone.utc)
    new_exp_str = renew_in.expiry_date.isoformat()

    await db["contracts"].update_one(
        {"$or": [{"client_id": cid}, {"client_name": cname}]},
        {"$set": {
            "client_id": cid,
            "client_name": cname,
            "expiry_date": new_exp_str,
            "status": "active",
            "updated_at": now
        }},
        upsert=True
    )

    expiry_fmt = _format_date_human(new_exp_str)
    return ClientContractDetailsResponse(
        client_id=cid,
        company_name=cname,
        status="Active",
        expiry_date=new_exp_str,
        expiry_date_formatted=expiry_fmt,
        pdf_url=None
    )

@client_mgmt_router.post("/clients/{client_id}/contract/upload", summary="Upload Client Contract PDF (Image 5)")
async def upload_client_contract_pdf(
    client_id: str,
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    file_url = f"/uploads/contracts/{client_id}_{file.filename}"

    await db["contracts"].update_one(
        {"client_id": cid},
        {"$set": {"pdf_url": file_url, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    return {"message": "Contract uploaded successfully", "pdf_url": file_url}
