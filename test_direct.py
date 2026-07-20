import asyncio
from app.schemas.user import WorkerSignup
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.core.database import get_database

async def main():
    repo = UserRepository()
    service = UserService(repo)
    user_in = WorkerSignup(
        full_name="Sadim Hasan Sourav",
        email="w4@yopmail.com",
        password="Secure123",
        phone="+8812345678998"
    )
    try:
        user = await service.signup_worker(user_in)
        print("Success:", user)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
