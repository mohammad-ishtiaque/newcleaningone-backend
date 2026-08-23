from fastapi import HTTPException, status
from datetime import datetime, timezone, timedelta
import random
import string
from app.repositories.user_repo import UserRepository
from app.schemas.user import LoginRequest, VerifyEmailRequest, ResendOTPRequest, ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest
from app.schemas.token import Token, RefreshTokenRequest
from app.security.password import verify_password, get_password_hash
from app.security.jwt import create_access_token, create_refresh_token, verify_refresh_token
from app.services.email_service import EmailService
from app.core.config import settings
from app.models.user import RoleEnum

class AuthService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo
        
    def _generate_otp(self) -> str:
        return ''.join(random.choices(string.digits, k=6))

    async def login(self, login_data: LoginRequest) -> Token:
        user = await self.user_repo.get_by_email(login_data.email)
        if not user or not verify_password(login_data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )
            
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inactive user"
            )
            
        if not user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified. Please verify your email to login."
            )
            
        # Worker and Client approval check
        if user.role in [RoleEnum.worker, RoleEnum.client]:
            status_str = getattr(user, "approval_status", "approved")
            if status_str == "rejected":
                reason = getattr(user, "rejection_reason", None)
                detail_msg = f"Your account application was rejected by an administrator: {reason}" if reason else "Your account application was rejected by an administrator."
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=detail_msg
                )
            if not getattr(user, "is_approved", True) or status_str == "pending":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your account is pending admin approval. Please wait for an administrator to approve your application."
                )
            
        # Update last login and device token
        user.last_login = datetime.now(timezone.utc)
        if login_data.onesignal_player_id:
            user.onesignal_player_id = login_data.onesignal_player_id
            
        await self.user_repo.update(user)
        
        # Adjust refresh token lifetime based on remember_me flag
        if login_data.remember_me:
            refresh_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        else:
            refresh_expires = timedelta(days=1)
        
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id, expires_delta=refresh_expires)
        
        user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
        is_temp = getattr(user, "is_temporary_password", False) or False
        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            name=user.full_name,
            role=user_role,
            is_temporary_password=is_temp
        )
        
    async def refresh_token(self, request: RefreshTokenRequest) -> Token:
        user_id = verify_refresh_token(request.refresh_token)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
            
        user = await self.user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
            
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
        is_temp = getattr(user, "is_temporary_password", False) or False
        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            name=user.full_name,
            role=user_role,
            is_temporary_password=is_temp
        )

    async def logout(self):
        return {"message": "Successfully logged out"}

    async def generate_and_send_otp(self, email: str, subject: str = "Your Verification OTP", purpose: str = "verification"):
        user = await self.user_repo.get_by_email(email)
        if not user:
            # Silently return to prevent email enumeration, or raise error. 
            # Given typical requirements, we can raise a 404 for clarity internally.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        otp = self._generate_otp()
        user.otp_code = otp
        user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        await self.user_repo.update(user)
        
        await EmailService.send_otp_email(
            to_email=user.email,
            full_name=user.full_name,
            otp=otp,
            purpose=purpose
        )
        return {"message": "OTP sent to email"}

    async def verify_email(self, request: VerifyEmailRequest) -> Token:
        user = await self.user_repo.get_by_email(request.email)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        expires_at = user.otp_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        if not user.otp_code or user.otp_code != request.otp_code or not expires_at or expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")
            
        user.is_verified = True
        user.otp_code = None
        user.otp_expires_at = None
        user.last_login = datetime.now(timezone.utc)
        if request.onesignal_player_id:
            user.onesignal_player_id = request.onesignal_player_id
            
        await self.user_repo.update(user)

        # Check if individual worker pending admin approval
        if user.role == RoleEnum.worker and not getattr(user, "is_admin_created", False):
            if not getattr(user, "is_approved", False) or getattr(user, "approval_status", "pending") == "pending":
                user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
                return Token(
                    message="OTP verified successfully. Your account is pending admin approval.",
                    name=user.full_name,
                    role=user_role,
                    is_approved=False,
                    approval_status="pending"
                )

        # Check if client pending admin approval
        if user.role == RoleEnum.client and not getattr(user, "is_admin_created", False):
            if not getattr(user, "is_approved", False) or getattr(user, "approval_status", "pending") == "pending":
                from app.services.notification_service import NotificationService
                from app.api.chat import ws_manager
                from app.core.database import get_database

                notif_service = NotificationService()
                await notif_service.create_notification(
                    title="New Client Signup Request",
                    message=f"A new client ({user.company_name or user.full_name}) has signed up and is awaiting approval.",
                    notification_type="approval_request",
                    recipient_type="admin"
                )

                db = get_database()
                admin_cursor = db["users"].find({"role": {"$in": ["admin", "manager"]}})
                admin_ids = [str(u.get("_id") or u.get("id")) async for u in admin_cursor]

                await ws_manager.broadcast_to_users({
                    "type": "new_approval_request",
                    "message": f"New client signup request from {user.company_name or user.full_name}."
                }, admin_ids)

                user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
                return Token(
                    message="OTP verified successfully. Your account is pending admin approval.",
                    name=user.full_name,
                    role=user_role,
                    is_approved=False,
                    approval_status="pending"
                )
        
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            name=user.full_name,
            role=user_role
        )

    async def resend_otp(self, request: ResendOTPRequest):
        return await self.generate_and_send_otp(request.email, "Resend: Your Verification OTP", purpose="resend")

    async def forgot_password(self, request: ForgotPasswordRequest):
        return await self.generate_and_send_otp(request.email, "Password Reset OTP", purpose="forgot_password")

    async def reset_password(self, request: ResetPasswordRequest):
        user = await self.user_repo.get_by_email(request.email)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        expires_at = user.otp_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        if not user.otp_code or user.otp_code != request.otp_code or not expires_at or expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")
            
        user.hashed_password = get_password_hash(request.new_password)
        user.is_temporary_password = False
        user.last_password_changed_at = datetime.now(timezone.utc)
        user.otp_code = None
        user.otp_expires_at = None
        await self.user_repo.update(user)
        
        # Send security notification email
        await EmailService.send_password_changed_email(user.email, user.full_name)
        
        return {"message": "Password reset successfully"}

    async def change_password(self, user_id: str, request: ChangePasswordRequest):
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        if not verify_password(request.old_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")
            
        user.hashed_password = get_password_hash(request.new_password)
        user.is_temporary_password = False
        user.last_password_changed_at = datetime.now(timezone.utc)
        await self.user_repo.update(user)
        
        # Send security notification email
        await EmailService.send_password_changed_email(user.email, user.full_name)
        
        return {"message": "Password changed successfully"}
