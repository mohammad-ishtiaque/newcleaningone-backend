import uuid
from datetime import datetime, timezone, date
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import List, Optional, Dict, Any
from bson import ObjectId
from app.core.database import get_database
from app.schemas.user import AdminCreate, AdminUpdate, AdminProfileResponse, UserResponse
from app.schemas.help import (
    CompanyProfileResponse, CompanyProfileUpdate, LegalDocumentResponse, LegalDocumentUpdate,
    SupportMessageResponse, SupportReplyRequest, AdminSupportListResponse,
    FAQCreate, FAQUpdate, FAQResponse
)
from app.schemas.client_list import (
    ClientListCreate, ClientListUpdate, ClientListResponse, ClientListPaginatedResponse,
    ContactCreate, ContactUpdate, ContactResponse, ContactPaginatedResponse,
    LocationCreate, LocationUpdate, LocationResponse, LocationPaginatedResponse,
    ContractCreate, ContractUpdate, ContractRenewRequest, ContractResponse, ContractPaginatedResponse,
    CleaningPlanCreate, CleaningPlanUpdate, CleaningPlanResponse, CleaningPlanPaginatedResponse,
    CleaningTaskCreate, CleaningTaskUpdate, CleaningTaskResponse,
    ReportResponse, ReportPaginatedResponse, ClientOverviewResponse,
    ClientOverviewItemResponse, ClientOverviewListPaginatedResponse, ClientOverviewDetailResponse,
    GlobalLocationResponse, GlobalLocationPaginatedResponse,
    LocationDropdownItemResponse, LocationDropdownPaginatedResponse,
    RoomDropdownItemResponse, RoomDropdownPaginatedResponse,
    RequiredPhotoCreate, RequiredPhotoResponse,
    RoomCreate, RoomUpdate, RoomResponse, RoomPaginatedResponse,
    CleaningPlanRoomInput, GlobalCleaningPlanCreate, GlobalCleaningPlanUpdate,
    CleaningPlanRoomDetail, CleaningPlanRoomSummary, GlobalCleaningPlanListItemResponse,
    GlobalCleaningPlanResponse, GlobalCleaningPlanPaginatedResponse
)
from app.models.client_list import ClientListDB
from app.services.notification_service import NotificationService
from app.services.faq_service import FAQService
from app.services.user_service import UserService
from app.services.s3_service import S3Service
from app.repositories.user_repo import UserRepository
from app.dependencies.rbac import RequireRole
from app.dependencies.auth import get_current_user
from app.models.user import RoleEnum, UserInDB
from app.security.password import get_password_hash

router = APIRouter(prefix="/admin", tags=["Admin"])
client_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Client Management"])
location_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Location Management"])
room_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Room Management"])
cleaning_plan_mgmt_router = APIRouter(prefix="/admin", tags=["Admin Cleaning Plan Management"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.admin, RoleEnum.super_admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user

# ================================
# My Profile (Admin/SuperAdmin)
# ================================

@router.get("/me", response_model=AdminProfileResponse)
async def get_my_admin_profile(current_user: UserInDB = Depends(require_admin)):
    return AdminProfileResponse(**current_user.model_dump())

@router.patch("/me", response_model=AdminProfileResponse)
async def update_my_admin_profile(
    admin_update: AdminUpdate,
    current_user: UserInDB = Depends(require_admin),
    user_repo: UserRepository = Depends(UserRepository)
):
    update_data = admin_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
        
    await user_repo.update(current_user)
    return AdminProfileResponse(**current_user.model_dump())

# ================================
# Company Profile (Admin/SuperAdmin)
# ================================

@router.get("/company-profile", response_model=CompanyProfileResponse)
async def get_company_profile(current_user: UserInDB = Depends(require_admin)):
    db = get_database()
    profile = await db["company_profile"].find_one({"type": "main"})
    if not profile:
        profile = {
            "company_name": "Cleaning One",
            "email": "admin@cleaningone.com",
            "phone": "+8801319414320",
            "address": "Mohakhali, Dhaka",
            "website": "www.cleaningone.com",
            "updated_at": datetime.now(timezone.utc)
        }
    return CompanyProfileResponse(**profile)

@router.patch("/company-profile", response_model=CompanyProfileResponse)
async def update_company_profile(
    update_data: CompanyProfileUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    fields = update_data.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)
    fields["type"] = "main"
    
    updated = await db["company_profile"].find_one_and_update(
        {"type": "main"},
        {"$set": fields},
        upsert=True,
        return_document=True
    )
    return CompanyProfileResponse(**updated)

# ================================
# Legal Documents (Privacy Policy & Terms)
# ================================

@router.get("/legal-documents/{doc_type}", response_model=LegalDocumentResponse)
async def get_legal_document(doc_type: str, current_user: UserInDB = Depends(require_admin)):
    if doc_type not in ["privacy_policy", "terms_and_conditions"]:
        raise HTTPException(status_code=400, detail="Invalid document type. Allowed: privacy_policy, terms_and_conditions")
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": doc_type})
    default_title = "Privacy Policy" if doc_type == "privacy_policy" else "Terms & Conditions"
    if doc:
        return LegalDocumentResponse(
            type=doc_type,
            title=doc.get("title", default_title),
            content=doc.get("content", ""),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc).isoformat())
        )
    return LegalDocumentResponse(
        type=doc_type,
        title=default_title,
        content=f"Our {default_title} is currently being drafted and will be updated soon.",
        updated_at=datetime.now(timezone.utc).isoformat()
    )

@router.patch("/legal-documents/{doc_type}", response_model=LegalDocumentResponse)
async def update_legal_document(
    doc_type: str,
    update_data: LegalDocumentUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    if doc_type not in ["privacy_policy", "terms_and_conditions"]:
        raise HTTPException(status_code=400, detail="Invalid document type. Allowed: privacy_policy, terms_and_conditions")
    db = get_database()
    default_title = "Privacy Policy" if doc_type == "privacy_policy" else "Terms & Conditions"
    title = update_data.title if update_data.title else default_title
    now_iso = datetime.now(timezone.utc).isoformat()
    
    doc_fields = {
        "type": doc_type,
        "title": title,
        "content": update_data.content,
        "updated_at": now_iso
    }
    
    await db["legal_documents"].find_one_and_update(
        {"type": doc_type},
        {"$set": doc_fields},
        upsert=True,
        return_document=True
    )
    
    # Notify workers and clients about the legal document update
    notification_service = NotificationService()
    await notification_service.notify_legal_document_update(doc_type=doc_type, title=title)
    
    return LegalDocumentResponse(
        type=doc_type,
        title=title,
        content=update_data.content,
        updated_at=now_iso
    )

# ================================
# Support Messages Management
# ================================

@router.get("/support-messages", response_model=AdminSupportListResponse)
async def list_support_messages(
    status_filter: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    is_read_by_admin: Optional[bool] = None,
    page: int = 1,
    limit: int = 20,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if status_filter:
        query["status"] = status_filter
    if is_resolved is not None:
        query["is_resolved"] = is_resolved
    if is_read_by_admin is not None:
        query["is_read_by_admin"] = is_read_by_admin

    total_count = await db["support_messages"].count_documents(query)
    unread_count = await db["support_messages"].count_documents({"is_read_by_admin": False})

    skip = (page - 1) * limit
    cursor = db["support_messages"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    messages = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        messages.append(SupportMessageResponse(**doc))

    return AdminSupportListResponse(
        total_count=total_count,
        unread_count=unread_count,
        messages=messages
    )

@router.get("/support-messages/{message_id}", response_model=SupportMessageResponse)
async def get_support_message_detail(
    message_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(message_id):
        raise HTTPException(status_code=400, detail="Invalid support message ID")

    doc = await db["support_messages"].find_one({"_id": ObjectId(message_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Support message not found")

    if not doc.get("is_read_by_admin", False):
        await db["support_messages"].update_one(
            {"_id": ObjectId(message_id)},
            {"$set": {"is_read_by_admin": True}}
        )
        doc["is_read_by_admin"] = True

    doc["_id"] = str(doc["_id"])
    return SupportMessageResponse(**doc)

@router.post("/support-messages/{message_id}/reply", response_model=SupportMessageResponse)
async def reply_support_message(
    message_id: str,
    reply_data: SupportReplyRequest,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(message_id):
        raise HTTPException(status_code=400, detail="Invalid support message ID")

    doc = await db["support_messages"].find_one({"_id": ObjectId(message_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Support message not found")

    now = datetime.now(timezone.utc)
    status_val = reply_data.status if reply_data.status else "resolved"
    is_resolved_val = reply_data.is_resolved if reply_data.is_resolved is not None else True

    update_fields = {
        "admin_reply": reply_data.admin_reply,
        "status": status_val,
        "is_resolved": is_resolved_val,
        "is_read_by_admin": True,
        "is_read_by_worker": False,
        "replied_at": now,
        "updated_at": now
    }

    updated_doc = await db["support_messages"].find_one_and_update(
        {"_id": ObjectId(message_id)},
        {"$set": update_fields},
        return_document=True
    )
    updated_doc["_id"] = str(updated_doc["_id"])

    # Notify the worker
    notification_service = NotificationService()
    return SupportMessageResponse(**updated_doc)

# ================================
# FAQ Management
# ================================

@router.get("/faqs", response_model=List[FAQResponse])
async def list_admin_faqs(current_user: UserInDB = Depends(require_admin)):
    faqs = await FAQService.get_all_faqs()
    return [FAQResponse(**f) for f in faqs]

@router.post("/faqs", response_model=FAQResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_faq(
    faq_in: FAQCreate,
    current_user: UserInDB = Depends(require_admin)
):
    created = await FAQService.create_faq(
        question=faq_in.question,
        answer=faq_in.answer,
        serial_no=faq_in.serial_no
    )
    return FAQResponse(**created)

@router.get("/faqs/{faq_id}", response_model=FAQResponse)
async def get_admin_faq_detail(
    faq_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    faq = await FAQService.get_faq_detail(faq_id)
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return FAQResponse(**faq)

@router.patch("/faqs/{faq_id}", response_model=FAQResponse)
async def update_admin_faq(
    faq_id: str,
    faq_update: FAQUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    updated = await FAQService.update_faq(
        faq_id=faq_id,
        question=faq_update.question,
        answer=faq_update.answer,
        serial_no=faq_update.serial_no
    )
    if not updated:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return FAQResponse(**updated)

@router.delete("/faqs/{faq_id}")
async def delete_admin_faq(
    faq_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    deleted = await FAQService.delete_faq(faq_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return {"message": "FAQ deleted successfully"}

# ================================
# CRUD for Admins (SuperAdmin only)
# ================================

@router.post(
    "/users", 
    response_model=UserResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def create_admin(
    user_in: AdminCreate,
    user_service: UserService = Depends(get_user_service)
):
    return await user_service.create_admin(user_in)

@router.get(
    "/users", 
    response_model=List[AdminProfileResponse],
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def list_admins(user_repo: UserRepository = Depends(UserRepository)):
    cursor = user_repo.collection.find({"role": RoleEnum.admin.value})
    admins = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        admins.append(AdminProfileResponse(**doc))
    return admins

@router.get(
    "/users/{admin_id}", 
    response_model=AdminProfileResponse,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def get_admin(admin_id: str, user_repo: UserRepository = Depends(UserRepository)):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    return AdminProfileResponse(**user.model_dump())

@router.patch(
    "/users/{admin_id}", 
    response_model=AdminProfileResponse,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def update_admin(
    admin_id: str, 
    admin_update: AdminUpdate, 
    user_repo: UserRepository = Depends(UserRepository)
):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
        
    update_data = admin_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
        
    await user_repo.update(user)
    return AdminProfileResponse(**user.model_dump())

@router.delete(
    "/users/{admin_id}", 
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def delete_admin(admin_id: str, user_repo: UserRepository = Depends(UserRepository)):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
        
    await user_repo.collection.delete_one({"_id": ObjectId(admin_id)})
    return {"message": "Admin user deleted successfully"}

# ================================
# Client List (Pre-approvals & Invitations)
# ================================

def _enrich_client_doc(doc: dict) -> dict:
    str_id = str(doc["_id"])
    doc["_id"] = str_id
    doc["id"] = str_id

    locations = doc.get("locations", [])
    doc["locations_count"] = len(locations)

    contracts = doc.get("contracts", [])
    latest_expiry = None
    if contracts:
        exp_dates = [c.get("expiry_date") for c in contracts if c.get("expiry_date")]
        if exp_dates:
            latest_expiry = max(exp_dates)

    doc["contract_status"] = _get_contract_status(latest_expiry) if latest_expiry else "no_contract"
    return doc

@client_mgmt_router.post("/client-list", response_model=ClientListResponse, status_code=status.HTTP_201_CREATED)
async def create_client_list_entry(
    client_in: ClientListCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    existing = await db["client_list"].find_one({"email": client_in.email})
    if existing:
        raise HTTPException(status_code=400, detail="Client with this email is already in the client list")

    now = datetime.now(timezone.utc)
    client_db = ClientListDB(
        admin_name=current_user.full_name,
        company_name=client_in.company_name,
        industry=client_in.industry,
        status=client_in.status or "pending",
        primary_contact_name=client_in.primary_contact_name,
        email=client_in.email,
        phone=client_in.phone,
        is_signup=False,
        created_at=now,
        updated_at=now
    )
    doc = client_db.model_dump(by_alias=True, exclude={"id"})
    res = await db["client_list"].insert_one(doc)
    doc["_id"] = res.inserted_id
    return ClientListResponse(**_enrich_client_doc(doc))

@client_mgmt_router.get("/client-list", response_model=ClientListPaginatedResponse, include_in_schema=False)
async def list_client_list_entries(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    is_signup: Optional[bool] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if is_signup is not None:
        query["is_signup"] = is_signup
    if search:
        query["$or"] = [
            {"company_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"primary_contact_name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"industry": {"$regex": search, "$options": "i"}}
        ]

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    clients = []
    async for doc in cursor:
        clients.append(ClientListResponse(**_enrich_client_doc(doc)))

    return ClientListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients
    )

@client_mgmt_router.get(
    "/client-overview",
    response_model=ClientOverviewListPaginatedResponse,
    summary="Get Client Overview List",
    description="Returns a paginated list of client high-level summary items including locations_count, contract_status, contact details, and dates."
)
async def get_client_overview(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    status: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if search:
        query["$or"] = [
            {"company_name": {"$regex": search, "$options": "i"}},
            {"industry": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"primary_contact_name": {"$regex": search, "$options": "i"}}
        ]
    if status:
        query["status"] = status

    total_count = await db["client_list"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["client_list"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_clients = await cursor.to_list(length=limit)

    clients_res = []
    for doc in raw_clients:
        str_id = str(doc["_id"])
        locations = doc.get("locations", [])
        locations_count = len(locations)

        contracts = doc.get("contracts", [])
        latest_expiry = None
        if contracts:
            exp_dates = [c.get("expiry_date") for c in contracts if c.get("expiry_date")]
            if exp_dates:
                latest_expiry = max(exp_dates)

        contract_status = _get_contract_status(latest_expiry) if latest_expiry else "no_contract"

        item = ClientOverviewItemResponse(
            id=str_id,
            company_name=doc.get("company_name", ""),
            industry=doc.get("industry", ""),
            status=doc.get("status", "pending"),
            primary_contact_name=doc.get("primary_contact_name", ""),
            email=doc.get("email", ""),
            phone=doc.get("phone", ""),
            locations_count=locations_count,
            contract_status=contract_status,
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at")
        )
        clients_res.append(item)

    return ClientOverviewListPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        clients=clients_res
    )

@client_mgmt_router.get(
    "/client-overview/{client_id}",
    response_model=ClientOverviewDetailResponse,
    summary="Get Specific Client Overview Detail",
    description="Returns high-level summary overview details for a specific client including locations_count, contract_status, contract_expiry_date, contacts_count, and timestamps."
)
async def get_specific_client_overview(
    client_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = doc.get("locations", [])
    locations_count = len(locations)

    contacts = doc.get("contacts", [])
    contacts_count = len(contacts)

    contracts = doc.get("contracts", [])
    latest_expiry = None
    if contracts:
        exp_dates = [c.get("expiry_date") for c in contracts if c.get("expiry_date")]
        if exp_dates:
            latest_expiry = max(exp_dates)

    contract_status = _get_contract_status(latest_expiry) if latest_expiry else "no_contract"

    return ClientOverviewDetailResponse(
        id=str(doc["_id"]),
        company_name=doc.get("company_name", ""),
        industry=doc.get("industry", ""),
        status=doc.get("status", "pending"),
        primary_contact_name=doc.get("primary_contact_name", ""),
        email=doc.get("email", ""),
        phone=doc.get("phone", ""),
        locations_count=locations_count,
        contract_status=contract_status,
        contract_expiry_date=latest_expiry,
        contacts_count=contacts_count,
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at")
    )

@client_mgmt_router.get("/client-list/{client_id}", response_model=ClientListResponse, include_in_schema=False)
async def get_client_list_entry(
    client_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Client invitation record not found")

    return ClientListResponse(**_enrich_client_doc(doc))

@client_mgmt_router.patch("/client-list/{client_id}", response_model=ClientListResponse)
async def update_client_list_entry(
    client_id: str,
    update_data: ClientListUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Client invitation record not found")

    old_email = doc.get("email")
    fields = update_data.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)

    if "email" in fields and fields["email"] != old_email:
        existing = await db["client_list"].find_one({"email": fields["email"]})
        if existing:
            raise HTTPException(status_code=400, detail="New email is already in client list")

    updated_doc = await db["client_list"].find_one_and_update(
        {"_id": ObjectId(client_id)},
        {"$set": fields},
        return_document=True
    )

    # If client has signed up, sync updates to users collection
    if doc.get("is_signup") and old_email:
        user_updates = {}
        if "email" in fields:
            user_updates["email"] = fields["email"]
        if "phone" in fields:
            user_updates["phone"] = fields["phone"]
        if "company_name" in fields:
            user_updates["company_name"] = fields["company_name"]

        if user_updates:
            user_updates["updated_at"] = datetime.now(timezone.utc)
            await db["users"].update_one(
                {"email": old_email, "role": RoleEnum.client.value},
                {"$set": user_updates}
            )

    return ClientListResponse(**_enrich_client_doc(updated_doc))

@client_mgmt_router.delete("/client-list/{client_id}")
async def delete_client_list_entry(
    client_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Client invitation record not found")

    email = doc.get("email")

    # 1. Delete from client_list collection
    await db["client_list"].delete_one({"_id": ObjectId(client_id)})

    # 2. Cascade delete signed-up client account from users collection if present
    if email:
        await db["users"].delete_many({"email": email, "role": RoleEnum.client.value})

    return {"message": "Client list entry deleted successfully"}

# ================================
# Helper Functions
# ================================

def _get_contract_status(expiry_date_val) -> str:
    if not expiry_date_val:
        return "expired"
    if isinstance(expiry_date_val, str):
        exp_d = datetime.strptime(expiry_date_val[:10], "%Y-%m-%d").date()
    elif isinstance(expiry_date_val, datetime):
        exp_d = expiry_date_val.date()
    else:
        exp_d = expiry_date_val
    today = date.today()
    if exp_d < today:
        return "expired"
    elif (exp_d - today).days <= 30:
        return "expiring"
    else:
        return "active"

# ================================
# Client Contacts APIs
# ================================

@client_mgmt_router.post("/client-list/{client_id}/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_client_contact(
    client_id: str,
    contact_in: ContactCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    now_str = datetime.now(timezone.utc).isoformat()
    contact_obj = {
        "id": str(uuid.uuid4()),
        "name": contact_in.name,
        "role": contact_in.role,
        "email": contact_in.email,
        "phone": contact_in.phone,
        "created_at": now_str,
        "updated_at": now_str
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"contacts": contact_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return ContactResponse(**contact_obj)

@client_mgmt_router.get("/client-list/{client_id}/contacts", response_model=ContactPaginatedResponse)
async def list_client_contacts(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contacts = client_doc.get("contacts", [])
    total_count = len(contacts)
    start = (page - 1) * limit
    end = start + limit
    paginated = contacts[start:end]

    return ContactPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        contacts=[ContactResponse(**c) for c in paginated]
    )

@client_mgmt_router.get("/client-list/{client_id}/contacts/{contact_id}", response_model=ContactResponse)
async def get_client_contact(
    client_id: str,
    contact_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contacts = client_doc.get("contacts", [])
    for c in contacts:
        if c.get("id") == contact_id:
            return ContactResponse(**c)

    raise HTTPException(status_code=404, detail="Contact not found")

@client_mgmt_router.patch("/client-list/{client_id}/contacts/{contact_id}", response_model=ContactResponse)
async def update_client_contact(
    client_id: str,
    contact_id: str,
    contact_in: ContactUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contacts = client_doc.get("contacts", [])
    target_idx = None
    target_contact = None
    for idx, c in enumerate(contacts):
        if c.get("id") == contact_id:
            target_idx = idx
            target_contact = c
            break

    if target_contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    update_fields = contact_in.model_dump(exclude_unset=True)
    for k, v in update_fields.items():
        if v is not None:
            target_contact[k] = v
    target_contact["updated_at"] = datetime.now(timezone.utc).isoformat()
    contacts[target_idx] = target_contact

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "contacts": contacts,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    return ContactResponse(**target_contact)

@client_mgmt_router.delete("/client-list/{client_id}/contacts/{contact_id}")
async def delete_client_contact(
    client_id: str,
    contact_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$pull": {"contacts": {"id": contact_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Contact deleted successfully"}

# ================================
# Client Locations APIs (AWS S3 Upload)
# ================================

@client_mgmt_router.post(
    "/client-list/{client_id}/locations",
    response_model=LocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Client Location",
    description="""
Create a new location entry for a specific client with optional AWS S3 image upload.

### Required Form Fields:
* **`name`** (`string`): Name of the location (e.g., `"HQ Tower - 3rd Floor"`).
* **`type`** (`string`): Location type enum. Allowed values: `"room"`, `"office"`, `"floor"`.
* **`address`** (`string`): Full street address (e.g., `"123 Business Way, Dhaka"`).

### Optional Form Fields:
* **`number_of_rooms`** (`integer`, default: `1`): Number of rooms at this location.
* **`description`** (`string`, default: `""`): Additional details or notes for this location.
* **`image_file`** (`file`, optional): Single location picture (JPEG/PNG/WEBP) automatically uploaded to AWS S3 bucket.

### Successful Response (HTTP 201 Created):
Returns a `LocationResponse` object containing the unique location `id`, location properties, AWS S3 public `image_url` (if uploaded), and ISO 8601 `created_at`/`updated_at` timestamps.
"""
)
async def create_client_location(
    client_id: str,
    name: str = Form(..., description="Location name (Required, e.g. 'Main HQ Office')"),
    type: str = Form(..., description="Location type enum: 'room' | 'office' | 'floor' (Required)"),
    address: str = Form(..., description="Full street address (Required, e.g. '123 Business Way, Dhaka')"),
    floor: int = Form(1, description="Floor number (Optional, default 1)"),
    number_of_rooms: int = Form(1, description="Number of rooms (Optional, default: 1)"),
    description: str = Form("", description="Additional location details or notes (Optional)"),
    image_file: Optional[UploadFile] = File(None, description="Optional image file (JPEG/PNG/WEBP) uploaded to AWS S3"),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    image_url = None
    if image_file:
        s3 = S3Service()
        contents = await image_file.read()
        image_url = await s3.upload_file(
            file_bytes=contents,
            file_name=image_file.filename or "location_image.jpg",
            content_type=image_file.content_type or "image/jpeg"
        )

    now_str = datetime.now(timezone.utc).isoformat()
    location_obj = {
        "id": str(uuid.uuid4()),
        "name": name,
        "type": type,
        "address": address,
        "floor": floor,
        "number_of_rooms": number_of_rooms,
        "description": description,
        "image_url": image_url,
        "created_at": now_str,
        "updated_at": now_str
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"locations": location_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return LocationResponse(**location_obj)

@client_mgmt_router.get("/client-list/{client_id}/locations", response_model=LocationPaginatedResponse)
async def list_client_locations(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = client_doc.get("locations", [])
    total_count = len(locations)
    start = (page - 1) * limit
    end = start + limit
    paginated = locations[start:end]

    return LocationPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=[LocationResponse(**l) for l in paginated]
    )

@client_mgmt_router.get("/client-list/{client_id}/locations/{location_id}", response_model=LocationResponse)
async def get_client_location(
    client_id: str,
    location_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = client_doc.get("locations", [])
    for l in locations:
        if l.get("id") == location_id:
            return LocationResponse(**l)

    raise HTTPException(status_code=404, detail="Location not found")

@client_mgmt_router.patch("/client-list/{client_id}/locations/{location_id}", response_model=LocationResponse)
async def update_client_location(
    client_id: str,
    location_id: str,
    name: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    floor: Optional[int] = Form(None),
    number_of_rooms: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    image_file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = client_doc.get("locations", [])
    target_idx = None
    target_loc = None
    for idx, l in enumerate(locations):
        if l.get("id") == location_id:
            target_idx = idx
            target_loc = l
            break

    if target_loc is None:
        raise HTTPException(status_code=404, detail="Location not found")

    if name is not None: target_loc["name"] = name
    if type is not None: target_loc["type"] = type
    if address is not None: target_loc["address"] = address
    if floor is not None: target_loc["floor"] = floor
    if number_of_rooms is not None: target_loc["number_of_rooms"] = number_of_rooms
    if description is not None: target_loc["description"] = description

    if image_file:
        s3 = S3Service()
        contents = await image_file.read()
        new_url = await s3.upload_file(
            file_bytes=contents,
            file_name=image_file.filename or "location_image.jpg",
            content_type=image_file.content_type or "image/jpeg"
        )
        if new_url:
            target_loc["image_url"] = new_url

    target_loc["updated_at"] = datetime.now(timezone.utc).isoformat()
    locations[target_idx] = target_loc

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "locations": locations,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    return LocationResponse(**target_loc)

@client_mgmt_router.delete("/client-list/{client_id}/locations/{location_id}")
async def delete_client_location(
    client_id: str,
    location_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$pull": {"locations": {"id": location_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Location deleted successfully"}

# ================================
# Client Contracts APIs (AWS S3 Upload & Renewal)
# ================================

@client_mgmt_router.post("/client-list/{client_id}/contracts", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
async def create_client_contract(
    client_id: str,
    client_name: str = Form(...),
    expiry_date: str = Form(...), # YYYY-MM-DD
    pdf_file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    pdf_url = None
    if pdf_file:
        s3 = S3Service()
        contents = await pdf_file.read()
        pdf_url = await s3.upload_file(
            file_bytes=contents,
            file_name=pdf_file.filename or "contract.pdf",
            content_type=pdf_file.content_type or "application/pdf"
        )

    calc_status = _get_contract_status(expiry_date)
    now_str = datetime.now(timezone.utc).isoformat()
    contract_obj = {
        "id": str(uuid.uuid4()),
        "client_name": client_name,
        "status": calc_status,
        "expiry_date": expiry_date,
        "pdf_url": pdf_url,
        "created_at": now_str,
        "updated_at": now_str
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"contracts": contract_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return ContractResponse(**contract_obj)

@client_mgmt_router.get("/client-list/{client_id}/contracts", response_model=ContractPaginatedResponse)
async def list_client_contracts(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contracts = client_doc.get("contracts", [])
    res = []
    for c in contracts:
        c["status"] = _get_contract_status(c.get("expiry_date"))
        res.append(ContractResponse(**c))

    total_count = len(res)
    start = (page - 1) * limit
    end = start + limit
    paginated = res[start:end]

    return ContractPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        contracts=paginated
    )

@client_mgmt_router.get("/client-list/{client_id}/contracts/{contract_id}", response_model=ContractResponse)
async def get_client_contract(
    client_id: str,
    contract_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contracts = client_doc.get("contracts", [])
    for c in contracts:
        if c.get("id") == contract_id:
            c["status"] = _get_contract_status(c.get("expiry_date"))
            return ContractResponse(**c)

    raise HTTPException(status_code=404, detail="Contract not found")

@client_mgmt_router.patch("/client-list/{client_id}/contracts/{contract_id}", response_model=ContractResponse)
async def update_client_contract(
    client_id: str,
    contract_id: str,
    client_name: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    pdf_file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contracts = client_doc.get("contracts", [])
    target_idx = None
    target_c = None
    for idx, c in enumerate(contracts):
        if c.get("id") == contract_id:
            target_idx = idx
            target_c = c
            break

    if target_c is None:
        raise HTTPException(status_code=404, detail="Contract not found")

    if client_name is not None: target_c["client_name"] = client_name
    if expiry_date is not None:
        target_c["expiry_date"] = expiry_date
        target_c["status"] = _get_contract_status(expiry_date)

    if pdf_file:
        s3 = S3Service()
        contents = await pdf_file.read()
        new_pdf = await s3.upload_file(
            file_bytes=contents,
            file_name=pdf_file.filename or "contract.pdf",
            content_type=pdf_file.content_type or "application/pdf"
        )
        if new_pdf:
            target_c["pdf_url"] = new_pdf

    target_c["updated_at"] = datetime.now(timezone.utc).isoformat()
    contracts[target_idx] = target_c

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "contracts": contracts,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    return ContractResponse(**target_c)

@client_mgmt_router.post(
    "/client-list/{client_id}/contracts/{contract_id}/renew",
    response_model=ContractResponse,
    summary="Renew Client Contract",
    description="Extends/renews a client contract's expiry date and recalculates contract status."
)
async def renew_client_contract(
    client_id: str,
    contract_id: str,
    renew_in: ContractRenewRequest,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    contracts = client_doc.get("contracts", [])
    target_idx = None
    target_c = None
    for idx, c in enumerate(contracts):
        if c.get("id") == contract_id:
            target_idx = idx
            target_c = c
            break

    if target_c is None:
        raise HTTPException(status_code=404, detail="Contract not found")

    new_exp_str = renew_in.expiry_date.strftime("%Y-%m-%d")
    target_c["expiry_date"] = new_exp_str
    target_c["status"] = _get_contract_status(new_exp_str)
    target_c["updated_at"] = datetime.now(timezone.utc).isoformat()
    contracts[target_idx] = target_c

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "contracts": contracts,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    return ContractResponse(**target_c)

@client_mgmt_router.delete("/client-list/{client_id}/contracts/{contract_id}")
async def delete_client_contract(
    client_id: str,
    contract_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$pull": {"contracts": {"id": contract_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Contract deleted successfully"}

# ================================
# Cleaning Plans & Task APIs
# ================================

@client_mgmt_router.post("/client-list/{client_id}/cleaning-plans", response_model=CleaningPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_cleaning_plan(
    client_id: str,
    plan_in: CleaningPlanCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    tasks_list = []
    if plan_in.tasks:
        for t in plan_in.tasks:
            tasks_list.append({
                "id": str(uuid.uuid4()),
                "name": t.name
            })

    plan_obj = {
        "id": str(uuid.uuid4()),
        "plan_name": plan_in.plan_name,
        "task_count": len(tasks_list),
        "tasks": tasks_list
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"cleaning_plans": plan_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    plan_obj["message"] = "Cleaning plan created successfully"
    return CleaningPlanResponse(**plan_obj)

@client_mgmt_router.get("/client-list/{client_id}/cleaning-plans", response_model=CleaningPlanPaginatedResponse)
async def list_cleaning_plans(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    for p in plans:
        p["task_count"] = len(p.get("tasks", []))

    total_count = len(plans)
    start = (page - 1) * limit
    end = start + limit
    paginated = plans[start:end]

    return CleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        cleaning_plans=[CleaningPlanResponse(**p) for p in paginated]
    )

@client_mgmt_router.get("/client-list/{client_id}/cleaning-plans/{plan_id}", response_model=CleaningPlanResponse)
async def get_cleaning_plan(
    client_id: str,
    plan_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    for p in plans:
        if p.get("id") == plan_id:
            p["task_count"] = len(p.get("tasks", []))
            return CleaningPlanResponse(**p)

    raise HTTPException(status_code=404, detail="Cleaning plan not found")

@client_mgmt_router.patch("/client-list/{client_id}/cleaning-plans/{plan_id}", response_model=CleaningPlanResponse)
async def update_cleaning_plan(
    client_id: str,
    plan_id: str,
    plan_in: CleaningPlanUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    target_idx = None
    target_p = None
    for idx, p in enumerate(plans):
        if p.get("id") == plan_id:
            target_idx = idx
            target_p = p
            break

    if target_p is None:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    if plan_in.plan_name:
        target_p["plan_name"] = plan_in.plan_name

    target_p["task_count"] = len(target_p.get("tasks", []))
    plans[target_idx] = target_p

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "cleaning_plans": plans,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    return CleaningPlanResponse(**target_p)

@client_mgmt_router.delete("/client-list/{client_id}/cleaning-plans/{plan_id}")
async def delete_cleaning_plan(
    client_id: str,
    plan_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$pull": {"cleaning_plans": {"id": plan_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Cleaning plan deleted successfully"}

# Task level operations within a cleaning plan
@client_mgmt_router.post("/client-list/{client_id}/cleaning-plans/{plan_id}/tasks", response_model=CleaningPlanResponse)
async def add_task_to_cleaning_plan(
    client_id: str,
    plan_id: str,
    task_in: CleaningTaskCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    target_idx = None
    target_p = None
    for idx, p in enumerate(plans):
        if p.get("id") == plan_id:
            target_idx = idx
            target_p = p
            break

    if target_p is None:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    new_task = {
        "id": str(uuid.uuid4()),
        "name": task_in.name
    }
    target_p.setdefault("tasks", []).append(new_task)
    target_p["task_count"] = len(target_p["tasks"])
    plans[target_idx] = target_p

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "cleaning_plans": plans,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    target_p["message"] = "Task added to cleaning plan successfully"
    return CleaningPlanResponse(**target_p)

@client_mgmt_router.patch("/client-list/{client_id}/cleaning-plans/{plan_id}/tasks/{task_id}", response_model=CleaningPlanResponse)
async def update_task_in_cleaning_plan(
    client_id: str,
    plan_id: str,
    task_id: str,
    task_in: CleaningTaskUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    target_p = None
    target_idx = None
    for idx, p in enumerate(plans):
        if p.get("id") == plan_id:
            target_idx = idx
            target_p = p
            break

    if target_p is None:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    tasks = target_p.get("tasks", [])
    task_found = False
    for t in tasks:
        if t.get("id") == task_id:
            if task_in.name is not None:
                t["name"] = task_in.name
            task_found = True
            break

    if not task_found:
        raise HTTPException(status_code=404, detail="Task not found in plan")

    target_p["task_count"] = len(tasks)
    plans[target_idx] = target_p

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "cleaning_plans": plans,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    target_p["message"] = "Task updated in cleaning plan successfully"
    return CleaningPlanResponse(**target_p)

@client_mgmt_router.delete("/client-list/{client_id}/cleaning-plans/{plan_id}/tasks/{task_id}", response_model=CleaningPlanResponse)
async def delete_task_from_cleaning_plan(
    client_id: str,
    plan_id: str,
    task_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    plans = client_doc.get("cleaning_plans", [])
    target_p = None
    target_idx = None
    for idx, p in enumerate(plans):
        if p.get("id") == plan_id:
            target_idx = idx
            target_p = p
            break

    if target_p is None:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    tasks = target_p.get("tasks", [])
    updated_tasks = [t for t in tasks if t.get("id") != task_id]
    target_p["tasks"] = updated_tasks
    target_p["task_count"] = len(updated_tasks)
    plans[target_idx] = target_p

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$set": {
                "cleaning_plans": plans,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    target_p["message"] = "Task deleted from cleaning plan successfully"
    return CleaningPlanResponse(**target_p)

# ================================
# Client Reports APIs
# ================================

def _generate_report_pdf_bytes(title: str, client_doc: dict, summary: dict) -> bytes:
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0F172A'),
        fontName='Helvetica-Bold',
        spaceAfter=4
    )

    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#64748B'),
        spaceAfter=10
    )

    section_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontSize=12,
        leading=15,
        textColor=colors.HexColor('#1E293B'),
        fontName='Helvetica-Bold',
        spaceBefore=12,
        spaceAfter=6
    )

    cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155')
    )

    cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=cell_style,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#0F172A')
    )

    cell_header = ParagraphStyle(
        'TableHeader',
        parent=cell_style,
        fontName='Helvetica-Bold',
        textColor=colors.white
    )

    elements = []

    # Title & Subtitle Header
    elements.append(Paragraph(title, title_style))
    gen_by = summary.get("generated_by", "System Admin")
    gen_at = summary.get("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    elements.append(Paragraph(f"Generated on {gen_at} by <b>{gen_by}</b>", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#3B82F6'), spaceAfter=12))

    # --- 1. Client Overview ---
    elements.append(Paragraph("1. Client Overview & Account Information", section_style))
    overview_data = [
        [Paragraph("Company Name", cell_bold), Paragraph(str(client_doc.get("company_name", "N/A")), cell_style),
         Paragraph("Industry", cell_bold), Paragraph(str(client_doc.get("industry", "N/A")), cell_style)],
        [Paragraph("Primary Contact", cell_bold), Paragraph(str(client_doc.get("primary_contact_name", "N/A")), cell_style),
         Paragraph("Account Status", cell_bold), Paragraph(str(client_doc.get("status", "N/A")).upper(), cell_style)],
        [Paragraph("Email Address", cell_bold), Paragraph(str(client_doc.get("email", "N/A")), cell_style),
         Paragraph("Phone Number", cell_bold), Paragraph(str(client_doc.get("phone", "N/A")), cell_style)],
        [Paragraph("Signup Completed", cell_bold), Paragraph("Yes" if client_doc.get("is_signup") else "No", cell_style),
         Paragraph("Contract Status", cell_bold), Paragraph(str(summary.get("contract_status", "N/A")).upper(), cell_style)]
    ]
    t_overview = Table(overview_data, colWidths=[110, 160, 110, 160])
    t_overview.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F1F5F9')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#F1F5F9')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(t_overview)
    elements.append(Spacer(1, 10))

    # --- 2. Locations Directory ---
    locations = client_doc.get("locations", [])
    elements.append(Paragraph(f"2. Locations Directory ({len(locations)})", section_style))
    if locations:
        loc_table_data = [[
            Paragraph("Location Name", cell_header),
            Paragraph("Type", cell_header),
            Paragraph("Address", cell_header),
            Paragraph("Rooms", cell_header),
            Paragraph("Description", cell_header)
        ]]
        for loc in locations:
            loc_table_data.append([
                Paragraph(str(loc.get("name", "")), cell_bold),
                Paragraph(str(loc.get("type", "")).capitalize(), cell_style),
                Paragraph(str(loc.get("address", "")), cell_style),
                Paragraph(str(loc.get("number_of_rooms", 1)), cell_style),
                Paragraph(str(loc.get("description", "")) or "-", cell_style)
            ])
        t_loc = Table(loc_table_data, colWidths=[120, 55, 185, 45, 135])
        t_loc.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(t_loc)
    else:
        elements.append(Paragraph("<i>No location records registered for this client.</i>", subtitle_style))
    elements.append(Spacer(1, 10))

    # --- 3. Key Contacts ---
    contacts = client_doc.get("contacts", [])
    elements.append(Paragraph(f"3. Key Contacts ({len(contacts)})", section_style))
    if contacts:
        ct_table_data = [[
            Paragraph("Contact Name", cell_header),
            Paragraph("Role", cell_header),
            Paragraph("Email", cell_header),
            Paragraph("Phone", cell_header)
        ]]
        for ct in contacts:
            role_label = str(ct.get("role", "")).replace("_", " ").title()
            ct_table_data.append([
                Paragraph(str(ct.get("name", "")), cell_bold),
                Paragraph(role_label, cell_style),
                Paragraph(str(ct.get("email", "")), cell_style),
                Paragraph(str(ct.get("phone", "")), cell_style)
            ])
        t_ct = Table(ct_table_data, colWidths=[130, 130, 160, 120])
        t_ct.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(t_ct)
    else:
        elements.append(Paragraph("<i>No contacts listed for this client.</i>", subtitle_style))
    elements.append(Spacer(1, 10))

    # --- 4. Contracts & Expiry ---
    contracts = client_doc.get("contracts", [])
    elements.append(Paragraph(f"4. Contracts & Expiry ({len(contracts)})", section_style))
    if contracts:
        c_table_data = [[
            Paragraph("Client Name", cell_header),
            Paragraph("Contract Status", cell_header),
            Paragraph("Expiry Date", cell_header),
            Paragraph("PDF Contract Document", cell_header)
        ]]
        for c in contracts:
            st = str(c.get("status", _get_contract_status(c.get("expiry_date")))).upper()
            pdf_link = f"<a href='{c.get('pdf_url')}'><u>View PDF</u></a>" if c.get("pdf_url") else "No File"
            c_table_data.append([
                Paragraph(str(c.get("client_name", "")), cell_bold),
                Paragraph(st, cell_style),
                Paragraph(str(c.get("expiry_date", "")), cell_style),
                Paragraph(pdf_link, cell_style)
            ])
        t_c = Table(c_table_data, colWidths=[140, 110, 110, 180])
        t_c.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(t_c)
    else:
        elements.append(Paragraph("<i>No contract records uploaded for this client.</i>", subtitle_style))
    elements.append(Spacer(1, 10))

    # --- 5. Cleaning Plans & Task Breakdown ---
    plans = client_doc.get("cleaning_plans", [])
    elements.append(Paragraph(f"5. Cleaning Plans & Task Breakdown ({len(plans)})", section_style))
    if plans:
        plan_table_data = [[
            Paragraph("Plan Name", cell_header),
            Paragraph("Task Count", cell_header),
            Paragraph("Assigned Tasks List", cell_header)
        ]]
        for p in plans:
            tasks_str = ", ".join([str(t.get("name", "")) for t in p.get("tasks", []) if t.get("name")]) or "None"
            plan_table_data.append([
                Paragraph(str(p.get("plan_name", "")), cell_bold),
                Paragraph(str(len(p.get("tasks", []))), cell_style),
                Paragraph(tasks_str, cell_style)
            ])
        t_p = Table(plan_table_data, colWidths=[150, 75, 315])
        t_p.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(t_p)
    else:
        elements.append(Paragraph("<i>No cleaning plans assigned for this client.</i>", subtitle_style))

    doc.build(elements)
    return buf.getvalue()

@client_mgmt_router.post("/client-list/{client_id}/reports/generate", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def generate_client_report(
    client_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = client_doc.get("locations", [])
    contacts = client_doc.get("contacts", [])
    contracts = client_doc.get("contracts", [])
    plans = client_doc.get("cleaning_plans", [])

    total_tasks = sum(len(p.get("tasks", [])) for p in plans)
    latest_expiry = None
    if contracts:
        exp_dates = [c.get("expiry_date") for c in contracts if c.get("expiry_date")]
        if exp_dates:
            latest_expiry = max(exp_dates)

    overall_contract_status = _get_contract_status(latest_expiry) if latest_expiry else "no_contract"
    now_str = datetime.now(timezone.utc).isoformat()

    summary_data = {
        "company_name": client_doc.get("company_name"),
        "industry": client_doc.get("industry"),
        "client_status": client_doc.get("status"),
        "locations_count": len(locations),
        "contacts_count": len(contacts),
        "contracts_count": len(contracts),
        "latest_contract_expiry": latest_expiry,
        "contract_status": overall_contract_status,
        "cleaning_plans_count": len(plans),
        "total_tasks_count": total_tasks,
        "generated_by": current_user.full_name,
        "generated_at": now_str
    }

    report_id = str(uuid.uuid4())
    report_title = f"Broad Performance & Asset Report - {client_doc.get('company_name', 'Client')}"

    pdf_bytes = _generate_report_pdf_bytes(title=report_title, client_doc=client_doc, summary=summary_data)

    s3 = S3Service()
    pdf_url = await s3.upload_file(
        file_bytes=pdf_bytes,
        file_name=f"report_{report_id}.pdf",
        content_type="application/pdf"
    )

    report_obj = {
        "id": report_id,
        "title": report_title,
        "generated_at": now_str,
        "summary": summary_data,
        "pdf_url": pdf_url
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"reports": report_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return ReportResponse(**report_obj)

@client_mgmt_router.get("/client-list/{client_id}/reports", response_model=ReportPaginatedResponse)
async def list_client_reports(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    reports = client_doc.get("reports", [])
    reports_sorted = sorted(reports, key=lambda r: r.get("generated_at", ""), reverse=True)

    total_count = len(reports_sorted)
    start = (page - 1) * limit
    end = start + limit
    paginated = reports_sorted[start:end]

    return ReportPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        reports=[ReportResponse(**r) for r in paginated]
    )

@client_mgmt_router.delete("/client-list/{client_id}/reports/{report_id}")
async def delete_client_report(
    client_id: str,
    report_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$pull": {"reports": {"id": report_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Report deleted successfully"}

# ================================
# Client Overview Summary API
# ================================

@client_mgmt_router.get("/client-list/{client_id}/overview", response_model=ClientOverviewResponse)
async def get_client_overview(
    client_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    locations = client_doc.get("locations", [])
    contacts = client_doc.get("contacts", [])
    contracts = client_doc.get("contracts", [])
    plans = client_doc.get("cleaning_plans", [])

    total_tasks = sum(len(p.get("tasks", [])) for p in plans)

    latest_expiry = None
    if contracts:
        exp_dates = [c.get("expiry_date") for c in contracts if c.get("expiry_date")]
        if exp_dates:
            latest_expiry = max(exp_dates)

    overall_contract_status = _get_contract_status(latest_expiry) if latest_expiry else "no_contract"

    return ClientOverviewResponse(
        company_name=client_doc.get("company_name", ""),
        industry=client_doc.get("industry", ""),
        status=client_doc.get("status", "pending"),
        contract_expiry=latest_expiry,
        locations_count=len(locations),
        contacts_count=len(contacts),
        active_tasks_count=total_tasks,
        contract_status=overall_contract_status
    )

# ================================
# Admin Location Management APIs
# ================================

@location_mgmt_router.get(
    "/locations/clients-dropdown",
    summary="Get Clients Dropdown for Location Creation",
    description="Returns a simplified list of clients (id, company_name, email, phone, industry) for dropdown selection."
)
async def get_clients_dropdown_for_locations(
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    cursor = db["client_list"].find(
        {},
        {"_id": 1, "company_name": 1, "email": 1, "phone": 1, "industry": 1}
    ).sort("company_name", 1)
    raw_clients = await cursor.to_list(length=1000)
    dropdown_list = []
    for c in raw_clients:
        dropdown_list.append({
            "id": str(c["_id"]),
            "company_name": c.get("company_name", ""),
            "email": c.get("email", ""),
            "phone": c.get("phone", ""),
            "industry": c.get("industry", "")
        })
    return dropdown_list

@location_mgmt_router.get(
    "/locations",
    response_model=GlobalLocationPaginatedResponse,
    summary="List All Locations (Global)",
    description="Returns a paginated list of all locations across all clients with optional search, type, and client_id filtering."
)
async def list_all_global_locations(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    type: Optional[str] = None,
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    match_stage = {}
    if client_id and ObjectId.is_valid(client_id):
        match_stage["_id"] = ObjectId(client_id)

    pipeline = [
        {"$unwind": "$locations"}
    ]
    if match_stage:
        pipeline.append({"$match": match_stage})

    if type:
        pipeline.append({"$match": {"locations.type": type}})

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        pipeline.append({
            "$match": {
                "$or": [
                    {"locations.name": search_regex},
                    {"locations.address": search_regex},
                    {"locations.description": search_regex},
                    {"company_name": search_regex}
                ]
            }
        })

    pipeline.append({
        "$project": {
            "_id": 0,
            "id": "$locations.id",
            "client_id": {"$toString": "$_id"},
            "company_name": "$company_name",
            "name": "$locations.name",
            "type": "$locations.type",
            "address": "$locations.address",
            "floor": {"$ifNull": ["$locations.floor", 1]},
            "number_of_rooms": "$locations.number_of_rooms",
            "description": "$locations.description",
            "image_url": "$locations.image_url",
            "created_at": "$locations.created_at",
            "updated_at": "$locations.updated_at"
        }
    })

    count_pipeline = pipeline + [{"$count": "total"}]
    count_res = await db["client_list"].aggregate(count_pipeline).to_list(length=1)
    total_count = count_res[0]["total"] if count_res else 0

    skip = (page - 1) * limit
    data_pipeline = pipeline + [{"$skip": skip}, {"$limit": limit}]
    raw_locations = await db["client_list"].aggregate(data_pipeline).to_list(length=limit)

    locations_list = [GlobalLocationResponse(**item) for item in raw_locations]

    return GlobalLocationPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=locations_list
    )

@location_mgmt_router.post(
    "/locations",
    response_model=GlobalLocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Location for Client",
    description="Creates a new location for a specific client with optional image upload to AWS S3 (resized to 1080px width)."
)
async def create_global_location(
    client_id: str = Form(..., description="Target client ID (Required)"),
    name: str = Form(..., description="Location name (Required)"),
    type: str = Form(..., description="Location type enum: 'room' | 'office' | 'floor' (Required)"),
    address: str = Form(..., description="Street address (Required)"),
    floor: int = Form(1, description="Floor number (Optional, default: 1)"),
    number_of_rooms: int = Form(1, description="Number of rooms (Optional, default: 1)"),
    description: str = Form("", description="Location description/notes (Optional)"),
    image_file: Optional[UploadFile] = File(None, description="Optional image file uploaded to AWS S3"),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    image_url = None
    if image_file:
        s3 = S3Service()
        contents = await image_file.read()
        image_url = await s3.upload_file(
            file_bytes=contents,
            file_name=image_file.filename or "location.jpg",
            content_type=image_file.content_type or "image/jpeg"
        )

    now_str = datetime.now(timezone.utc).isoformat()
    location_id = str(uuid.uuid4())
    location_obj = {
        "id": location_id,
        "name": name,
        "type": type,
        "address": address,
        "floor": floor,
        "number_of_rooms": number_of_rooms,
        "description": description,
        "image_url": image_url,
        "created_at": now_str,
        "updated_at": now_str
    }

    await db["client_list"].update_one(
        {"_id": ObjectId(client_id)},
        {
            "$push": {"locations": location_obj},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )

    return GlobalLocationResponse(
        id=location_id,
        client_id=client_id,
        company_name=client_doc.get("company_name", ""),
        name=name,
        type=type,
        address=address,
        floor=floor,
        number_of_rooms=number_of_rooms,
        description=description,
        image_url=image_url,
        created_at=now_str,
        updated_at=now_str
    )

@location_mgmt_router.get(
    "/locations/{location_id}",
    response_model=GlobalLocationResponse,
    summary="Get Location Detail",
    description="Returns detailed location record by location ID across all clients."
)
async def get_global_location_detail(
    location_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    pipeline = [
        {"$unwind": "$locations"},
        {"$match": {"locations.id": location_id}},
        {"$project": {
            "_id": 0,
            "id": "$locations.id",
            "client_id": {"$toString": "$_id"},
            "company_name": "$company_name",
            "name": "$locations.name",
            "type": "$locations.type",
            "address": "$locations.address",
            "floor": {"$ifNull": ["$locations.floor", 1]},
            "number_of_rooms": "$locations.number_of_rooms",
            "description": "$locations.description",
            "image_url": "$locations.image_url",
            "created_at": "$locations.created_at",
            "updated_at": "$locations.updated_at"
        }}
    ]
    raw = await db["client_list"].aggregate(pipeline).to_list(length=1)
    if not raw:
        raise HTTPException(status_code=404, detail="Location not found")

    return GlobalLocationResponse(**raw[0])

@location_mgmt_router.patch(
    "/locations/{location_id}",
    response_model=GlobalLocationResponse,
    summary="Update Location",
    description="Updates location fields and/or uploads a replacement image to AWS S3."
)
async def update_global_location(
    location_id: str,
    name: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    floor: Optional[int] = Form(None),
    number_of_rooms: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    image_file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    client_doc = await db["client_list"].find_one({"locations.id": location_id})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    locations = client_doc.get("locations", [])
    target_idx = None
    target_loc = None
    for idx, loc in enumerate(locations):
        if loc.get("id") == location_id:
            target_idx = idx
            target_loc = loc
            break

    if target_loc is None:
        raise HTTPException(status_code=404, detail="Location record not found")

    if name is not None: target_loc["name"] = name
    if type is not None: target_loc["type"] = type
    if address is not None: target_loc["address"] = address
    if floor is not None: target_loc["floor"] = floor
    if number_of_rooms is not None: target_loc["number_of_rooms"] = number_of_rooms
    if description is not None: target_loc["description"] = description

    if image_file:
        s3 = S3Service()
        if target_loc.get("image_url"):
            await s3.delete_file(target_loc["image_url"])
        contents = await image_file.read()
        new_url = await s3.upload_file(
            file_bytes=contents,
            file_name=image_file.filename or "location.jpg",
            content_type=image_file.content_type or "image/jpeg"
        )
        if new_url:
            target_loc["image_url"] = new_url

    now_str = datetime.now(timezone.utc).isoformat()
    target_loc["updated_at"] = now_str
    locations[target_idx] = target_loc

    await db["client_list"].update_one(
        {"_id": client_doc["_id"]},
        {
            "$set": {
                "locations": locations,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )

    return GlobalLocationResponse(
        id=target_loc["id"],
        client_id=str(client_doc["_id"]),
        company_name=client_doc.get("company_name", ""),
        name=target_loc.get("name", ""),
        type=target_loc.get("type", ""),
        address=target_loc.get("address", ""),
        floor=target_loc.get("floor", 1),
        number_of_rooms=target_loc.get("number_of_rooms", 1),
        description=target_loc.get("description", ""),
        image_url=target_loc.get("image_url"),
        created_at=target_loc.get("created_at", now_str),
        updated_at=now_str
    )

@location_mgmt_router.delete(
    "/locations/{location_id}",
    summary="Delete Location",
    description="Deletes location record and removes associated image from AWS S3."
)
async def delete_global_location(
    location_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    client_doc = await db["client_list"].find_one({"locations.id": location_id})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Location not found")

    locations = client_doc.get("locations", [])
    target_loc = next((loc for loc in locations if loc.get("id") == location_id), None)
    if target_loc and target_loc.get("image_url"):
        s3 = S3Service()
        await s3.delete_file(target_loc["image_url"])

    await db["client_list"].update_one(
        {"_id": client_doc["_id"]},
        {
            "$pull": {"locations": {"id": location_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return {"message": "Location deleted successfully"}

# ====================================
# Helper function for Location lookup
# ====================================
async def _find_location_info_by_id(db, location_id: str):
    pipeline = [
        {"$unwind": "$locations"},
        {"$match": {"locations.id": location_id}},
        {"$project": {
            "_id": 0,
            "client_id": {"$toString": "$_id"},
            "company_name": "$company_name",
            "location_name": "$locations.name",
            "floor": {"$ifNull": ["$locations.floor", 1]}
        }}
    ]
    raw = await db["client_list"].aggregate(pipeline).to_list(length=1)
    if not raw:
        return None
    return raw[0]

# ================================
# Admin Room Management APIs
# ================================

@room_mgmt_router.get(
    "/rooms/locations-dropdown",
    response_model=LocationDropdownPaginatedResponse,
    summary="Get Locations Dropdown for Room Creation",
    description="Returns a paginated list of locations filtered by optional client_id for admin dropdown selection."
)
async def get_locations_dropdown_for_rooms(
    page: int = 1,
    limit: int = 10,
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    match_stage = {}
    if client_id and ObjectId.is_valid(client_id):
        match_stage["_id"] = ObjectId(client_id)

    pipeline = []
    if match_stage:
        pipeline.append({"$match": match_stage})

    pipeline.extend([
        {"$unwind": "$locations"},
        {"$project": {
            "_id": 0,
            "id": "$locations.id",
            "name": "$locations.name",
            "client_id": {"$toString": "$_id"},
            "company_name": "$company_name",
            "floor": {"$ifNull": ["$locations.floor", 1]}
        }}
    ])

    count_pipeline = pipeline + [{"$count": "total"}]
    count_res = await db["client_list"].aggregate(count_pipeline).to_list(length=1)
    total_count = count_res[0]["total"] if count_res else 0

    skip = (page - 1) * limit
    data_pipeline = pipeline + [{"$skip": skip}, {"$limit": limit}]
    raw_locations = await db["client_list"].aggregate(data_pipeline).to_list(length=limit)

    locations_list = [LocationDropdownItemResponse(**item) for item in raw_locations]

    return LocationDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=locations_list
    )

@room_mgmt_router.get(
    "/rooms",
    response_model=RoomPaginatedResponse,
    summary="List All Rooms",
    description="Returns a paginated list of all rooms with optional filtering by search, room_type, clean_type, location_id, and client_id."
)
async def list_all_rooms(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    room_type: Optional[str] = None,
    clean_type: Optional[str] = None,
    location_id: Optional[str] = None,
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if room_type: query["room_type"] = room_type
    if clean_type: query["clean_type"] = clean_type
    if location_id: query["location_id"] = location_id
    if client_id: query["client_id"] = client_id

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"room_name": search_regex},
            {"location_name": search_regex},
            {"company_name": search_regex}
        ]

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["rooms"].find(query).skip(skip).limit(limit).sort("created_at", -1)
    raw_rooms = await cursor.to_list(length=limit)

    rooms_res = []
    for r in raw_rooms:
        r["id"] = r.get("_id") or r.get("id")
        raw_photos = r.get("required_photos", [])
        if isinstance(raw_photos, int):
            raw_photos = [{"id": str(uuid.uuid4()), "name": f"Required Photo {i+1}"} for i in range(raw_photos)]
        r["required_photos"] = [RequiredPhotoResponse(**p) for p in raw_photos]
        r["photo_number"] = len(raw_photos)
        r["tasks"] = [CleaningTaskResponse(**t) for t in r.get("tasks", [])]
        rooms_res.append(RoomResponse(**r))

    return RoomPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=rooms_res
    )

def _process_required_photos_input(photos_in: Optional[List[RequiredPhotoCreate]]) -> List[dict]:
    photos_list = []
    if photos_in:
        for p in photos_in:
            p_dict = p.model_dump()
            if "id" not in p_dict or not p_dict["id"]:
                p_dict["id"] = str(uuid.uuid4())
            photos_list.append(p_dict)
    return photos_list

@room_mgmt_router.post(
    "/rooms",
    response_model=RoomResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Room",
    description="Creates a new room associated with a selected location."
)
async def create_room(
    room_in: RoomCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    loc_info = await _find_location_info_by_id(db, room_in.location_id)
    if not loc_info:
        raise HTTPException(status_code=404, detail="Selected location_id not found")

    tasks_list = []
    if room_in.tasks:
        for t in room_in.tasks:
            t_dict = t.model_dump()
            if "id" not in t_dict or not t_dict["id"]:
                t_dict["id"] = str(uuid.uuid4())
            tasks_list.append(t_dict)

    photos_list = _process_required_photos_input(room_in.required_photos)

    now = datetime.now(timezone.utc)
    room_id = str(uuid.uuid4())

    room_doc = {
        "_id": room_id,
        "id": room_id,
        "room_name": room_in.room_name,
        "room_type": room_in.room_type,
        "client_id": loc_info["client_id"],
        "company_name": loc_info["company_name"],
        "location_id": room_in.location_id,
        "location_name": loc_info["location_name"],
        "floor": room_in.floor,
        "duration": room_in.duration,
        "required_photos": photos_list,
        "photo_number": len(photos_list),
        "task_number": len(tasks_list),
        "clean_type": room_in.clean_type,
        "tasks": tasks_list,
        "created_at": now,
        "updated_at": now
    }

    await db["rooms"].insert_one(room_doc)
    room_doc["required_photos"] = [RequiredPhotoResponse(**p) for p in photos_list]
    room_doc["tasks"] = [CleaningTaskResponse(**t) for t in tasks_list]
    return RoomResponse(**room_doc)

@room_mgmt_router.get(
    "/rooms/{room_id}",
    response_model=RoomResponse,
    summary="Get Room Detail",
    description="Returns detailed room record by room ID."
)
async def get_room_detail(
    room_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    room_doc = await db["rooms"].find_one({"_id": room_id})
    if not room_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    room_doc["id"] = room_doc.get("_id") or room_doc.get("id")
    raw_photos = room_doc.get("required_photos", [])
    if isinstance(raw_photos, int):
        raw_photos = [{"id": str(uuid.uuid4()), "name": f"Required Photo {i+1}"} for i in range(raw_photos)]
    room_doc["required_photos"] = [RequiredPhotoResponse(**p) for p in raw_photos]
    room_doc["photo_number"] = len(raw_photos)
    room_doc["tasks"] = [CleaningTaskResponse(**t) for t in room_doc.get("tasks", [])]
    return RoomResponse(**room_doc)

@room_mgmt_router.patch(
    "/rooms/{room_id}",
    response_model=RoomResponse,
    summary="Update Room",
    description="Updates room properties and task list."
)
async def update_room(
    room_id: str,
    room_in: RoomUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    room_doc = await db["rooms"].find_one({"_id": room_id})
    if not room_doc:
        raise HTTPException(status_code=404, detail="Room not found")

    update_fields = {}
    if room_in.room_name is not None: update_fields["room_name"] = room_in.room_name
    if room_in.room_type is not None: update_fields["room_type"] = room_in.room_type
    if room_in.floor is not None: update_fields["floor"] = room_in.floor
    if room_in.duration is not None: update_fields["duration"] = room_in.duration
    if room_in.clean_type is not None: update_fields["clean_type"] = room_in.clean_type

    if room_in.required_photos is not None:
        photos_list = _process_required_photos_input(room_in.required_photos)
        update_fields["required_photos"] = photos_list
        update_fields["photo_number"] = len(photos_list)

    if room_in.location_id is not None and room_in.location_id != room_doc.get("location_id"):
        loc_info = await _find_location_info_by_id(db, room_in.location_id)
        if not loc_info:
            raise HTTPException(status_code=404, detail="Selected location_id not found")
        update_fields["location_id"] = room_in.location_id
        update_fields["location_name"] = loc_info["location_name"]
        update_fields["client_id"] = loc_info["client_id"]
        update_fields["company_name"] = loc_info["company_name"]

    if room_in.tasks is not None:
        tasks_list = []
        for t in room_in.tasks:
            t_dict = t.model_dump()
            if "id" not in t_dict or not t_dict["id"]:
                t_dict["id"] = str(uuid.uuid4())
            tasks_list.append(t_dict)
        update_fields["tasks"] = tasks_list
        update_fields["task_number"] = len(tasks_list)

    update_fields["updated_at"] = datetime.now(timezone.utc)
    await db["rooms"].update_one({"_id": room_id}, {"$set": update_fields})

    updated_doc = await db["rooms"].find_one({"_id": room_id})
    updated_doc["id"] = updated_doc.get("_id") or updated_doc.get("id")
    updated_doc["tasks"] = [CleaningTaskResponse(**t) for t in updated_doc.get("tasks", [])]
    return RoomResponse(**updated_doc)

@room_mgmt_router.delete(
    "/rooms/{room_id}",
    summary="Delete Room",
    description="Deletes room record from database."
)
async def delete_room(
    room_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    res = await db["rooms"].delete_one({"_id": room_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"message": "Room deleted successfully"}


# ========================================
# Admin Cleaning Plan Management APIs
# ========================================

@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/rooms-dropdown",
    response_model=RoomDropdownPaginatedResponse,
    summary="Get Rooms Dropdown for Cleaning Plan Creation",
    description="Returns a paginated list of room dropdown items (id and room_name) filtered by optional location_id or client_id."
)
async def get_rooms_dropdown_for_cleaning_plans(
    page: int = 1,
    limit: int = 10,
    location_id: Optional[str] = None,
    client_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if location_id: query["location_id"] = location_id
    if client_id: query["client_id"] = client_id

    total_count = await db["rooms"].count_documents(query)
    skip = (page - 1) * limit

    cursor = db["rooms"].find(query, {"_id": 1, "id": 1, "room_name": 1}).sort("room_name", 1).skip(skip).limit(limit)
    raw_rooms = await cursor.to_list(length=limit)

    rooms_list = []
    for r in raw_rooms:
        rooms_list.append(RoomDropdownItemResponse(
            id=r.get("_id") or r.get("id"),
            room_name=r.get("room_name", "")
        ))

    return RoomDropdownPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        rooms=rooms_list
    )

@cleaning_plan_mgmt_router.get(
    "/cleaning-plans",
    response_model=GlobalCleaningPlanPaginatedResponse,
    summary="List All Cleaning Plans",
    description="Returns a paginated list of all global cleaning plans with optional search, client_id, and location_id filtering."
)
async def list_all_global_cleaning_plans(
    page: int = 1,
    limit: int = 10,
    search: Optional[str] = None,
    client_id: Optional[str] = None,
    location_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    query = {}
    if client_id: query["client_id"] = client_id
    if location_id: query["location_id"] = location_id

    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"name": search_regex},
            {"client_name": search_regex},
            {"client_location_name": search_regex}
        ]

    total_count = await db["global_cleaning_plans"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["global_cleaning_plans"].find(query).skip(skip).limit(limit).sort("created_at", -1)
    raw_plans = await cursor.to_list(length=limit)

    plans_res = []
    for p in raw_plans:
        p["id"] = p.get("_id") or p.get("id")
        rooms_summary = []
        for rd in p.get("rooms", []):
            rooms_summary.append(CleaningPlanRoomSummary(
                room_id=rd.get("room_id", ""),
                custom_room_name=rd.get("custom_room_name", ""),
                room_type=rd.get("room_type", "standard")
            ))
        p["rooms"] = rooms_summary
        plans_res.append(GlobalCleaningPlanListItemResponse(**p))

    return GlobalCleaningPlanPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        cleaning_plans=plans_res
    )

def _process_room_tasks_for_plan(room_doc: dict, room_input: CleaningPlanRoomInput) -> List[dict]:
    base_tasks = room_doc.get("tasks", [])
    tasks_list = []
    seen_ids = set()

    for t in base_tasks:
        t_dict = dict(t)
        if "id" not in t_dict or not t_dict["id"]:
            t_dict["id"] = str(uuid.uuid4())
        tasks_list.append(t_dict)
        seen_ids.add(t_dict["id"])

    input_tasks = (room_input.additional_tasks or []) + (room_input.tasks or [])
    if input_tasks:
        for t in input_tasks:
            t_dict = t.model_dump()
            t_id = t_dict.get("id")
            existing = next((existing_t for existing_t in tasks_list if t_id and existing_t.get("id") == t_id), None)
            if existing:
                if "name" in t_dict and t_dict["name"]:
                    existing["name"] = t_dict["name"]
            else:
                t_dict["id"] = str(uuid.uuid4())
                tasks_list.append(t_dict)
                seen_ids.add(t_dict["id"])

    return tasks_list

def _process_room_photos_for_plan(room_doc: dict, room_input: CleaningPlanRoomInput) -> List[dict]:
    raw_base = room_doc.get("required_photos", [])
    photos_list = []
    seen_ids = set()

    if isinstance(raw_base, int):
        raw_base = [{"id": str(uuid.uuid4()), "name": f"Required Photo {i+1}"} for i in range(raw_base)]

    for p in raw_base:
        p_dict = dict(p)
        if "id" not in p_dict or not p_dict["id"]:
            p_dict["id"] = str(uuid.uuid4())
        photos_list.append(p_dict)
        seen_ids.add(p_dict["id"])

    input_photos = (room_input.additional_photo_requirements or []) + (room_input.required_photos or [])
    if input_photos:
        for p in input_photos:
            p_dict = p.model_dump()
            p_id = p_dict.get("id")
            existing = next((existing_p for existing_p in photos_list if p_id and existing_p.get("id") == p_id), None)
            if existing:
                if "name" in p_dict and p_dict["name"]:
                    existing["name"] = p_dict["name"]
            else:
                p_dict["id"] = str(uuid.uuid4())
                photos_list.append(p_dict)
                seen_ids.add(p_dict["id"])

    return photos_list

@cleaning_plan_mgmt_router.post(
    "/cleaning-plans",
    response_model=GlobalCleaningPlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Cleaning Plan",
    description="Creates a new cleaning plan for single or multiple rooms/locations, pulling default tasks and photo requirements from room creation, allowing Admin to append new tasks and photo requirements."
)
async def create_global_cleaning_plan(
    plan_in: GlobalCleaningPlanCreate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    if not ObjectId.is_valid(plan_in.client_id):
        raise HTTPException(status_code=400, detail="Invalid client ID")

    client_doc = await db["client_list"].find_one({"_id": ObjectId(plan_in.client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Client record not found")

    loc_id_target = plan_in.location_id
    loc_name_target = None
    if loc_id_target:
        loc_info = await _find_location_info_by_id(db, loc_id_target)
        if not loc_info:
            raise HTTPException(status_code=404, detail="Location not found")
        loc_name_target = loc_info["location_name"]

    processed_rooms = []
    total_duration = 0
    total_photo_req = 0
    total_tasks = 0

    for room_input in plan_in.rooms:
        room_doc = await db["rooms"].find_one({"_id": room_input.room_id})
        if not room_doc:
            raise HTTPException(status_code=404, detail=f"Room ID '{room_input.room_id}' not found")

        custom_name = room_input.custom_room_name or room_doc.get("room_name", "")
        duration = room_input.duration if room_input.duration is not None else room_doc.get("duration", 30)
        clean_type = room_input.clean_type or room_doc.get("clean_type", "standard")

        tasks_list = _process_room_tasks_for_plan(room_doc, room_input)
        photos_list = _process_room_photos_for_plan(room_doc, room_input)

        room_detail = {
            "room_id": room_input.room_id,
            "custom_room_name": custom_name,
            "room_type": room_doc.get("room_type", "standard"),
            "location_id": room_doc.get("location_id", ""),
            "location_name": room_doc.get("location_name", ""),
            "floor": room_doc.get("floor", 1),
            "clean_type": clean_type,
            "duration": duration,
            "required_photos": photos_list,
            "photo_number": len(photos_list),
            "task_number": len(tasks_list),
            "tasks": tasks_list
        }
        processed_rooms.append(room_detail)

        total_duration += duration
        total_photo_req += len(photos_list)
        total_tasks += len(tasks_list)

    if not loc_id_target:
        unique_loc_ids = list(dict.fromkeys(r["location_id"] for r in processed_rooms if r.get("location_id")))
        unique_loc_names = list(dict.fromkeys(r["location_name"] for r in processed_rooms if r.get("location_name")))
        if len(unique_loc_ids) == 1:
            loc_id_target = unique_loc_ids[0]
            loc_name_target = unique_loc_names[0]
        elif len(unique_loc_names) > 1:
            loc_id_target = None
            loc_name_target = ", ".join(unique_loc_names)

    now = datetime.now(timezone.utc)
    plan_id = str(uuid.uuid4())

    plan_doc = {
        "_id": plan_id,
        "id": plan_id,
        "name": plan_in.name,
        "client_id": plan_in.client_id,
        "client_name": client_doc.get("company_name", ""),
        "location_id": loc_id_target,
        "client_location_name": loc_name_target,
        "rooms": processed_rooms,
        "total_duration": total_duration,
        "total_photo_required": total_photo_req,
        "total_tasks_count": total_tasks,
        "created_at": now,
        "updated_at": now
    }

    await db["global_cleaning_plans"].insert_one(plan_doc)

    for r in processed_rooms:
        r["required_photos"] = [RequiredPhotoResponse(**p) for p in r.get("required_photos", [])]
        r["tasks"] = [CleaningTaskResponse(**t) for t in r.get("tasks", [])]

    plan_doc["rooms"] = [CleaningPlanRoomDetail(**r) for r in processed_rooms]
    return GlobalCleaningPlanResponse(**plan_doc)

@cleaning_plan_mgmt_router.get(
    "/cleaning-plans/{plan_id}",
    response_model=GlobalCleaningPlanResponse,
    summary="Get Cleaning Plan Detail",
    description="Returns detailed cleaning plan record by plan ID."
)
async def get_global_cleaning_plan_detail(
    plan_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    plan_doc = await db["global_cleaning_plans"].find_one({"_id": plan_id})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    plan_doc["id"] = plan_doc.get("_id") or plan_doc.get("id")
    for r in plan_doc.get("rooms", []):
        raw_photos = r.get("required_photos", [])
        if isinstance(raw_photos, int):
            raw_photos = [{"id": str(uuid.uuid4()), "name": f"Required Photo {i+1}"} for i in range(raw_photos)]
        r["required_photos"] = [RequiredPhotoResponse(**p) for p in raw_photos]
        r["photo_number"] = len(raw_photos)
        r["tasks"] = [CleaningTaskResponse(**t) for t in r.get("tasks", [])]
    plan_doc["rooms"] = [CleaningPlanRoomDetail(**r) for r in plan_doc.get("rooms", [])]
    return GlobalCleaningPlanResponse(**plan_doc)

@cleaning_plan_mgmt_router.patch(
    "/cleaning-plans/{plan_id}",
    response_model=GlobalCleaningPlanResponse,
    summary="Update Cleaning Plan",
    description="Updates cleaning plan name, room configurations, and modifiable task lists."
)
async def update_global_cleaning_plan(
    plan_id: str,
    plan_in: GlobalCleaningPlanUpdate,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    plan_doc = await db["global_cleaning_plans"].find_one({"_id": plan_id})
    if not plan_doc:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")

    update_fields = {}
    if plan_in.name is not None:
        update_fields["name"] = plan_in.name

    client_id = plan_in.client_id or plan_doc.get("client_id")
    location_id = plan_in.location_id or plan_doc.get("location_id")

    if plan_in.client_id is not None:
        if not ObjectId.is_valid(plan_in.client_id):
            raise HTTPException(status_code=400, detail="Invalid client ID")
        client_doc = await db["client_list"].find_one({"_id": ObjectId(plan_in.client_id)})
        if not client_doc:
            raise HTTPException(status_code=404, detail="Client record not found")
        update_fields["client_id"] = plan_in.client_id
        update_fields["client_name"] = client_doc.get("company_name", "")

    if plan_in.location_id is not None:
        loc_info = await _find_location_info_by_id(db, plan_in.location_id)
        if not loc_info:
            raise HTTPException(status_code=404, detail="Location not found")
        update_fields["location_id"] = plan_in.location_id
        update_fields["client_location_name"] = loc_info["location_name"]

    if plan_in.rooms is not None:
        processed_rooms = []
        total_duration = 0
        total_photo_req = 0
        total_tasks = 0

        for room_input in plan_in.rooms:
            room_doc = await db["rooms"].find_one({"_id": room_input.room_id})
            if not room_doc:
                raise HTTPException(status_code=404, detail=f"Room ID '{room_input.room_id}' not found")

            custom_name = room_input.custom_room_name or room_doc.get("room_name", "")
            duration = room_input.duration if room_input.duration is not None else room_doc.get("duration", 30)
            clean_type = room_input.clean_type or room_doc.get("clean_type", "standard")

            tasks_list = _process_room_tasks_for_plan(room_doc, room_input)
            photos_list = _process_room_photos_for_plan(room_doc, room_input)

            room_detail = {
                "room_id": room_input.room_id,
                "custom_room_name": custom_name,
                "room_type": room_doc.get("room_type", "standard"),
                "location_id": room_doc.get("location_id", ""),
                "location_name": room_doc.get("location_name", ""),
                "floor": room_doc.get("floor", 1),
                "clean_type": clean_type,
                "duration": duration,
                "required_photos": photos_list,
                "photo_number": len(photos_list),
                "task_number": len(tasks_list),
                "tasks": tasks_list
            }
            processed_rooms.append(room_detail)

            total_duration += duration
            total_photo_req += len(photos_list)
            total_tasks += len(tasks_list)

        update_fields["rooms"] = processed_rooms
        update_fields["total_duration"] = total_duration
        update_fields["total_photo_required"] = total_photo_req
        update_fields["total_tasks_count"] = total_tasks

    update_fields["updated_at"] = datetime.now(timezone.utc)
    await db["global_cleaning_plans"].update_one({"_id": plan_id}, {"$set": update_fields})

    updated_doc = await db["global_cleaning_plans"].find_one({"_id": plan_id})
    updated_doc["id"] = updated_doc.get("_id") or updated_doc.get("id")
    for r in updated_doc.get("rooms", []):
        raw_photos = r.get("required_photos", [])
        if isinstance(raw_photos, int):
            raw_photos = [{"id": str(uuid.uuid4()), "name": f"Required Photo {i+1}"} for i in range(raw_photos)]
        r["required_photos"] = [RequiredPhotoResponse(**p) for p in raw_photos]
        r["photo_number"] = len(raw_photos)
        r["tasks"] = [CleaningTaskResponse(**t) for t in r.get("tasks", [])]
    updated_doc["rooms"] = [CleaningPlanRoomDetail(**r) for r in updated_doc.get("rooms", [])]
    return GlobalCleaningPlanResponse(**updated_doc)

@cleaning_plan_mgmt_router.delete(
    "/cleaning-plans/{plan_id}",
    summary="Delete Cleaning Plan",
    description="Deletes cleaning plan record from database."
)
async def delete_global_cleaning_plan(
    plan_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    res = await db["global_cleaning_plans"].delete_one({"_id": plan_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cleaning plan not found")
    return {"message": "Cleaning plan deleted successfully"}
