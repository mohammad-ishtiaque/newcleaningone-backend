import os
from fastapi import APIRouter, Depends, status, HTTPException, Body
from typing import List
from app.models.user import RoleEnum, UserInDB
from app.schemas.user import UserCreate, UserResponse, AdminCreate
from app.dependencies.auth import get_current_user
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.security.password import get_password_hash

router = APIRouter(prefix="/admin", tags=["Admin Service Users"])

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user

def get_user_service() -> UserService:
    repo = UserRepository()
    return UserService(repo)

@router.post("/create-admin", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_admin(
    user_in: AdminCreate,
    secret_key: str,
    user_service: UserService = Depends(get_user_service)
):
    """
    Create a new Admin user using the secret key.
    """
    from app.core.config import settings
    expected_secret = settings.ADMIN_CREATION_SECRET
    if secret_key != expected_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin creation secret key")

    user_dict = user_in.model_dump()
    user_dict["role"] = RoleEnum.admin
    user_dict["is_verified"] = True
    user_dict["is_active"] = True

    existing = await user_service.user_repo.get_by_email(user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_in_db = UserInDB(**user_dict, hashed_password=get_password_hash(user_in.password))
    created_user = await user_service.user_repo.create(user_in_db)

    # Send credentials email via SMTP
    from app.services.email_service import EmailService
    await EmailService.send_credentials_email(
        to_email=user_in.email,
        full_name=user_in.full_name,
        role="Admin",
        password=user_in.password
    )

    return UserResponse(**created_user.model_dump())

@router.post("/create-manager", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_manager(
    user_in: AdminCreate = Body(..., openapi_examples={
        "manager_example": {
            "summary": "A typical manager creation payload",
            "value": {
                "full_name": "Thakur Saad",
                "email": "m1@yopmail.com",
                "password": "Secure123",
                "phone": "+8812345678987",
                "role": "manager"
            }
        }
    }),
    current_user: UserInDB = Depends(require_admin),
    user_service: UserService = Depends(get_user_service)
):
    """
    Create a new Manager user.
    Accessible only by admin.
    """
    user = await user_service._create_user(user_in, RoleEnum.manager)
    return user
