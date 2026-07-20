from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.models.user import UserInDB

class UserRepository:
    def __init__(self):
        pass

    @property
    def collection(self):
        return get_database()["users"]

    async def get_by_email(self, email: str) -> Optional[UserInDB]:
        user_doc = await self.collection.find_one({"email": email})
        if user_doc:
            # Convert ObjectId to string for Pydantic
            user_doc["_id"] = str(user_doc["_id"])
            return UserInDB(**user_doc)
        return None

    async def get_by_id(self, user_id: str) -> Optional[UserInDB]:
        try:
            user_doc = await self.collection.find_one({"_id": ObjectId(user_id)})
            if user_doc:
                user_doc["_id"] = str(user_doc["_id"])
                return UserInDB(**user_doc)
            return None
        except Exception:
            return None

    async def create(self, user: UserInDB) -> UserInDB:
        user_dict = user.model_dump(by_alias=True, exclude={"id"})
        result = await self.collection.insert_one(user_dict)
        user_dict["_id"] = str(result.inserted_id)
        return UserInDB(**user_dict)
    
    async def update(self, user: UserInDB) -> UserInDB:
        user_dict = user.model_dump(by_alias=True, exclude={"id"})
        await self.collection.update_one(
            {"_id": ObjectId(user.id)},
            {"$set": user_dict}
        )
        return user

    async def get_next_employee_sequence(self) -> int:
        from pymongo import ReturnDocument
        db = get_database()
        result = await db["counters"].find_one_and_update(
            {"_id": "employee_id"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return result["seq"]
