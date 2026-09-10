from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import List, Optional, Union
from datetime import date, datetime, timezone
from bson import ObjectId
from app.schemas.user import (
    WorkerSignup, UserResponse, WorkerOnboardingStep1, WorkerProfileResponse,
    WorkerDraftResponse, SignupResponse, WorkerProfileEdit, PushSettingsUpdate,
    PushSettingsResponse, WorkerPersonalInformation,
    WorkerWorkingDaysResponse, WorkerWorkingDaysUpdate
)
from app.api.worker_shift_utils import normalize_working_days
from app.schemas.help import SupportMessageRequest, FAQListResponse, FAQDetailResponse, LegalDocumentResponse, SupportMessageResponse, WorkerSupportListResponse
from app.models.support import SupportMessageDB
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum, WorkerTypeEnum
from app.services.s3_service import S3Service
from app.api.profile import process_image
from app.schemas.notification import (
    NotificationListResponse, NotificationResponse,
    NotificationBulkDeleteRequest, NotificationBulkDeleteResponse
)
from app.services.notification_service import NotificationService
from app.services.faq_service import FAQService

router = APIRouter(prefix="/worker", tags=["Worker Profile"])


def user_id_query(user_id: str) -> dict:
    """
    Build a `users` filter that matches a worker by id.

    `_id` is stored as an ObjectId, while the id carried on the authenticated
    user is a plain string, so a filter of `{"_id": "<hex>"}` matches nothing.
    Matching on the string alone made `find_one` return None (falling back to a
    hardcoded default schedule) and `update_one` match zero documents while
    still reporting success. Cover the ObjectId form plus the string/`id` forms
    used by older documents.
    """
    clauses: List[dict] = []
    if ObjectId.is_valid(user_id):
        clauses.append({"_id": ObjectId(user_id)})
    clauses.append({"_id": user_id})
    clauses.append({"id": user_id})
    return {"$or": clauses}

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def get_s3_service() -> S3Service:
    return S3Service()

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.worker and current_user.role != "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user

@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup_worker(
    user_in: WorkerSignup,
    user_service: UserService = Depends(get_user_service)
):
    # Pass WorkerSignup attributes properly
    user = await user_service.signup_worker(user_in)
    return SignupResponse(user=user)

@router.post("/onboarding/step1", response_model=WorkerProfileResponse)
async def onboarding_step1(
    step1_data: WorkerOnboardingStep1,
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    if not step1_data.isagree_condition:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You must agree to the terms and conditions to proceed.")

    # Validation: Individual Worker Signups are locked to freelancer
    if not getattr(current_user, "is_admin_created", False):
        if step1_data.worker_type == WorkerTypeEnum.employee:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Individual worker signups are only allowed to register as freelancers."
            )
        
    current_user.onboarding_draft["dob"] = step1_data.dob.isoformat()
    current_user.onboarding_draft["isagree_condition"] = step1_data.isagree_condition
    current_user.onboarding_draft["nationality"] = step1_data.nationality
    current_user.onboarding_draft["worker_type"] = step1_data.worker_type.value
    
    current_user.onboarding_complete1 = True
    await user_repo.update(current_user)
    
    # We construct the response
    return await _build_worker_response(current_user)

from typing import Union

@router.post("/onboarding/step2", response_model=WorkerProfileResponse)
async def onboarding_step2(
    id_card_front: Optional[UploadFile] = File(None),
    id_card_back: Optional[UploadFile] = File(None),
    profile_photo: Optional[UploadFile] = File(None),
    certificates: List[Union[UploadFile, str]] = File(default=[]),
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository),
    s3_service: S3Service = Depends(get_s3_service)
):
    if not current_user.onboarding_complete1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please complete Step 1 first")
        
    if len(certificates) > 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum 5 certificates allowed")

    # Upload or verify ID Front
    if id_card_front and id_card_front.filename:
        front_url = await s3_service.upload_file(await id_card_front.read(), id_card_front.filename, id_card_front.content_type)
        current_user.onboarding_draft["id_card_front"] = front_url
    elif not current_user.onboarding_draft.get("id_card_front") and not current_user.id_card_front:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID card front is required")

    # Upload or verify ID Back
    if id_card_back and id_card_back.filename:
        back_url = await s3_service.upload_file(await id_card_back.read(), id_card_back.filename, id_card_back.content_type)
        current_user.onboarding_draft["id_card_back"] = back_url
    elif not current_user.onboarding_draft.get("id_card_back") and not current_user.id_card_back:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID card back is required")

    # Upload Certificates
    cert_urls = current_user.onboarding_draft.get("certificates", [])
    for cert in certificates:
        if isinstance(cert, UploadFile) and cert.filename:
            url = await s3_service.upload_file(await cert.read(), cert.filename, cert.content_type)
            if url:
                cert_urls.append(url)
    current_user.onboarding_draft["certificates"] = cert_urls
    
    # Upload Profile Photo with resize
    if profile_photo and profile_photo.filename:
        processed_photo = process_image(await profile_photo.read())
        photo_url = await s3_service.upload_file(processed_photo, profile_photo.filename)
        current_user.onboarding_draft["profile_photo"] = photo_url

    await user_repo.update(current_user)
    return await _build_worker_response(current_user)

@router.get("/onboarding/draft", response_model=WorkerDraftResponse)
async def get_onboarding_draft(current_user: UserInDB = Depends(require_worker)):
    draft = current_user.onboarding_draft
    return WorkerDraftResponse(
        dob=draft.get("dob"),
        nationality=draft.get("nationality"),
        worker_type=draft.get("worker_type"),
        isagree_condition=draft.get("isagree_condition"),
        id_card_front_link=draft.get("id_card_front"),
        id_card_back_link=draft.get("id_card_back"),
        profile_photo_link=draft.get("profile_photo"),
        certificate_count=len(draft.get("certificates", [])),
        certificate_links=draft.get("certificates", [])
    )

@router.patch("/onboarding/draft", response_model=WorkerDraftResponse)
async def patch_onboarding_draft(
    dob: Optional[date] = Form(None),
    nationality: Optional[str] = Form(None),
    worker_type: Optional[WorkerTypeEnum] = Form(None),
    isagree_condition: Optional[bool] = Form(None),
    id_card_front: Optional[UploadFile] = File(None),
    id_card_back: Optional[UploadFile] = File(None),
    profile_photo: Optional[UploadFile] = File(None),
    certificates: List[Union[UploadFile, str]] = File(default=[]),
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository),
    s3_service: S3Service = Depends(get_s3_service)
):
    draft = current_user.onboarding_draft
    
    if dob is not None:
        draft["dob"] = dob.isoformat()
    if nationality is not None:
        draft["nationality"] = nationality
    if worker_type is not None:
        draft["worker_type"] = worker_type.value
    if isagree_condition is not None:
        draft["isagree_condition"] = isagree_condition
        
    if id_card_front:
        draft["id_card_front"] = await s3_service.upload_file(await id_card_front.read(), id_card_front.filename, id_card_front.content_type)
        
    if id_card_back:
        draft["id_card_back"] = await s3_service.upload_file(await id_card_back.read(), id_card_back.filename, id_card_back.content_type)
        
    if profile_photo:
        processed_photo = process_image(await profile_photo.read())
        draft["profile_photo"] = await s3_service.upload_file(processed_photo, profile_photo.filename)
        
    if certificates:
        cert_urls = draft.get("certificates", [])
        for cert in certificates:
            if isinstance(cert, UploadFile) and cert.filename:
                url = await s3_service.upload_file(await cert.read(), cert.filename, cert.content_type)
                if url:
                    cert_urls.append(url)
        draft["certificates"] = cert_urls
        
    current_user.onboarding_draft = draft
    await user_repo.update(current_user)
    
    return WorkerDraftResponse(
        dob=draft.get("dob"),
        nationality=draft.get("nationality"),
        worker_type=draft.get("worker_type"),
        isagree_condition=draft.get("isagree_condition"),
        id_card_front_link=draft.get("id_card_front"),
        id_card_back_link=draft.get("id_card_back"),
        profile_photo_link=draft.get("profile_photo"),
        certificate_count=len(draft.get("certificates", [])),
        certificate_links=draft.get("certificates", [])
    )

@router.post("/onboarding/confirm")
async def confirm_onboarding(
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    if not current_user.onboarding_complete1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step 1 incomplete")
        
    draft = current_user.onboarding_draft
    
    if not draft.get("id_card_front") or not draft.get("id_card_back"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step 2 incomplete (missing ID cards)")
        
    # Map draft to actual profile fields
    current_user.dob = draft.get("dob")
    current_user.isagree_condition = draft.get("isagree_condition")
    current_user.nationality = draft.get("nationality")
    current_user.worker_type = draft.get("worker_type")
    
    current_user.id_card_front = draft.get("id_card_front")
    current_user.id_card_back = draft.get("id_card_back")
    current_user.certificates = draft.get("certificates", [])
    if draft.get("profile_photo"):
        current_user.profile_photo = draft.get("profile_photo")
        
    # Generate unique sequential employee_id
    seq = await user_repo.get_next_employee_sequence()
    current_user.employee_id = f"CL-2026-{seq}"
        
    # Clear draft and finalize
    current_user.onboarding_draft = {}
    current_user.is_profile_completed = True
    
    await user_repo.update(current_user)
    return {"message": "Onboarding confirmed successfully"}

@router.get("/profile/edit-profile", response_model=WorkerProfileEdit)
async def get_worker_profile_edit(current_user: UserInDB = Depends(require_worker)):
    return WorkerProfileEdit(
        full_name=current_user.full_name,
        phone=current_user.phone,
        location=current_user.location
    )

@router.patch("/profile/edit-profile", response_model=WorkerProfileResponse)
async def patch_worker_profile_edit(
    edit_data: WorkerProfileEdit,
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    if edit_data.full_name is not None:
        current_user.full_name = edit_data.full_name
    if edit_data.phone is not None:
        current_user.phone = edit_data.phone
    if edit_data.location is not None:
        current_user.location = edit_data.location
        
    await user_repo.update(current_user)
    return await _build_worker_response(current_user)

@router.get("/profile/personal-information", response_model=WorkerPersonalInformation)
async def get_worker_profile_edit(current_user: UserInDB = Depends(require_worker)):
    return WorkerPersonalInformation(
        full_name=current_user.full_name,
        phone=current_user.phone,
        email=current_user.email,
        employee_id=current_user.employee_id,
        location=current_user.location
    )

@router.patch("/profile/personal-information", response_model=WorkerPersonalInformation)
async def patch_worker_profile_edit(
    edit_data: WorkerPersonalInformation,
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    if edit_data.full_name is not None:
        current_user.full_name = edit_data.full_name
    if edit_data.phone is not None:
        current_user.phone = edit_data.phone
    if edit_data.email is not None:
        current_user.email = edit_data.email
    if edit_data.employee_id is not None:
        current_user.employee_id = edit_data.employee_id    
    if edit_data.location is not None:
        current_user.location = edit_data.location
        
    await user_repo.update(current_user)
    return await _build_worker_response(current_user)

@router.patch("/profile/push-settings", response_model=PushSettingsResponse)
async def patch_push_settings(
    settings_data: PushSettingsUpdate,
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    # Toggle the setting
    current_user.push_notifications_enabled = not current_user.push_notifications_enabled
    
    if settings_data.onesignal_player_id is not None:
        current_user.onesignal_player_id = settings_data.onesignal_player_id
        
    await user_repo.update(current_user)
    return PushSettingsResponse(
        push_notifications_enabled=current_user.push_notifications_enabled,
        onesignal_player_id=current_user.onesignal_player_id
    )


@router.get(
    "/profile/working-days",
    response_model=WorkerWorkingDaysResponse,
    summary="Get Worker Working Days & Availability Schedule",
    description="""
### Get Worker Working Days & Availability Schedule
Returns the worker's active weekly working days (e.g. `['mon', 'tue', 'wed', 'thu', 'fri', 'sat']`) and calculated off-duty days (e.g. `['sun']`).

When a manager assigns shifts or views worker availability dropdowns, the system automatically checks these working days to determine if a worker is available or off-duty.
"""
)
async def get_worker_working_days(
    current_user: UserInDB = Depends(require_worker)
):
    from app.core.database import get_database
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    worker_name = getattr(current_user, "full_name", "Worker")

    # Fetch from database to ensure freshest state
    u_doc = await db["users"].find_one(user_id_query(worker_id)) or {}
    raw_days = u_doc.get("working_days") or getattr(current_user, "working_days", None) or ["mon", "tue", "wed", "thu", "fri", "sat"]

    working_days, off_days = normalize_working_days(raw_days)
    uat = u_doc.get("updated_at") if isinstance(u_doc.get("updated_at"), datetime) else None

    return WorkerWorkingDaysResponse(
        worker_id=worker_id,
        worker_name=worker_name,
        working_days=working_days,
        off_days=off_days,
        total_working_days=len(working_days),
        updated_at=uat
    )


@router.patch(
    "/profile/working-days",
    response_model=WorkerWorkingDaysResponse,
    summary="Update Worker Working Days Schedule",
    description="""
### Update Worker Working Days Schedule
Updates the worker's active working days. Days not included in `working_days` are treated as worker off-days.

#### Supported Values for `working_days`:
- **3-letter Abbreviations**: `['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']`
- **Full Day Names**: `['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']`

#### Example Request:
```json
{
  "working_days": ["mon", "tue", "wed", "thu", "fri"]
}
```
"""
)
async def update_worker_working_days(
    days_in: WorkerWorkingDaysUpdate,
    current_user: UserInDB = Depends(require_worker)
):
    from app.core.database import get_database
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    worker_name = getattr(current_user, "full_name", "Worker")
    worker_email = getattr(current_user, "email", None)

    working_days, off_days = normalize_working_days(days_in.working_days)
    now = datetime.now(timezone.utc)

    # 1. Update in users collection
    result = await db["users"].update_one(
        user_id_query(worker_id),
        {"$set": {"working_days": working_days, "updated_at": now}}
    )
    # update_one is silent when the filter matches nothing, which previously let
    # this endpoint echo the requested schedule back as if it had been saved.
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Worker account not found; working days were not saved")

    # 2. Sync to admin_workers collection
    if worker_email:
        await db["admin_workers"].update_one(
            {"email": worker_email},
            {"$set": {"working_days": working_days, "updated_at": now}},
            upsert=True
        )

    # 3. Sync to worker_availability collection
    DAY_TO_FULL = {
        "mon": "monday", "tue": "tuesday", "wed": "wednesday",
        "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"
    }
    weekly_slots = [
        {"day": DAY_TO_FULL.get(d, d), "is_available": (d in working_days), "start_time": "08:00 AM", "end_time": "05:00 PM"}
        for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    ]
    await db["worker_availability"].update_one(
        {"worker_id": worker_id},
        {"$set": {"weekly_availability": weekly_slots, "updated_at": now}, "$setOnInsert": {"created_at": now}},
        upsert=True
    )

    current_user.working_days = working_days

    return WorkerWorkingDaysResponse(
        worker_id=worker_id,
        worker_name=worker_name,
        working_days=working_days,
        off_days=off_days,
        total_working_days=len(working_days),
        updated_at=now
    )


@router.get(
    "/working-days",
    response_model=WorkerWorkingDaysResponse,
    summary="Get Worker Working Days (Alias)",
    description="Alias for GET /worker/profile/working-days",
    include_in_schema=False
)
async def get_worker_working_days_alias(
    current_user: UserInDB = Depends(require_worker)
):
    return await get_worker_working_days(current_user=current_user)


@router.patch(
    "/working-days",
    response_model=WorkerWorkingDaysResponse,
    summary="Update Worker Working Days (Alias)",
    description="Alias for PATCH /worker/profile/working-days",
    include_in_schema=False
)
async def update_worker_working_days_alias(
    days_in: WorkerWorkingDaysUpdate,
    current_user: UserInDB = Depends(require_worker)
):
    return await update_worker_working_days(days_in=days_in, current_user=current_user)



@router.get("/profile", response_model=WorkerProfileResponse)
async def get_worker_profile(current_user: UserInDB = Depends(require_worker)):
    return await _build_worker_response(current_user)

@router.get("/help/faq", response_model=FAQListResponse)
async def get_faqs(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_worker)
):
    return await FAQService.get_faqs_list(page, limit)

@router.get("/help/faq/{faq_id}", response_model=FAQDetailResponse)
async def get_faq_detail(
    faq_id: int,
    current_user: UserInDB = Depends(require_worker)
):
    faq = await FAQService.get_faq_detail(faq_id)
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return faq

@router.post("/help/message", response_model=SupportMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_support_message(
    message: SupportMessageRequest,
    current_user: UserInDB = Depends(require_worker)
):
    from app.core.database import get_database
    db = get_database()
    
    support_msg = SupportMessageDB(
        worker_id=current_user.id,
        worker_name=current_user.full_name,
        worker_email=current_user.email,
        subject=message.subject,
        description=message.description,
        status="pending",
        is_resolved=False,
        is_read_by_admin=False,
        is_read_by_worker=True
    )
    doc = support_msg.model_dump(by_alias=True, exclude={"id"})
    res = await db["support_messages"].insert_one(doc)
    doc["_id"] = str(res.inserted_id)

    from app.services.notification_service import NotificationService
    await NotificationService().create_notification(
        title=f"New Worker Support Ticket: {message.subject}",
        message=f"{current_user.full_name} sent an inquiry: {message.description[:100]}",
        notification_type="support",
        route_type="settings",
        recipient_type="manager",
        data={
            "support_message_id": str(res.inserted_id),
            "route": "/manager/support-messages",
            "deeplink": f"cleaningone://manager/support-messages?id={res.inserted_id}"
        }
    )

    return SupportMessageResponse(**doc)

@router.get("/help/messages", response_model=WorkerSupportListResponse)
async def get_my_support_messages(
    current_user: UserInDB = Depends(require_worker)
):
    from app.core.database import get_database
    db = get_database()
    
    cursor = db["support_messages"].find({"worker_id": current_user.id}).sort("created_at", -1)
    messages = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        messages.append(SupportMessageResponse(**doc))
        
    return WorkerSupportListResponse(
        total_count=len(messages),
        messages=messages
    )

@router.get("/help/messages/{message_id}", response_model=SupportMessageResponse)
async def get_my_support_message_detail(
    message_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    from app.core.database import get_database
    from bson import ObjectId
    db = get_database()
    
    q_id = ObjectId(message_id) if ObjectId.is_valid(message_id) else message_id
    doc = await db["support_messages"].find_one({
        "$or": [{"_id": q_id}, {"_id": message_id}, {"id": message_id}],
        "worker_id": str(current_user.id)
    })
    if not doc:
        doc = await db["support_messages"].find_one({"$or": [{"_id": q_id}, {"_id": message_id}, {"id": message_id}]})
        if not doc:
            raise HTTPException(status_code=404, detail="Support message not found")
        
    if not doc.get("is_read_by_worker", False):
        await db["support_messages"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"is_read_by_worker": True}}
        )
        doc["is_read_by_worker"] = True
        
    doc["_id"] = str(doc["_id"])
    return SupportMessageResponse(**doc)

@router.get("/privacy-policy", response_model=LegalDocumentResponse)
async def get_privacy_policy():
    from app.core.database import get_database
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "privacy_policy"})
    if doc:
        u_at = doc.get("updated_at")
        u_str = u_at.isoformat() if hasattr(u_at, "isoformat") else str(u_at or datetime.now().isoformat())
        return LegalDocumentResponse(
            type="privacy_policy",
            title=doc.get("title", "Privacy Policy"),
            content=doc.get("content", ""),
            updated_at=u_str
        )
    return LegalDocumentResponse(
        type="privacy_policy",
        title="Privacy Policy",
        content="Our Privacy Policy is currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/terms-and-conditions", response_model=LegalDocumentResponse)
async def get_terms_and_conditions():
    from app.core.database import get_database
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "terms_and_conditions"})
    if doc:
        u_at = doc.get("updated_at")
        u_str = u_at.isoformat() if hasattr(u_at, "isoformat") else str(u_at or datetime.now().isoformat())
        return LegalDocumentResponse(
            type="terms_and_conditions",
            title=doc.get("title", "Terms & Conditions"),
            content=doc.get("content", ""),
            updated_at=u_str
        )
    return LegalDocumentResponse(
        type="terms_and_conditions",
        title="Terms & Conditions",
        content="Our Terms & Conditions are currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/notifications", response_model=NotificationListResponse)
async def get_worker_notifications(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    service = NotificationService()
    return await service.get_user_notifications(user_id=worker_id, recipient_type="worker", page=page, limit=limit)


@router.post("/notifications/bulk-delete", response_model=NotificationBulkDeleteResponse, summary="Bulk Delete Notifications")
async def bulk_delete_worker_notifications(
    payload: NotificationBulkDeleteRequest,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    service = NotificationService()
    count = await service.bulk_delete_notifications(
        user_id=worker_id,
        recipient_type="worker",
        notification_ids=payload.notification_ids,
        delete_all=payload.delete_all
    )
    return NotificationBulkDeleteResponse(deleted_count=count, message=f"{count} notification(s) deleted")


@router.get("/notifications/{notification_id}", response_model=NotificationResponse, summary="Get Single Notification Details")
async def get_worker_notification_detail(
    notification_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    service = NotificationService()
    doc = await service.get_notification_detail(notification_id=notification_id, user_id=worker_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Notification not found")
    return doc

@router.patch("/notifications/{notification_id}/read")
async def mark_worker_notification_as_read(
    notification_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    service = NotificationService()
    success = await service.mark_notification_as_read(notification_id=notification_id, user_id=worker_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found or cannot be updated")
    return {"message": "Notification marked as read"}

@router.delete("/notifications/{notification_id}")
async def delete_worker_notification(
    notification_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    service = NotificationService()
    success = await service.delete_notification(notification_id=notification_id, user_id=worker_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found or cannot be deleted")
    return {"message": "Notification deleted successfully"}

async def _build_worker_response(user: UserInDB) -> WorkerProfileResponse:
    from app.core.database import get_database
    from app.api.worker_availability import build_worker_availability_response

    user_data = user.model_dump()
    raw_days = user_data.get("working_days") or getattr(user, "working_days", None) or ["mon", "tue", "wed", "thu", "fri", "sat"]
    working_days, off_days = normalize_working_days(raw_days)
    user_data["working_days"] = working_days
    user_data["off_days"] = off_days

    worker_id = str(getattr(user, "id", None) or getattr(user, "_id", None) or getattr(user, "mongo_id", None) or "")
    user_data["availability"] = await build_worker_availability_response(worker_id, get_database())
    user_data["id_uploaded"] = bool(user.id_card_front and user.id_card_back)
    user_data["id_card_front_link"] = user.id_card_front
    user_data["id_card_back_link"] = user.id_card_back
    user_data["certificate_uploaded"] = len(user.certificates) > 0
    user_data["certificate_count"] = len(user.certificates)
    user_data["certificate_links"] = user.certificates
    user_data["profile_photo_uploaded"] = bool(user.profile_photo)
    user_data["profile_photo_link"] = user.profile_photo
    user_data["push_notifications_enabled"] = user.push_notifications_enabled
    return WorkerProfileResponse(**user_data)
