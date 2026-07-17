from fastapi import APIRouter, Depends, status, HTTPException
from app.schemas.user import ClientSignup, ClientUpdate, ClientProfileResponse
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum

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
