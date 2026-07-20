from app.schemas.user import LoginRequest
import json

print(json.dumps(LoginRequest.model_json_schema(), indent=2))
