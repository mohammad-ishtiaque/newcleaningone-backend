import httpx
import csv
import io
import time
from app.core.config import settings
from app.schemas.help import FAQListItem, FAQDetailResponse, FAQListResponse

class FAQService:
    _cache = []
    _cache_time = 0
    _CACHE_TTL = 300 # 5 minutes

    @classmethod
    async def _fetch_all_faqs(cls):
        if cls._cache and (time.time() - cls._cache_time) < cls._CACHE_TTL:
            return cls._cache

        url = settings.FAQ_SHEET_LINK
        if not url:
            return []
            
        if "edit?usp=sharing" in url:
            url = url.replace("edit?usp=sharing", "export?format=csv")
            
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                
                csv_reader = csv.DictReader(io.StringIO(response.text))
                faqs = []
                for row in csv_reader:
                    try:
                        faq_item = {
                            "serial_no": int(row.get("Serial No", 0)),
                            "question": row.get("FAQ Question", ""),
                            "answer": row.get("Answer", "")
                        }
                        faqs.append(faq_item)
                    except ValueError:
                        continue
                        
                cls._cache = faqs
                cls._cache_time = time.time()
                return faqs
        except Exception as e:
            print(f"Error fetching FAQs: {e}")
            return cls._cache

    @classmethod
    async def get_faqs_list(cls, page: int = 1, limit: int = 10) -> dict:
        all_faqs = await cls._fetch_all_faqs()
        total = len(all_faqs)
        
        start = (page - 1) * limit
        end = start + limit
        paginated = all_faqs[start:end]
        
        return {
            "total_count": total,
            "page": page,
            "limit": limit,
            "faqs": paginated
        }

    @classmethod
    async def get_faq_detail(cls, faq_id: int) -> dict:
        all_faqs = await cls._fetch_all_faqs()
        for faq in all_faqs:
            if faq["serial_no"] == faq_id:
                return faq
        return None
