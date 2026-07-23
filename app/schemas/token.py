from pydantic import BaseModel
from typing import Optional

class Token(BaseModel):
    message: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    is_approved: Optional[bool] = None
    approval_status: Optional[str] = None

class TokenData(BaseModel):
    user_id: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str
