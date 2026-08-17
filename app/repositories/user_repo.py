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
            if ObjectId.is_valid(user_id):
                user_doc = await self.collection.find_one({"$or": [{"_id": ObjectId(user_id)}, {"_id": user_id}, {"id": user_id}]})
            else:
                user_doc = await self.collection.find_one({"$or": [{"_id": user_id}, {"id": user_id}]})
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
        uid = str(user.id)
        if ObjectId.is_valid(uid):
            query = {"$or": [{"_id": ObjectId(uid)}, {"_id": uid}, {"id": uid}]}
        else:
            query = {"$or": [{"_id": uid}, {"id": uid}]}
        await self.collection.update_one(query, {"$set": user_dict})
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
