import asyncio
from app.core.database import get_database

async def run():
    db = get_database()
    users = await db.users.find({"role": "client"}).to_list(100)
    for u in users: print(u.get("email"), u.get("approval_status"))

asyncio.run(run())
