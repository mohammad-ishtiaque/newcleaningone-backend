from fastapi import Depends, HTTPException, status
from typing import List
from app.models.user import UserInDB, RoleEnum
from app.dependencies.auth import get_current_user

class RequireRole:
    def __init__(self, allowed_roles: List[RoleEnum]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action"
            )
        return current_user

async def ensure_profile_completed(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role == RoleEnum.worker and not current_user.is_profile_completed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must complete your profile onboarding before accessing this resource"
        )
    return current_user
