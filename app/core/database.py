from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

client = None
db = None

def get_database():
    global client, db
    if client is None:
        client = AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_DB_NAME]
    return db
