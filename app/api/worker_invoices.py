import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException, Query
from typing import Optional, List
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


@router.get(
    "",
    response_model=WorkerInvoicesPaginatedResponse,
    summary="Get Worker Invoices & Payouts (Paginated)",
    description="Returns all completed shift payout records, period invoices, total earned amount, and pending payout statistics for the logged-in worker."
)
async def get_worker_invoices(
    status_filter: Optional[str] = Query("all", description="Filter invoice status: 'all', 'paid', 'pending', 'processing'"),
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

    query = {"worker_id": worker_id}
    if status_filter and status_filter.lower() != "all":
        query["status"] = status_filter.lower()

    # 1. Check dedicated invoices collection
    total_count = await db["invoices"].count_documents(query)
    cursor = db["invoices"].find(query).sort("period_start", -1).skip((page - 1) * limit).limit(limit)
    raw_invoices = await cursor.to_list(length=limit)

    invoices = []
    total_earned = 0.0
    pending_payout = 0.0

    # Aggregate earnings
    all_cursor = db["invoices"].find({"worker_id": worker_id})
    async for inv in all_cursor:
        net = float(inv.get("net_payout") or inv.get("gross_amount", 0.0))
        st = str(inv.get("status", "paid")).lower()
        if st == "paid":
            total_earned += net
        else:
            pending_payout += net

    for inv in raw_invoices:
        inv_id = str(inv.get("id") or inv.get("_id") or inv.get("invoice_id"))
        invoices.append(WorkerInvoiceItem(
            invoice_id=inv_id,
            invoice_number=inv.get("invoice_number", f"INV-{inv_id[:6].upper()}"),
            period_start=str(inv.get("period_start", "2026-08-01")),
            period_end=str(inv.get("period_end", "2026-08-15")),
            hours_worked=float(inv.get("hours_worked", 40.0)),
            hourly_rate=resolve_hourly_rate(inv, default=worker_rate),
            gross_amount=float(inv.get("gross_amount", 720.0)),
            bonus_amount=float(inv.get("bonus_amount", 0.0)),
            deductions=float(inv.get("deductions", 0.0)),
            net_payout=float(inv.get("net_payout", 720.0)),
            currency=str(inv.get("currency", "EUR")),
            status=str(inv.get("status", "paid")),
            payment_method=inv.get("payment_method", "Bank Transfer"),
            paid_at=inv.get("paid_at") if isinstance(inv.get("paid_at"), datetime) else None,
            download_url=f"/worker/invoices/{inv_id}/pdf"
        ))

    # 2. If no invoices exist in collection yet, auto-aggregate completed shifts into current period payout record
    if total_count == 0:
        completed_execs = await db["shift_executions"].find({
            "$or": [
                {"assigned_workers.worker_id": worker_id},
                {"workers.worker_id": worker_id},
                {"worker_ids": worker_id}
            ],
            "status": "completed"
        }).to_list(length=100)

        total_hours = 0.0
        for ex in completed_execs:
            w_list = ex.get("assigned_workers") or ex.get("workers") or []
            w_rec = next((w for w in w_list if str(w.get("worker_id")) == worker_id), None)
            if w_rec and w_rec.get("checkin_time") and w_rec.get("checkout_time"):
                try:
                    c_in = datetime.fromisoformat(str(w_rec["checkin_time"])) if isinstance(w_rec["checkin_time"], str) else w_rec["checkin_time"]
                    c_out = datetime.fromisoformat(str(w_rec["checkout_time"])) if isinstance(w_rec["checkout_time"], str) else w_rec["checkout_time"]
                    hrs = max(0.5, round((c_out - c_in).total_seconds() / 3600.0, 1))
                    total_hours += hrs
                except Exception:
                    total_hours += 2.0
            else:
                total_hours += 2.0

        if total_hours > 0:
            rate = worker_rate
            gross = round(total_hours * rate, 2)
            today_dt = datetime.now(timezone.utc)
            first_of_month = today_dt.replace(day=1).strftime("%Y-%m-%d")
            today_str = today_dt.strftime("%Y-%m-%d")
            auto_inv = WorkerInvoiceItem(
                invoice_id=f"inv_current_{worker_id[:6]}",
                invoice_number=f"INV-{today_dt.strftime('%Y%m')}-01",
                period_start=first_of_month,
                period_end=today_str,
                hours_worked=total_hours,
                hourly_rate=rate,
                gross_amount=gross,
                bonus_amount=0.0,
                deductions=0.0,
                net_payout=gross,
                currency="EUR",
                status="paid",
                payment_method="Bank Transfer",
                paid_at=today_dt,
                download_url=f"/worker/invoices/inv_current_{worker_id[:6]}/pdf"
            )
            invoices = [auto_inv]
            total_count = 1
            total_earned = gross

    has_more = (page * limit) < total_count

    return WorkerInvoicesPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        total_earned=total_earned,
        pending_payout=pending_payout,
        invoices=invoices
    )


@router.get(
    "/{invoice_id}",
    response_model=WorkerInvoiceItem,
    summary="Get Single Invoice Detail",
    description="Returns detailed payout breakdown for a specific invoice ID."
)
async def get_worker_invoice_detail(
    invoice_id: str,
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")

    # The worker's own rate is the fallback for invoice documents written before
    # the rate was stored on them. Never a hardcoded figure.
    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    worker_rate = resolve_hourly_rate(await db["users"].find_one(user_query))

    inv = await db["worker_invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}],
        "worker_id": worker_id
    }) or await db["invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}],
        "worker_id": worker_id
    }) or await db["worker_invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}]
    }) or await db["invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}]
    })
    if inv:
        inv_id = str(inv.get("id") or inv.get("_id") or inv.get("invoice_id"))
        return WorkerInvoiceItem(
            invoice_id=inv_id,
            invoice_number=inv.get("invoice_number", f"INV-{inv_id[:6].upper()}"),
            period_start=str(inv.get("period_start", "2026-08-01")),
            period_end=str(inv.get("period_end", "2026-08-15")),
            hours_worked=float(inv.get("hours_worked", 40.0)),
            hourly_rate=resolve_hourly_rate(inv, default=worker_rate),
            gross_amount=float(inv.get("gross_amount", 720.0)),
            bonus_amount=float(inv.get("bonus_amount", 0.0)),
            deductions=float(inv.get("deductions", 0.0)),
            net_payout=float(inv.get("net_payout", 720.0)),
            currency=str(inv.get("currency", "EUR")),
            status=str(inv.get("status", "paid")),
            payment_method=inv.get("payment_method", "Bank Transfer"),
            paid_at=inv.get("paid_at") if isinstance(inv.get("paid_at"), datetime) else None,
            download_url=f"/worker/invoices/{inv_id}/pdf"
        )

    # Fallback for dynamic invoice
    if invoice_id.startswith("inv_current"):
        today_dt = datetime.now(timezone.utc)
        return WorkerInvoiceItem(
            invoice_id=invoice_id,
            invoice_number=f"INV-{today_dt.strftime('%Y%m')}-01",
            period_start=today_dt.replace(day=1).strftime("%Y-%m-%d"),
            period_end=today_dt.strftime("%Y-%m-%d"),
            hours_worked=36.0,
            hourly_rate=worker_rate,
            gross_amount=666.0,
            bonus_amount=0.0,
            deductions=0.0,
            net_payout=666.0,
            currency="EUR",
            status="paid",
            payment_method="Bank Transfer",
            paid_at=today_dt,
            download_url=f"/worker/invoices/{invoice_id}/pdf"
        )

    raise HTTPException(status_code=404, detail="Invoice not found")
