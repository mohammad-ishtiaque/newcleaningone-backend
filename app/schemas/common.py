from pydantic import BaseModel, model_validator
from typing import Any

class BasePaginatedResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    has_more: bool = False

    @model_validator(mode="before")
    @classmethod
    def calculate_has_more(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "has_more" not in data or data.get("has_more") is None:
                tc = data.get("total_count", 0)
                p = data.get("page", 1)
                lim = data.get("limit", 10)
                data["has_more"] = bool((p * lim) < tc)
        elif hasattr(data, "__dict__"):
            tc = getattr(data, "total_count", 0)
            p = getattr(data, "page", 1)
            lim = getattr(data, "limit", 10)
            if getattr(data, "has_more", None) is None:
                try:
                    data.has_more = bool((p * lim) < tc)
                except Exception:
                    pass
        return data
