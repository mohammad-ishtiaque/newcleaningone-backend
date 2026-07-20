from pydantic import BaseModel, EmailStr, Field

class TestLogin(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "w1@yopmail.com"})
    password: str = Field(json_schema_extra={"example": "Secure123"})

import json
print(json.dumps(TestLogin.model_json_schema(), indent=2))
