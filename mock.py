from app.schemas.user import ClientProfileResponse
from datetime import datetime, timezone
print(ClientProfileResponse(full_name="a", email="a@b.com", role="client", is_active=True, is_verified=False, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), company_name="Test Company").model_dump_json())
