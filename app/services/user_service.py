from fastapi import HTTPException, status
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreate, AdminCreate, UserResponse, WorkerSignup, ClientSignup, ClientProfileResponse
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
        from app.core.database import get_database
        from app.models.user import WorkerTypeEnum
        db = get_database()

        existing_user = await self.user_repo.get_by_email(user_in.email)
        if existing_user:
            from bson import ObjectId
            # If the user is a previously rejected worker, allow them to re-apply by replacing the old rejected record
            if existing_user.role == RoleEnum.worker and getattr(existing_user, "approval_status", "") == "rejected":
                user_obj_id = ObjectId(existing_user.id) if ObjectId.is_valid(existing_user.id) else existing_user.id
                await db["users"].delete_one({"_id": user_obj_id})
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )

        # Check if email exists in admin_workers collection
        admin_worker = await db["admin_workers"].find_one({"email": user_in.email})

        hashed_password = get_password_hash(user_in.password)
        now = datetime.now(timezone.utc)

        if admin_worker:
            # 1. Admin-created worker pre-entry validation
            if not user_in.full_name or user_in.full_name.strip().lower() != admin_worker.get("name", "").strip().lower():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Provided name does not match the Admin pre-created record."
                )

            if not user_in.phone or user_in.phone.strip() != admin_worker.get("phone", "").strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Provided phone number does not match the Admin pre-created record."
                )

            wt = admin_worker.get("worker_type")
            valid_types = [e.value for e in WorkerTypeEnum]
            w_enum = WorkerTypeEnum(wt) if wt in valid_types else None

            # Prepare onboarding draft pre-filled with admin data
            draft = {
                "worker_type": wt,
                "position": admin_worker.get("position"),
                "location": admin_worker.get("base_location"),
                "languages": admin_worker.get("languages", []),
                "id_card_front": admin_worker.get("national_id_front"),
                "id_card_back": admin_worker.get("national_id_back"),
                "employee_contract_pdf": admin_worker.get("employee_contract_pdf")
            }

            new_user = UserInDB(
                full_name=user_in.full_name,
                email=user_in.email,
                phone=user_in.phone,
                hashed_password=hashed_password,
                role=RoleEnum.worker,
                is_admin_created=True,
                is_approved=True,
                approval_status="approved",
                worker_type=w_enum,
                position=admin_worker.get("position"),
                location=admin_worker.get("base_location"),
                base_location=admin_worker.get("base_location"),
                languages=admin_worker.get("languages", []),
                id_card_front=admin_worker.get("national_id_front"),
                id_card_back=admin_worker.get("national_id_back"),
                employee_contract_pdf=admin_worker.get("employee_contract_pdf"),
                onboarding_draft=draft,
                created_at=now,
                updated_at=now
            )
            # Remove pre-creation record from admin_workers collection
            await db["admin_workers"].delete_one({"_id": admin_worker["_id"]})
        else:
            # 2. Individual Worker Signup (Self signup)
            # Individual worker is locked to freelancer & requires Admin Approval
            new_user = UserInDB(
                full_name=user_in.full_name,
                email=user_in.email,
                phone=user_in.phone,
                hashed_password=hashed_password,
                role=RoleEnum.worker,
                is_admin_created=False,
                is_approved=False,
                approval_status="pending",
                worker_type=WorkerTypeEnum.freelancer,
                onboarding_draft={"worker_type": WorkerTypeEnum.freelancer.value},
                created_at=now,
                updated_at=now
            )

        created_user = await self.user_repo.create(new_user)
        
        # Automatically send verification OTP after worker signup
        await self.auth_service.generate_and_send_otp(created_user.email, subject="Welcome! Verify your email")
        
        return UserResponse(**created_user.model_dump())

    async def signup_client(self, user_in: ClientSignup) -> ClientProfileResponse:
        from app.core.database import get_database
        db = get_database()

        # 1. Check if existing user in users collection
        existing_user = await self.user_repo.get_by_email(user_in.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # 2. Check if email is in client_list collection (Admin pre-approval)
        invited_client = await db["client_list"].find_one({"email": user_in.email})
        
        hashed_password = get_password_hash(user_in.password)
        now = datetime.now(timezone.utc)

        if not invited_client:
            # Self signup - pending admin approval
            new_user = UserInDB(
                full_name=user_in.full_name,
                email=user_in.email,
                phone=user_in.phone,
                company_name=user_in.company_name,
                hashed_password=hashed_password,
                role=RoleEnum.client,
                is_active=True,
                is_verified=False,
                is_admin_created=False,
                is_approved=False,
                approval_status="pending",
                created_at=now,
                updated_at=now
            )
            created_user = await self.user_repo.create(new_user)
        else:
            # Pre-approved by Admin
            if invited_client.get("is_signup", False):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Client account has already been registered with this email invitation."
                )

            new_user = UserInDB(
                full_name=user_in.full_name,
                email=user_in.email,
                phone=user_in.phone,
                company_name=user_in.company_name,
                hashed_password=hashed_password,
                role=RoleEnum.client,
                is_active=True,
                is_verified=False,
                is_admin_created=True,
                is_approved=True,
                approval_status="approved",
                created_at=now,
                updated_at=now
            )

            created_user = await self.user_repo.create(new_user)

            await db["client_list"].update_one(
                {"_id": invited_client["_id"]},
                {
                    "$set": {
                        "is_signup": True,
                        "status": "active",
                        "primary_contact_name": user_in.full_name,
                        "phone": user_in.phone,
                        "company_name": user_in.company_name,
                        "updated_at": now
                    }
                }
            )

        # Automatically send verification OTP after client signup
        await self.auth_service.generate_and_send_otp(created_user.email, subject="Welcome! Verify your email")

        return ClientProfileResponse(**created_user.model_dump())

    async def create_admin(self, user_in: AdminCreate) -> UserResponse:
        return await self._create_user(user_in, RoleEnum.admin)
