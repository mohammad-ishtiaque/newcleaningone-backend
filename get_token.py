import asyncio
from app.core.database import get_database
from app.security.jwt import create_access_token

async def run():
    db = get_database()
    admin = await db.users.find_one({"role": "admin"})
    token = create_access_token(subject=str(admin.get("_id")))
    print(token)

asyncio.run(run())
