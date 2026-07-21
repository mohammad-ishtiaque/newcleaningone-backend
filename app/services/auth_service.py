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
        
        return Token(access_token=access_token, refresh_token=refresh_token, token_type="bearer")
        
    async def refresh_token(self, request: RefreshTokenRequest) -> Token:
        user_id = verify_refresh_token(request.refresh_token)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
            
        user = await self.user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
            
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        return Token(access_token=access_token, refresh_token=refresh_token, token_type="bearer")

    async def logout(self):
        return {"message": "Successfully logged out"}

    async def generate_and_send_otp(self, email: str, subject: str = "Your Verification OTP"):
        user = await self.user_repo.get_by_email(email)
        if not user:
            # Silently return to prevent email enumeration, or raise error. 
            # Given typical requirements, we can raise a 404 for clarity internally.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        otp = self._generate_otp()
        user.otp_code = otp
        user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        await self.user_repo.update(user)
        
        await EmailService.send_email(user.email, subject, f"Your OTP code is {otp}. It expires in 15 minutes.")
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
        
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        return Token(access_token=access_token, refresh_token=refresh_token, token_type="bearer")

    async def resend_otp(self, request: ResendOTPRequest):
        return await self.generate_and_send_otp(request.email, "Resend: Your Verification OTP")

    async def forgot_password(self, request: ForgotPasswordRequest):
        return await self.generate_and_send_otp(request.email, "Password Reset OTP")

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
        user.otp_code = None
        user.otp_expires_at = None
        await self.user_repo.update(user)
        
        return {"message": "Password reset successfully"}

    async def change_password(self, user_id: str, request: ChangePasswordRequest):
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            
        if not verify_password(request.old_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")
            
        user.hashed_password = get_password_hash(request.new_password)
        await self.user_repo.update(user)
        
        return {"message": "Password changed successfully"}
