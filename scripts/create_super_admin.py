import asyncio
import sys
import os

# Add the root project directory to the python path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import client, get_database
from app.repositories.user_repo import UserRepository
from app.models.user import UserInDB, RoleEnum
from app.security.password import get_password_hash
from datetime import datetime, timezone

async def create_super_admin(email: str, password: str, full_name: str = "Super Admin"):
    print("Checking for existing super admin...")
    user_repo = UserRepository()
    
    # Check if a super admin already exists
    existing_super_admin = await user_repo.collection.find_one({"role": RoleEnum.super_admin.value})
    if existing_super_admin:
        print("A super admin already exists. Aborting.")
        return

    # Check if email is already taken
    existing_user = await user_repo.get_by_email(email)
    if existing_user:
        print(f"Error: Email {email} is already taken by another user.")
        return

    hashed_password = get_password_hash(password)
    new_user = UserInDB(
        full_name=full_name,
        email=email,
        hashed_password=hashed_password,
        role=RoleEnum.super_admin,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    
    await user_repo.create(new_user)
    print(f"Success! Super Admin '{email}' created.")

async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/create_super_admin.py <email> <password> [full_name]")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]
    full_name = sys.argv[3] if len(sys.argv) > 3 else "Super Admin"

    try:
        await create_super_admin(email, password, full_name)
    finally:
        from app.core import database
        if database.client:
            database.client.close()

if __name__ == "__main__":
    asyncio.run(main())
