from fastapi import APIRouter, Depends
from app.core.database import get_database
from app.models.user import UserInDB
from app.api.admin_users import require_admin
from app.schemas.client_approvals import ClientApprovalHistoryPaginatedResponse, ClientApprovalHistoryResponse

router = APIRouter(prefix="/admin", tags=["Cleaning One Admin API - Client Approval History"])

@router.get("/client-approvals-history", response_model=ClientApprovalHistoryPaginatedResponse, summary="View Client Approvals History")
async def get_client_approvals_history(
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_admin)
):
    db = get_database()
    total_count = await db["client_approval_history"].count_documents({})
    skip = (page - 1) * limit
    cursor = db["client_approval_history"].find({}).sort("created_at", -1).skip(skip).limit(limit)
    history_docs = await cursor.to_list(length=limit)
    
    history_list = []
    for doc in history_docs:
        doc["id"] = str(doc.get("_id"))
        history_list.append(ClientApprovalHistoryResponse(**doc))
        
    has_more = (skip + len(history_docs)) < total_count
    
    return ClientApprovalHistoryPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        history=history_list
    )
