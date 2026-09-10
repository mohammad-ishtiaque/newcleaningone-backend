from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from app.schemas.common import BasePaginatedResponse


class SuggestedQuestionCreate(BaseModel):
    question: str = Field(..., json_schema_extra={"example": "How do I request time off?"})
    answer: str = Field(..., json_schema_extra={"example": "Go to Availability in your profile and submit a leave request."})
    # Which chat surface sees this suggestion. "all" shows it to both worker and client chat.
    target_role: Literal["worker", "client", "all"] = Field(default="all", json_schema_extra={"example": "worker"})
    is_active: bool = True


class SuggestedQuestionUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    target_role: Optional[Literal["worker", "client", "all"]] = None
    is_active: Optional[bool] = None


class SuggestedQuestionResponse(BaseModel):
    id: str
    question: str
    answer: str
    target_role: str
    is_active: bool
    created_by_manager_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SuggestedQuestionPaginatedResponse(BasePaginatedResponse):
    questions: List[SuggestedQuestionResponse] = Field(default_factory=list)
