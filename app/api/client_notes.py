import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.common import BasePaginatedResponse

router = APIRouter(prefix="/client/notes", tags=["Client Special Notes Management"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.client, "client"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


class ClientNoteCreate(BaseModel):
    title: str = Field(..., json_schema_extra={"example": "Special Key Card Instructions"})
    content: str = Field(..., json_schema_extra={"example": "Keycard is inside lockbox #4 next to reception desk. Code: 4891."})
    location_id: Optional[str] = Field(None, json_schema_extra={"example": "loc_0510d4c547"})
    category: Optional[str] = Field("general", json_schema_extra={"example": "access_instruction"})
    is_pinned: bool = False

class ClientNoteResponse(BaseModel):
    id: str
    client_id: str
    title: str
    content: str
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    category: str = "general"
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime

class ClientNotesPaginatedResponse(BasePaginatedResponse):
    total_count: int
    page: int
    limit: int
    has_more: bool = False
    notes: List[ClientNoteResponse] = Field(default_factory=list)


@router.get(
    "",
    response_model=ClientNotesPaginatedResponse,
    summary="Get Client Notes & Special Instructions (Paginated)",
    description="Returns all special cleaning instructions, access codes, and notes created by the client for their locations."
)
async def get_client_notes(
    location_id: Optional[str] = Query(None, description="Filter notes by specific location ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    query = {"client_id": client_id}
    if location_id:
        query["location_id"] = location_id

    total_count = await db["client_notes"].count_documents(query)
    cursor = db["client_notes"].find(query).sort([("is_pinned", -1), ("created_at", -1)]).skip((page - 1) * limit).limit(limit)
    raw_notes = await cursor.to_list(length=limit)

    notes = []
    for n in raw_notes:
        nid = str(n.get("id") or n.get("_id"))
        c_at = n.get("created_at") if isinstance(n.get("created_at"), datetime) else datetime.now(timezone.utc)
        u_at = n.get("updated_at") if isinstance(n.get("updated_at"), datetime) else datetime.now(timezone.utc)
        notes.append(ClientNoteResponse(
            id=nid,
            client_id=client_id,
            title=n.get("title", ""),
            content=n.get("content", ""),
            location_id=n.get("location_id"),
            location_name=n.get("location_name"),
            category=n.get("category", "general"),
            is_pinned=bool(n.get("is_pinned", False)),
            created_at=c_at,
            updated_at=u_at
        ))

    has_more = (page * limit) < total_count

    return ClientNotesPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        notes=notes
    )


@router.post(
    "",
    response_model=ClientNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Client Special Note",
    description="Creates a new special cleaning instruction or access note for cleaners and managers."
)
async def create_client_note(
    note_in: ClientNoteCreate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    now = datetime.now(timezone.utc)
    note_id = f"note_{uuid.uuid4().hex[:10]}"

    loc_name = None
    if note_in.location_id:
        loc = await db["locations"].find_one({"$or": [{"_id": note_in.location_id}, {"id": note_in.location_id}]})
        if loc:
            loc_name = loc.get("name")

    doc = {
        "_id": note_id,
        "id": note_id,
        "client_id": client_id,
        "title": note_in.title,
        "content": note_in.content,
        "location_id": note_in.location_id,
        "location_name": loc_name,
        "category": note_in.category or "general",
        "is_pinned": note_in.is_pinned,
        "created_at": now,
        "updated_at": now
    }

    await db["client_notes"].insert_one(doc)

    return ClientNoteResponse(
        id=note_id,
        client_id=client_id,
        title=note_in.title,
        content=note_in.content,
        location_id=note_in.location_id,
        location_name=loc_name,
        category=note_in.category or "general",
        is_pinned=note_in.is_pinned,
        created_at=now,
        updated_at=now
    )


@router.delete(
    "/{note_id}",
    summary="Delete Client Note",
    description="Deletes a specific note created by the logged-in client."
)
async def delete_client_note(
    note_id: str,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    client_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    result = await db["client_notes"].delete_one({
        "$or": [{"_id": note_id}, {"id": note_id}],
        "client_id": client_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"message": "Note deleted successfully"}
