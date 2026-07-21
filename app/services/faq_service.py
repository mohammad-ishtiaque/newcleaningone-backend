import httpx
import csv
import io
from typing import Optional, List, Union
from datetime import datetime, timezone
from bson import ObjectId
from app.core.config import settings
from app.core.database import get_database

class FAQService:

    @classmethod
    async def _seed_from_sheet_if_empty(cls):
        db = get_database()
        count = await db["faqs"].count_documents({})
        if count > 0:
            return

        url = settings.FAQ_SHEET_LINK
        if not url:
            return

        if "edit?usp=sharing" in url:
            url = url.replace("edit?usp=sharing", "export?format=csv")

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()

                csv_reader = csv.DictReader(io.StringIO(response.text))
                items = []
                for row in csv_reader:
                    try:
                        serial_no = int(row.get("Serial No", 0))
                        question = row.get("FAQ Question", "").strip()
                        answer = row.get("Answer", "").strip()
                        if question and answer:
                            items.append({
                                "serial_no": serial_no,
                                "question": question,
                                "answer": answer,
                                "created_at": datetime.now(timezone.utc),
                                "updated_at": datetime.now(timezone.utc)
                            })
                    except ValueError:
                        continue

                if items:
                    await db["faqs"].insert_many(items)
        except Exception as e:
            print(f"Error seeding FAQs from Google Sheet: {e}")

    @classmethod
    async def get_all_faqs(cls) -> List[dict]:
        await cls._seed_from_sheet_if_empty()
        db = get_database()
        cursor = db["faqs"].find({}).sort("serial_no", 1)
        faqs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            faqs.append(doc)
        return faqs

    @classmethod
    async def get_faqs_list(cls, page: int = 1, limit: int = 10) -> dict:
        all_faqs = await cls.get_all_faqs()
        total = len(all_faqs)

        start = (page - 1) * limit
        end = start + limit
        paginated = all_faqs[start:end]

        return {
            "total_count": total,
            "page": page,
            "limit": limit,
            "faqs": [
                {
                    "serial_no": item["serial_no"],
                    "question": item["question"]
                }
                for item in paginated
            ]
        }

    @classmethod
    async def get_faq_detail(cls, faq_id: Union[int, str]) -> Optional[dict]:
        all_faqs = await cls.get_all_faqs()
        for faq in all_faqs:
            if str(faq.get("serial_no")) == str(faq_id) or faq.get("_id") == str(faq_id):
                return faq
        return None

    @classmethod
    async def create_faq(cls, question: str, answer: str, serial_no: Optional[int] = None) -> dict:
        db = get_database()
        if serial_no is None:
            # Get max serial_no
            last_doc = await db["faqs"].find_one({}, sort=[("serial_no", -1)])
            serial_no = (last_doc.get("serial_no", 0) + 1) if last_doc else 1

        new_faq = {
            "serial_no": serial_no,
            "question": question,
            "answer": answer,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        res = await db["faqs"].insert_one(new_faq)
        new_faq["_id"] = str(res.inserted_id)
        return new_faq

    @classmethod
    async def update_faq(cls, faq_id: str, question: Optional[str] = None, answer: Optional[str] = None, serial_no: Optional[int] = None) -> Optional[dict]:
        db = get_database()
        query = {}
        if ObjectId.is_valid(faq_id):
            query = {"_id": ObjectId(faq_id)}
        elif faq_id.isdigit():
            query = {"serial_no": int(faq_id)}
        else:
            return None

        update_fields = {"updated_at": datetime.now(timezone.utc)}
        if question is not None:
            update_fields["question"] = question
        if answer is not None:
            update_fields["answer"] = answer
        if serial_no is not None:
            update_fields["serial_no"] = serial_no

        res = await db["faqs"].find_one_and_update(
            query,
            {"$set": update_fields},
            return_document=True
        )
        if res:
            res["_id"] = str(res["_id"])
            return res
        return None

    @classmethod
    async def delete_faq(cls, faq_id: str) -> bool:
        db = get_database()
        query = {}
        if ObjectId.is_valid(faq_id):
            query = {"_id": ObjectId(faq_id)}
        elif faq_id.isdigit():
            query = {"serial_no": int(faq_id)}
        else:
            return False

        res = await db["faqs"].delete_one(query)
        return res.deleted_count > 0
