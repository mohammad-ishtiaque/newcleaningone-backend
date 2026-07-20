from pydantic import BaseModel, Field
from typing import List

class FAQListItem(BaseModel):
    id: int = Field(alias="serial_no")
    question: str

    class Config:
        populate_by_name = True

class FAQDetailResponse(BaseModel):
    id: int = Field(alias="serial_no")
    question: str
    answer: str
    
    class Config:
        populate_by_name = True

class FAQListResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    faqs: List[FAQListItem]

class SupportMessageRequest(BaseModel):
    subject: str = Field(..., json_schema_extra={"example": "Issue with shift"})
    description: str = Field(..., json_schema_extra={"example": "I cannot view my upcoming shift details."})

class LegalDocumentResponse(BaseModel):
    title: str
    content: str
    updated_at: str
