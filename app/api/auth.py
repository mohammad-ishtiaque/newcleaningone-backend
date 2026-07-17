from fastapi import APIRouter, Depends, status
from typing import Any
from app.schemas.user import (
    UserResponse, LoginRequest, VerifyEmailRequest, ResendOTPRequest,
    ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest,
    WorkerProfileResponse, ClientProfileResponse, AdminProfileResponse
)
from app.schemas.token import Token, RefreshTokenRequest
from app.services.auth_service import AuthService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum

router = APIRouter(prefix="/auth", tags=["Auth"])

def get_auth_service(user_repo: UserRepository = Depends(UserRepository)) -> AuthService:
    return AuthService(user_repo)

@router.post("/login", response_model=Token)
async def login(
    login_data: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.login(login_data)

@router.post("/verify-email")
async def verify_email(
    request: VerifyEmailRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.verify_email(request)

@router.post("/resend-otp")
async def resend_otp(
    request: ResendOTPRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.resend_otp(request)

@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.forgot_password(request)

@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.reset_password(request)

@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    auth_service: AuthService = Depends(get_auth_service),
    current_user: UserInDB = Depends(get_current_user)
):
    return await auth_service.change_password(current_user.id, request)

@router.post("/refresh", response_model=Token)
async def refresh(
    request: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    return await auth_service.refresh_token(request)

@router.post("/logout")
async def logout(
    auth_service: AuthService = Depends(get_auth_service),
    current_user: UserInDB = Depends(get_current_user)
):
    return await auth_service.logout()

@router.get("/me")
async def get_me(current_user: UserInDB = Depends(get_current_user)) -> Any:
    user_data = current_user.model_dump()
    if current_user.role == RoleEnum.worker:
        user_data["id_uploaded"] = bool(current_user.id_card_front and current_user.id_card_back)
        user_data["id_card_front_link"] = current_user.id_card_front
        user_data["id_card_back_link"] = current_user.id_card_back
        user_data["certificate_uploaded"] = len(current_user.certificates) > 0
        user_data["certificate_count"] = len(current_user.certificates)
        user_data["certificate_links"] = current_user.certificates
        user_data["profile_photo_uploaded"] = bool(current_user.profile_photo)
        user_data["profile_photo_link"] = current_user.profile_photo
        return WorkerProfileResponse(**user_data)
    elif current_user.role == RoleEnum.client:
        return ClientProfileResponse(**user_data)
    elif current_user.role == RoleEnum.admin or current_user.role == RoleEnum.super_admin:
        return AdminProfileResponse(**user_data)
    return UserResponse(**user_data)

@router.delete("/me")
async def delete_me(
    current_user: UserInDB = Depends(get_current_user),
    user_repo: UserRepository = Depends(UserRepository)
):
    # Depending on requirements, soft delete is safer, but user asked for "Delete Me"
    # We will soft delete for data integrity or hard delete based on preference. Let's do soft delete.
    current_user.is_active = False
    await user_repo.update(current_user)
    return {"message": "User account deactivated (deleted)"}
