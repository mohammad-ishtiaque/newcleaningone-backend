import os
from fastapi import APIRouter, Depends, status, HTTPException
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
    expected_secret = os.getenv("ADMIN_CREATION_SECRET", "super_admin_secret_key_123")
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
    return UserResponse(**created_user.model_dump())

@router.post("/create-manager", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_manager(
    user_in: AdminCreate,
    current_user: UserInDB = Depends(require_admin),
    user_service: UserService = Depends(get_user_service)
):
    """
    Create a new Manager user.
    Accessible only by admin.
    """
    user = await user_service._create_user(user_in, RoleEnum.manager)
    return user
