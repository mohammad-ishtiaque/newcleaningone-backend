from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List
from app.schemas.common import BasePaginatedResponse
from app.schemas.client_list import CleaningTaskResponse

class ClientRoomShortSummaryItem(BaseModel):
    id: str = ""
    room_name: str = ""
    room_type: str = "standard"
    floor: Optional[int] = 1
    duration: Optional[int] = 30
    cleaning_type: Optional[str] = "standard"
    monthly_cleaning_frequency: Optional[int] = 0
    tasks_count: int = 0
    photos_count: int = 0

    def __init__(self, **data):
        if "room_id" in data and not data.get("id"):
            data["id"] = data["room_id"]
        if "name" in data and not data.get("room_name"):
            data["room_name"] = data["name"]
        super().__init__(**data)

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "room_a366ecf17c",
                "room_name": "Executive Boardroom",
                "room_type": "conference_room",
                "floor": 2,
                "duration": 45,
                "cleaning_type": "standard",
                "monthly_cleaning_frequency": 0,
                "tasks_count": 3,
                "photos_count": 2
            }
        }
    }

class ClientLocationShortItem(BaseModel):
    id: str = ""
    name: str = ""
    type: str = "office"
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "Netherlands"
    floor: Optional[int] = 1
    total_rooms_count: int = 0
    cleaning_plans_count: int = 0
    image_url: Optional[str] = None
    is_active: bool = True
    rooms_summary: List[ClientRoomShortSummaryItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "location_id" in data and not data.get("id"):
            data["id"] = data["location_id"]
        if "location_name" in data and not data.get("name"):
            data["name"] = data["location_name"]
        super().__init__(**data)

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "loc_db28f5a3f6",
                "name": "Betopia Headquarters Tower",
                "type": "office",
                "address": "Keizersgracht 421",
                "city": "Amsterdam",
                "postal_code": "1016 EK",
                "country": "Netherlands",
                "floor": 5,
                "total_rooms_count": 8,
                "cleaning_plans_count": 2,
                "is_active": True,
                "rooms_summary": [
                    {
                        "id": "room_a366ecf17c",
                        "room_name": "Executive Boardroom",
                        "room_type": "conference_room",
                        "floor": 2,
                        "duration": 45,
                        "tasks_count": 3,
                        "photos_count": 2
                    }
                ],
                "created_at": "2026-08-24T10:00:00Z",
                "updated_at": "2026-08-24T10:00:00Z"
            }
        }
    }

class ClientLocationMonitoringPaginatedResponse(BasePaginatedResponse):
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    locations: List[ClientLocationShortItem] = Field(default_factory=list)

class ClientRoomDetailItem(BaseModel):
    id: str = ""
    room_name: str = ""
    room_type: str = "standard"
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    floor: Optional[int] = 1
    duration: Optional[int] = 30
    cleaning_type: Optional[str] = "standard"
    monthly_cleaning_frequency: Optional[int] = 0
    tasks_count: int = 0
    photos_count: int = 0
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "room_id" in data and not data.get("id"):
            data["id"] = data["room_id"]
        if "name" in data and not data.get("room_name"):
            data["room_name"] = data["name"]
        super().__init__(**data)

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "room_a366ecf17c",
                "room_name": "Executive Boardroom",
                "room_type": "conference_room",
                "location_id": "loc_db28f5a3f6",
                "location_name": "Betopia Headquarters Tower",
                "floor": 2,
                "duration": 45,
                "cleaning_type": "standard",
                "monthly_cleaning_frequency": 0,
                "tasks_count": 2,
                "photos_count": 2,
                "tasks": [
                    {
                        "id": "t_01",
                        "name": "Vacuum carpet",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "photo": [{"id": "p_01", "name": "After vacuum photo"}],
                        "total_photos_required": 1
                    }
                ],
                "created_at": "2026-08-24T10:00:00Z",
                "updated_at": "2026-08-24T10:00:00Z"
            }
        }
    }

class ClientLocationDetailResponse(BaseModel):
    id: str = ""
    name: str = ""
    type: str = "office"
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "Netherlands"
    floor: Optional[int] = 1
    description: Optional[str] = None
    notes: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool = True
    total_rooms_count: int = 0
    cleaning_plans_count: int = 0
    client_id: str = ""
    company_name: str = ""
    rooms: List[ClientRoomDetailItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "location_id" in data and not data.get("id"):
            data["id"] = data["location_id"]
        if "location_name" in data and not data.get("name"):
            data["name"] = data["location_name"]
        super().__init__(**data)

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "loc_db28f5a3f6",
                "name": "Betopia Headquarters Tower",
                "type": "office",
                "address": "Keizersgracht 421",
                "city": "Amsterdam",
                "postal_code": "1016 EK",
                "country": "Netherlands",
                "floor": 5,
                "description": "Primary corporate office tower",
                "total_rooms_count": 8,
                "cleaning_plans_count": 2,
                "client_id": "client_123",
                "company_name": "Betopia Corp",
                "is_active": True,
                "rooms": [],
                "created_at": "2026-08-24T10:00:00Z",
                "updated_at": "2026-08-24T10:00:00Z"
            }
        }
    }

class ClientLocationRoomsPaginatedResponse(BasePaginatedResponse):
    location_id: str
    location_name: str
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    rooms: List[ClientRoomDetailItem] = Field(default_factory=list)

class ClientRoomFullDetailResponse(BaseModel):
    id: str = ""
    room_name: str = ""
    room_type: str = "standard"
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    client_id: Optional[str] = None
    company_name: Optional[str] = None
    floor: Optional[int] = 1
    duration: Optional[int] = 30
    cleaning_type: Optional[str] = "standard"
    monthly_cleaning_frequency: Optional[int] = 0
    photo_number: int = 0
    task_number: int = 0
    tasks: List[CleaningTaskResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def __init__(self, **data):
        if "room_id" in data and not data.get("id"):
            data["id"] = data["room_id"]
        if "name" in data and not data.get("room_name"):
            data["room_name"] = data["name"]
        super().__init__(**data)

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "room_a366ecf17c",
                "room_name": "Executive Boardroom",
                "room_type": "conference_room",
                "location_id": "loc_db28f5a3f6",
                "location_name": "Betopia Headquarters Tower",
                "client_id": "client_123",
                "company_name": "Betopia Corp",
                "floor": 2,
                "duration": 45,
                "cleaning_type": "standard",
                "monthly_cleaning_frequency": 0,
                "photo_number": 2,
                "task_number": 2,
                "tasks": [
                    {
                        "id": "t_01",
                        "name": "Vacuum carpet",
                        "frequency_type": "every_visit",
                        "is_photo_req": True,
                        "photo": [{"id": "p_01", "name": "After vacuum"}],
                        "total_photos_required": 1
                    }
                ],
                "created_at": "2026-08-24T10:00:00Z",
                "updated_at": "2026-08-24T10:00:00Z"
            }
        }
    }
