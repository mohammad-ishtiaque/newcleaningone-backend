from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

class SupportMessageDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    worker_id: str
    subject: str
    description: str
    status: str = "pending" # pending, resolved
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
