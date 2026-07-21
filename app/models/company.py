from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, timezone

class CompanyProfileDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    company_name: Optional[str] = "Cleaning One"
    email: Optional[EmailStr] = "admin@cleaningone.com"
    phone: Optional[str] = "+8801319414320"
    address: Optional[str] = "Mohakhali, Dhaka"
    website: Optional[str] = "www.cleaningone.com"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
