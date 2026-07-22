from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from bson import ObjectId

class ClientListDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    admin_name: str
    company_name: str
    industry: str
    status: str = "pending"
    primary_contact_name: str
    email: EmailStr
    phone: str
    is_signup: bool = False
    locations: List[Dict[str, Any]] = Field(default_factory=list)
    contacts: List[Dict[str, Any]] = Field(default_factory=list)
    contracts: List[Dict[str, Any]] = Field(default_factory=list)
    cleaning_plans: List[Dict[str, Any]] = Field(default_factory=list)
    reports: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
