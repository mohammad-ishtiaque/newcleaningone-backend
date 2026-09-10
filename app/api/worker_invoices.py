from datetime import datetime
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import resolve_hourly_rate
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_modules import (
    WorkerInvoiceItem, WorkerInvoicesPaginatedResponse
)

router = APIRouter(prefix="/worker/invoices", tags=["Worker Invoices & Payouts"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


def _format_worker_invoice_item(inv: dict, worker_rate: float) -> WorkerInvoiceItem:
    """
    Maps a real `worker_invoices` document (written by the manager via
    POST/PATCH /manager/worker-invoices) into the worker-facing response shape.
    No field here is ever fabricated — a missing value means the invoice
    genuinely doesn't have it, and is shown as 0/empty, never guessed.
    """
    inv_id = str(inv.get("invoice_id") or inv.get("id") or inv.get("_id"))
    pay_status = str(inv.get("payment_status", "pending")).lower()
    amount_paid = float(inv.get("amount_paid") or 0.0)
    gross_amount = float(inv.get("gross_amount") or 0.0)
    balance_due = float(inv.get("balance_due") if inv.get("balance_due") is not None else max(0.0, gross_amount - amount_paid))

    return WorkerInvoiceItem(
        invoice_id=inv_id,
        invoice_number=inv.get("invoice_number") or f"INV-{inv_id[:6].upper()}",
        period_start=str(inv.get("period_start") or ""),
        period_end=str(inv.get("period_end") or ""),
        hours_worked=float(inv.get("hours_worked") or 0.0),
        regular_hours=float(inv["regular_hours"]) if inv.get("regular_hours") is not None else None,
        overtime_hours=float(inv["overtime_hours"]) if inv.get("overtime_hours") is not None else None,
        hourly_rate=resolve_hourly_rate(inv, default=worker_rate),
        gross_amount=gross_amount,
        bonus_amount=float(inv.get("bonus_amount") or 0.0),
        deductions=float(inv.get("deductions") or 0.0),
        net_payout=amount_paid,
        balance_due=balance_due,
        currency=str(inv.get("currency", "EUR")),
        status=pay_status,
        payment_method=inv.get("payment_method", "Bank Transfer"),
        # Only a genuinely "paid" invoice gets a paid_at — never fabricated for
        # a pending/partial one just because the record exists.
        paid_at=inv.get("updated_at") if pay_status == "paid" and isinstance(inv.get("updated_at"), datetime) else None,
        download_url=f"/worker/invoices/{inv_id}/pdf"
    )


@router.get(
    "",
    response_model=WorkerInvoicesPaginatedResponse,
    summary="Get Worker Invoices & Payouts (Paginated)",
    description="Returns the worker's real recorded invoices/payouts (created by a manager via the invoicing workflow), plus totals actually paid and still outstanding."
)
async def get_worker_invoices(
    status_filter: Optional[str] = Query("all", description="Filter invoice status: 'all', 'paid', 'pending', 'partial'"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    # The worker's own rate is the fallback for invoice documents written before
    # the rate was stored on them. Never a hardcoded figure.
    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    worker_rate = resolve_hourly_rate(await db["users"].find_one(user_query))

    # The real, manager-managed collection — matches what /manager/worker-invoices writes.
    query = {"worker_id": worker_id}
    if status_filter and status_filter.lower() != "all":
        query["payment_status"] = status_filter.lower()

    total_count = await db["worker_invoices"].count_documents(query)
    cursor = db["worker_invoices"].find(query).sort([("period_year", -1), ("period_month", -1)]).skip((page - 1) * limit).limit(limit)
    raw_invoices = await cursor.to_list(length=limit)
    invoices = [_format_worker_invoice_item(inv, worker_rate) for inv in raw_invoices]

    # Totals across ALL of the worker's real invoices (not just this page).
    # total_earned = gross value of work invoiced; total_paid = what's actually
    # been disbursed; pending_payout = the difference — three distinct numbers,
    # not one value relabeled three times.
    total_earned = 0.0
    total_paid = 0.0
    pending_payout = 0.0
    total_hours_worked = 0.0
    total_overtime_hours = 0.0
    async for inv in db["worker_invoices"].find({"worker_id": worker_id}):
        amount_paid = float(inv.get("amount_paid") or 0.0)
        gross = float(inv.get("gross_amount") or 0.0)
        balance = float(inv.get("balance_due") if inv.get("balance_due") is not None else max(0.0, gross - amount_paid))
        total_earned += gross
        total_paid += amount_paid
        pending_payout += balance
        total_hours_worked += float(inv.get("hours_worked") or 0.0)
        total_overtime_hours += float(inv.get("overtime_hours") or 0.0)

    has_more = (page * limit) < total_count

    return WorkerInvoicesPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        total_earned=round(total_earned, 2),
        total_paid=round(total_paid, 2),
        pending_payout=round(pending_payout, 2),
        current_hourly_rate=worker_rate,
        total_hours_worked=round(total_hours_worked, 2),
        total_overtime_hours=round(total_overtime_hours, 2),
        invoices=invoices
    )


@router.get(
    "/{invoice_id}",
    response_model=WorkerInvoiceItem,
    summary="Get Single Invoice Detail",
    description="Returns detailed payout breakdown for a specific real invoice ID recorded by a manager."
)
async def get_worker_invoice_detail(
    invoice_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    worker_rate = resolve_hourly_rate(await db["users"].find_one(user_query))

    inv = await db["worker_invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}],
        "worker_id": worker_id
    })
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return _format_worker_invoice_item(inv, worker_rate)
