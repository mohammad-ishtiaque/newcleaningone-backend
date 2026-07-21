from datetime import datetime
from fastapi import APIRouter, Depends, status, HTTPException
from app.schemas.user import ClientSignup, ClientUpdate, ClientProfileResponse
from app.schemas.help import LegalDocumentResponse
from app.schemas.notification import NotificationListResponse
from app.services.user_service import UserService
from app.services.notification_service import NotificationService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.core.database import get_database

router = APIRouter(prefix="/client", tags=["Client"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user

@router.post("/signup", response_model=ClientProfileResponse, status_code=status.HTTP_201_CREATED)
async def signup_client(
    user_in: ClientSignup,
    user_service: UserService = Depends(get_user_service)
):
    user_resp = await user_service.signup_client(user_in)
    # The user_service returns a generic UserResponse. We can construct a ClientProfileResponse manually or alter user_service.
    return user_resp

@router.get("/", response_model=ClientProfileResponse)
async def get_client_profile(current_user: UserInDB = Depends(require_client)):
    return ClientProfileResponse(**current_user.model_dump())

@router.patch("/", response_model=ClientProfileResponse)
async def update_client_profile(
    client_update: ClientUpdate,
    current_user: UserInDB = Depends(require_client),
    user_repo: UserRepository = Depends(UserRepository)
):
    update_data = client_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
        
    await user_repo.update(current_user)
    return ClientProfileResponse(**current_user.model_dump())

@router.get("/privacy-policy", response_model=LegalDocumentResponse)
async def get_client_privacy_policy():
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "privacy_policy"})
    if doc:
        return LegalDocumentResponse(
            title=doc.get("title", "Privacy Policy"),
            content=doc.get("content", ""),
            updated_at=doc.get("updated_at", datetime.now().isoformat())
        )
    return LegalDocumentResponse(
        title="Privacy Policy",
        content="Our Privacy Policy is currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/terms-and-conditions", response_model=LegalDocumentResponse)
async def get_client_terms_and_conditions():
    db = get_database()
    doc = await db["legal_documents"].find_one({"type": "terms_and_conditions"})
    if doc:
        return LegalDocumentResponse(
            title=doc.get("title", "Terms & Conditions"),
            content=doc.get("content", ""),
            updated_at=doc.get("updated_at", datetime.now().isoformat())
        )
    return LegalDocumentResponse(
        title="Terms & Conditions",
        content="Our Terms & Conditions are currently being drafted and will be updated soon.",
        updated_at=datetime.now().isoformat()
    )

@router.get("/notifications", response_model=NotificationListResponse)
async def get_client_notifications(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    return await service.get_user_notifications(user_id=current_user.id, recipient_type="client", page=page, limit=limit)

@router.patch("/notifications/{notification_id}/read")
async def mark_client_notification_read(
    notification_id: str,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    success = await service.mark_notification_as_read(notification_id=notification_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}

@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client_notification(
    notification_id: str,
    current_user: UserInDB = Depends(require_client)
):
    service = NotificationService()
    success = await service.delete_user_notification(notification_id=notification_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return None
