from fastapi import APIRouter, Depends, status, HTTPException
from typing import List
from bson import ObjectId
from app.schemas.user import AdminCreate, AdminUpdate, AdminProfileResponse, UserResponse
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.dependencies.rbac import RequireRole
from app.dependencies.auth import get_current_user
from app.models.user import RoleEnum, UserInDB
from app.security.password import get_password_hash

router = APIRouter(prefix="/admin", tags=["Admin"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.admin, RoleEnum.super_admin]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user

# ================================
# My Profile (Admin/SuperAdmin)
# ================================

@router.get("/me", response_model=AdminProfileResponse)
async def get_my_admin_profile(current_user: UserInDB = Depends(require_admin)):
    return AdminProfileResponse(**current_user.model_dump(by_alias=True))

@router.patch("/me", response_model=AdminProfileResponse)
async def update_my_admin_profile(
    admin_update: AdminUpdate,
    current_user: UserInDB = Depends(require_admin),
    user_repo: UserRepository = Depends(UserRepository)
):
    update_data = admin_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
        
    await user_repo.update(current_user)
    return AdminProfileResponse(**current_user.model_dump(by_alias=True))

# ================================
# CRUD for Admins (SuperAdmin only)
# ================================

@router.post(
    "/users", 
    response_model=UserResponse, 
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def create_admin(
    user_in: AdminCreate,
    user_service: UserService = Depends(get_user_service)
):
    return await user_service.create_admin(user_in)

@router.get(
    "/users", 
    response_model=List[AdminProfileResponse],
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def list_admins(user_repo: UserRepository = Depends(UserRepository)):
    cursor = user_repo.collection.find({"role": RoleEnum.admin.value})
    admins = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        admins.append(AdminProfileResponse(**doc))
    return admins

@router.get(
    "/users/{admin_id}", 
    response_model=AdminProfileResponse,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def get_admin(admin_id: str, user_repo: UserRepository = Depends(UserRepository)):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    return AdminProfileResponse(**user.model_dump(by_alias=True))

@router.patch(
    "/users/{admin_id}", 
    response_model=AdminProfileResponse,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def update_admin(
    admin_id: str, 
    admin_update: AdminUpdate, 
    user_repo: UserRepository = Depends(UserRepository)
):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
        
    update_data = admin_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
        
    await user_repo.update(user)
    return AdminProfileResponse(**user.model_dump(by_alias=True))

@router.delete(
    "/users/{admin_id}", 
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireRole([RoleEnum.super_admin]))]
)
async def delete_admin(admin_id: str, user_repo: UserRepository = Depends(UserRepository)):
    user = await user_repo.get_by_id(admin_id)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin not found")
        
    await user_repo.collection.delete_one({"_id": ObjectId(admin_id)})
    return None
