from fastapi import HTTPException, status
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreate, AdminCreate, UserResponse, WorkerSignup, ClientSignup
from app.models.user import UserInDB, RoleEnum
from app.security.password import get_password_hash
from datetime import datetime, timezone
from app.services.auth_service import AuthService

class UserService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo
        self.auth_service = AuthService(user_repo)

    async def _create_user(self, user_in: UserCreate | AdminCreate, role: RoleEnum) -> UserResponse:
        existing_user = await self.user_repo.get_by_email(user_in.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        hashed_password = get_password_hash(user_in.password)
        new_user = UserInDB(
            full_name=user_in.full_name,
            email=user_in.email,
            phone=user_in.phone,
            hashed_password=hashed_password,
            role=role,
            is_active=True,
            is_verified=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        created_user = await self.user_repo.create(new_user)
        return UserResponse(**created_user.model_dump())

    async def signup_worker(self, user_in: WorkerSignup) -> UserResponse:
        existing_user = await self.user_repo.get_by_email(user_in.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        hashed_password = get_password_hash(user_in.password)
        new_user = UserInDB(
            full_name=user_in.full_name,
            email=user_in.email,
            phone=user_in.phone,
            hashed_password=hashed_password,
            role=RoleEnum.worker,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        created_user = await self.user_repo.create(new_user)
        
        # Automatically send verification OTP after worker signup
        await self.auth_service.generate_and_send_otp(created_user.email, subject="Welcome! Verify your email")
        
        return UserResponse(**created_user.model_dump())

    async def signup_client(self, user_in: ClientSignup) -> UserResponse:
        from app.core.database import get_database
        db = get_database()

        # 1. Check if email is in client_list collection (Admin pre-approval)
        invited_client = await db["client_list"].find_one({"email": user_in.email})
        if not invited_client:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email is not pre-approved for client signup. Please contact Admin."
            )

        # 2. Check if client has already signed up
        if invited_client.get("is_signup", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Client account has already been registered with this email invitation."
            )

        # 3. Validate phone and company_name match the admin-created entry
        if not user_in.phone or user_in.phone.strip() != invited_client.get("phone", "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provided phone number does not match the invitation record."
            )

        if not user_in.company_name or user_in.company_name.strip().lower() != invited_client.get("company_name", "").strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provided company name does not match the invitation record."
            )

        # 4. Check if existing user in users collection
        existing_user = await self.user_repo.get_by_email(user_in.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # 5. Create user in users collection
        hashed_password = get_password_hash(user_in.password)
        new_user = UserInDB(
            full_name=user_in.full_name,
            email=user_in.email,
            phone=user_in.phone,
            company_name=user_in.company_name,
            hashed_password=hashed_password,
            role=RoleEnum.client,
            is_active=True,
            is_verified=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )

        created_user = await self.user_repo.create(new_user)

        # 6. Update is_signup to True in client_list collection
        await db["client_list"].update_one(
            {"_id": invited_client["_id"]},
            {
                "$set": {
                    "is_signup": True,
                    "status": "active",
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )

        # Automatically send verification OTP after client signup
        await self.auth_service.generate_and_send_otp(created_user.email, subject="Welcome! Verify your email")

        return UserResponse(**created_user.model_dump())

    async def create_admin(self, user_in: AdminCreate) -> UserResponse:
        return await self._create_user(user_in, RoleEnum.admin)
