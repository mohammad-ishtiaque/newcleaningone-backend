import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.user import AdminUpdate, AdminProfileResponse
from app.schemas.help import (
    CompanyProfileResponse, CompanyProfileUpdate, LegalDocumentResponse, LegalDocumentUpdate,
    SupportMessageResponse, SupportReplyRequest, AdminSupportListResponse,
    FAQCreate, FAQUpdate, FAQResponse
)
from app.models.user import RoleEnum, UserInDB
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user

router = APIRouter(prefix="/manager", tags=["Admin"])

def require_manager(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.manager, RoleEnum.admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
    return current_user

# ================================
# My Profile (Admin/SuperAdmin)
# ================================

@router.get("/me", response_model=AdminProfileResponse)
async def get_my_admin_profile(current_user: UserInDB = Depends(require_manager)):
    return AdminProfileResponse(**current_user.model_dump())

@router.patch("/me", response_model=AdminProfileResponse)
async def update_my_admin_profile(
    admin_update: AdminUpdate,
    current_user: UserInDB = Depends(require_manager),
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
async def get_company_profile(current_user: UserInDB = Depends(require_manager)):
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
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    fields = update_data.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)
    fields["type"] = "main"

    await db["company_profile"].update_one(
        {"type": "main"},
        {"$set": fields},
        upsert=True
    )
    
    updated_profile = await db["company_profile"].find_one({"type": "main"})
    return CompanyProfileResponse(**updated_profile)

# ================================
# Legal Documents (Privacy Policy & Terms)
# ================================

@router.get("/legal-documents/{type}", response_model=LegalDocumentResponse)
async def get_legal_document(
    type: str,
    current_user: UserInDB = Depends(require_manager)
):
    if type not in ["privacy_policy", "terms_and_conditions"]:
        raise HTTPException(status_code=400, detail="Invalid document type")

    db = get_database()
    doc = await db["legal_documents"].find_one({"type": type})
    if not doc:
        title = "Privacy Policy" if type == "privacy_policy" else "Terms and Conditions"
        content = "Initial content for " + title
        doc = {
            "type": type,
            "title": title,
            "content": content,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    else:
        if isinstance(doc.get("updated_at"), datetime):
            doc["updated_at"] = doc["updated_at"].isoformat()
        else:
            doc["updated_at"] = str(doc.get("updated_at", datetime.now(timezone.utc).isoformat()))
    return LegalDocumentResponse(
        type=doc.get("type", type),
        title=doc.get("title", ""),
        content=doc.get("content", ""),
        updated_at=str(doc.get("updated_at", ""))
    )

@router.patch("/legal-documents/{type}", response_model=LegalDocumentResponse)
async def update_legal_document(
    type: str,
    doc_update: LegalDocumentUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    if type not in ["privacy_policy", "terms_and_conditions"]:
        raise HTTPException(status_code=400, detail="Invalid document type")

    db = get_database()
    fields = doc_update.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)

    await db["legal_documents"].update_one(
        {"type": type},
        {"$set": fields},
        upsert=True
    )

    updated_doc = await db["legal_documents"].find_one({"type": type})
    return LegalDocumentResponse(
        type=updated_doc.get("type", type),
        title=updated_doc.get("title", ""),
        content=updated_doc.get("content", ""),
        updated_at=str(updated_doc.get("updated_at", datetime.now(timezone.utc).isoformat()))
    )

# ================================
# Support Messages & Ticket Resolution
# ================================

@router.get("/support-messages", response_model=AdminSupportListResponse)
async def list_support_messages(
    page: int = 1,
    limit: int = 10,
    status_filter: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    skip = (page - 1) * limit
    
    query = {}
    if status_filter:
        query["status"] = status_filter
        
    total_count = await db["support_messages"].count_documents(query)
    unread_count = await db["support_messages"].count_documents({"is_read_by_admin": False})
    cursor = db["support_messages"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_msgs = await cursor.to_list(length=limit)

    support_list = []
    for m in raw_msgs:
        s_id = str(m.get("_id") or m.get("id"))
        w_id = str(m.get("worker_id", ""))
        w_doc = await db["users"].find_one({"_id": ObjectId(w_id)}) if ObjectId.is_valid(w_id) else await db["users"].find_one({"_id": w_id})
        worker_name = w_doc.get("full_name", "Worker") if w_doc else m.get("worker_name", "Worker")
        worker_email = w_doc.get("email") if w_doc else m.get("worker_email")

        created_at = m.get("created_at")
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        updated_at = m.get("updated_at")
        if not isinstance(updated_at, datetime):
            updated_at = created_at

        support_list.append(SupportMessageResponse(
            id=s_id,
            worker_id=w_id,
            worker_name=worker_name,
            worker_email=worker_email,
            subject=m.get("subject", ""),
            description=m.get("description") or m.get("message", ""),
            status=m.get("status", "pending"),
            is_resolved=m.get("is_resolved", False),
            is_read_by_admin=m.get("is_read_by_admin", False),
            is_read_by_worker=m.get("is_read_by_worker", True),
            admin_reply=m.get("admin_reply"),
            replied_at=m.get("replied_at"),
            created_at=created_at,
            updated_at=updated_at
        ))

    return AdminSupportListResponse(
        total_count=total_count,
        unread_count=unread_count,
        messages=support_list
    )

@router.post("/support-messages/{message_id}/reply", response_model=SupportMessageResponse)
async def reply_support_message(
    message_id: str,
    reply_in: SupportReplyRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"_id": ObjectId(message_id)} if ObjectId.is_valid(message_id) else {"_id": message_id}
    msg_doc = await db["support_messages"].find_one(query)
    if not msg_doc:
        raise HTTPException(status_code=404, detail="Support message not found")

    now = datetime.now(timezone.utc)
    await db["support_messages"].update_one(
        query,
        {"$set": {
            "admin_reply": reply_in.admin_reply,
            "status": reply_in.status,
            "is_resolved": reply_in.is_resolved,
            "is_read_by_admin": True,
            "replied_at": now,
            "updated_at": now
        }}
    )

    updated = await db["support_messages"].find_one(query)
    w_id = str(updated.get("worker_id", ""))
    w_doc = await db["users"].find_one({"_id": ObjectId(w_id)}) if ObjectId.is_valid(w_id) else await db["users"].find_one({"_id": w_id})
    worker_name = w_doc.get("full_name", "Worker") if w_doc else updated.get("worker_name", "Worker")

    created_at = updated.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = now

    return SupportMessageResponse(
        id=str(updated.get("_id")),
        worker_id=w_id,
        worker_name=worker_name,
        worker_email=updated.get("worker_email"),
        subject=updated.get("subject", ""),
        description=updated.get("description") or updated.get("message", ""),
        status=updated.get("status", "resolved"),
        is_resolved=updated.get("is_resolved", True),
        is_read_by_admin=True,
        is_read_by_worker=False,
        admin_reply=updated.get("admin_reply"),
        replied_at=updated.get("replied_at"),
        created_at=created_at,
        updated_at=now
    )

# ================================
# FAQs Management
# ================================

@router.get("/faqs", response_model=List[FAQResponse])
async def list_admin_faqs(current_user: UserInDB = Depends(require_manager)):
    db = get_database()
    cursor = db["faqs"].find().sort("created_at", -1)
    raw_faqs = await cursor.to_list(length=100)
    res = []
    for idx, f in enumerate(raw_faqs):
        c_at = f.get("created_at")
        if not isinstance(c_at, datetime):
            c_at = datetime.now(timezone.utc)
        u_at = f.get("updated_at")
        if not isinstance(u_at, datetime):
            u_at = c_at
        res.append(FAQResponse(
            id=str(f.get("_id") or f.get("id")),
            serial_no=f.get("serial_no") or (idx + 1),
            question=f.get("question", ""),
            answer=f.get("answer", ""),
            created_at=c_at,
            updated_at=u_at
        ))
    return res

@router.post("/faqs", response_model=FAQResponse, status_code=status.HTTP_201_CREATED)
async def create_faq(
    faq_in: FAQCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    faq_id = f"faq_{uuid.uuid4().hex[:8]}"
    
    max_doc = await db["faqs"].find_one({}, sort=[("serial_no", -1)])
    s_no = faq_in.serial_no or ((max_doc.get("serial_no", 0) + 1) if max_doc else 1)
    
    doc = {
        "_id": faq_id,
        "id": faq_id,
        "serial_no": s_no,
        "question": faq_in.question,
        "answer": faq_in.answer,
        "created_at": now,
        "updated_at": now
    }
    await db["faqs"].insert_one(doc)
    return FAQResponse(**doc)

@router.patch("/faqs/{faq_id}", response_model=FAQResponse)
async def update_faq(
    faq_id: str,
    faq_in: FAQUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": faq_id}, {"id": faq_id}]}
    doc = await db["faqs"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="FAQ not found")

    fields = faq_in.model_dump(exclude_unset=True)
    fields["updated_at"] = datetime.now(timezone.utc)

    await db["faqs"].update_one(query, {"$set": fields})
    updated = await db["faqs"].find_one(query)
    c_at = updated.get("created_at")
    if not isinstance(c_at, datetime):
        c_at = datetime.now(timezone.utc)
    u_at = updated.get("updated_at")
    if not isinstance(u_at, datetime):
        u_at = c_at
    return FAQResponse(
        id=str(updated.get("_id") or updated.get("id")),
        serial_no=updated.get("serial_no", 1),
        question=updated.get("question", ""),
        answer=updated.get("answer", ""),
        created_at=c_at,
        updated_at=u_at
    )

@router.delete("/faqs/{faq_id}", status_code=status.HTTP_200_OK)
async def delete_faq(
    faq_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {"$or": [{"_id": faq_id}, {"id": faq_id}]}
    res = await db["faqs"].delete_one(query)
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return {"message": "FAQ deleted successfully"}
