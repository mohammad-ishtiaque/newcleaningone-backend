import asyncio
from app.core.database import get_database

async def run():
    db = get_database()
    user = await db.users.find_one({"email": "c5@yopmail.com"})
    print(user.get("otp_code"))

asyncio.run(run())
