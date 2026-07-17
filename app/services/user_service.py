from fastapi import HTTPException, status
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreate, AdminCreate, UserResponse, WorkerSignup, ClientSignup
from app.models.user import UserInDB, RoleEnum
from app.security.password import get_password_hash
from datetime import datetime, timezone

class UserService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

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
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        created_user = await self.user_repo.create(new_user)
        return UserResponse(**created_user.model_dump(by_alias=True))

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
            employee_id=user_in.employee_id,
            isagree_condition=user_in.isagree_condition,
            hashed_password=hashed_password,
            role=RoleEnum.worker,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        created_user = await self.user_repo.create(new_user)
        return UserResponse(**created_user.model_dump(by_alias=True))

    async def signup_client(self, user_in: ClientSignup) -> UserResponse:
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
            company_name=user_in.company_name,
            hashed_password=hashed_password,
            role=RoleEnum.client,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        created_user = await self.user_repo.create(new_user)
        return UserResponse(**created_user.model_dump(by_alias=True))

    async def create_admin(self, user_in: AdminCreate) -> UserResponse:
        return await self._create_user(user_in, RoleEnum.admin)
