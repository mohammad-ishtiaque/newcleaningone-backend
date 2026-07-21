from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone
from app.core.database import get_database
from app.schemas.user import AdminCreate, AdminUpdate, AdminProfileResponse, UserResponse
from app.schemas.help import (
    CompanyProfileResponse, CompanyProfileUpdate, LegalDocumentResponse, LegalDocumentUpdate,
    SupportMessageResponse, SupportReplyRequest, AdminSupportListResponse,
    FAQCreate, FAQUpdate, FAQResponse
)
from app.services.notification_service import NotificationService
from app.services.faq_service import FAQService
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.dependencies.rbac import RequireRole
from app.dependencies.auth import get_current_user
from app.models.user import RoleEnum, UserInDB
from app.security.password import get_password_hash

router = APIRouter(prefix="/admin", tags=["Admin"])

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

@router.delete("/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_faq(
    faq_id: str,
    current_user: UserInDB = Depends(require_admin)
):
    deleted = await FAQService.delete_faq(faq_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return None

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
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def delete_admin(admin_id: str, user_repo: UserRepository = Depends(UserRepository)):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
        
    await user_repo.collection.delete_one({"_id": ObjectId(admin_id)})
    return None
