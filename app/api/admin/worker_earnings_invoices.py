import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Optional, List, Literal
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import rate_for_shift_record, resolve_hourly_rate
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.api.admin.profile_company import require_manager
from app.schemas.worker_modules import (
    WorkerShiftEarningItem, WorkerInvoiceItem,
    ManagerWorkerMonthlyEarningsResponse,
    ManagerWorkerEarningsOverviewItem, ManagerWorkerEarningsPaginatedResponse,
    ManagerWorkerInvoiceCreate, ManagerWorkerInvoiceUpdate,
    ManagerWorkerInvoiceDetailResponse, ManagerWorkerInvoicePaginatedResponse
)
from app.api.worker_shift_utils import (
    calculate_rounded_work_hours, evaluate_shift_overtime
)

worker_earnings_invoices_router = APIRouter(prefix="/manager", tags=["Manager Worker Management"])


# ============================================================================
# 1. Get Specific Worker Monthly Earnings
# ============================================================================
@worker_earnings_invoices_router.get(
    "/workers/{worker_id}/earnings",
    response_model=ManagerWorkerMonthlyEarningsResponse,
    summary="Get Worker Monthly Earnings Breakdown (Manager)",
    description="""
### Get Worker Monthly Earnings Breakdown
Returns complete breakdown of shifts, hours worked (with 30-minute block rounding), regular hours, overtime hours, gross earnings, total paid, and balance due for a given worker and month.
"""
)
async def get_worker_monthly_earnings(
    worker_id: str,
    month: Optional[int] = Query(None, ge=1, le=12, description="Month (1-12, defaults to current month)"),
    year: Optional[int] = Query(None, ge=2020, description="Year (defaults to current year)"),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    target_month = month or now.month

    # Resolve worker user
    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one({"$and": [user_query, {"role": "worker"}]})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Worker not found")

    w_uid = str(user_doc["_id"])
    w_name = user_doc.get("full_name") or user_doc.get("name") or "Worker"
    w_email = user_doc.get("email")
    w_phone = user_doc.get("phone")
    w_type = user_doc.get("worker_type") or "employee"
    w_pos = user_doc.get("position") or "Cleaner"
    hourly_r = resolve_hourly_rate(user_doc)

    month_str = f"{target_year:04d}-{target_month:02d}"
    month_label = datetime(target_year, target_month, 1).strftime("%B %Y")

    # Query shift_executions and direct shifts for this month
    shift_query = {
        "date": {"$regex": f"^{month_str}"},
        "status": {"$ne": "cancelled"},
        "$or": [
            {"assigned_workers.worker_id": w_uid},
            {"workers.worker_id": w_uid},
            {"worker_ids": w_uid}
        ]
    }

    execs = await db["shift_executions"].find(shift_query).sort("date", 1).to_list(length=300)
    direct_shifts = await db["shifts"].find(shift_query).sort("date", 1).to_list(length=300)
    all_shifts = execs + direct_shifts

    shifts_list: List[WorkerShiftEarningItem] = []
    seen_ids = set()

    for s in all_shifts:
        s_id = str(s.get("id") or s.get("_id"))
        if s_id in seen_ids:
            continue
        seen_ids.add(s_id)

        w_list = s.get("assigned_workers") or s.get("workers") or []
        w_rec = next((w for w in w_list if str(w.get("worker_id") or w.get("id")) == w_uid), None)
        if not w_rec:
            continue

        c_time = w_rec.get("checkin_time")
        co_time = w_rec.get("checkout_time")
        s_date = str(s.get("date") or month_str + "-01")

        if co_time and c_time:
            c_dt = c_time if isinstance(c_time, datetime) else datetime.fromisoformat(str(c_time))
            co_dt = co_time if isinstance(co_time, datetime) else datetime.fromisoformat(str(co_time))
            if c_dt.tzinfo is None:
                c_dt = c_dt.replace(tzinfo=timezone.utc)
            if co_dt.tzinfo is None:
                co_dt = co_dt.replace(tzinfo=timezone.utc)

            dur_sec = max(0.0, (co_dt - c_dt).total_seconds())
            rounded_hw, raw_hw, _ = calculate_rounded_work_hours(dur_sec)
        else:
            hw_stored = float(w_rec.get("hours_worked") or 0.0)
            rounded_hw = hw_stored
            raw_hw = hw_stored
            c_dt = c_time if isinstance(c_time, datetime) else None
            co_dt = co_time if isinstance(co_time, datetime) else None

        sched_mins = float(s.get("duration_minutes") or 0.0)
        reg_hw, ot_hw = evaluate_shift_overtime(rounded_hw, sched_mins)
        earned = round(rounded_hw * hourly_r, 2)

        shifts_list.append(WorkerShiftEarningItem(
            shift_id=s_id,
            title=s.get("title") or s.get("plan_name") or "Cleaning Shift",
            location_name=s.get("location_name") or "Amsterdam Location",
            date=s_date,
            start_time=s.get("start_time") or "08:00 AM",
            end_time=s.get("end_time") or "05:00 PM",
            checkin_time=c_dt,
            checkout_time=co_dt,
            raw_hours_worked=raw_hw,
            rounded_hours_worked=rounded_hw,
            regular_hours=reg_hw,
            overtime_hours=ot_hw,
            hourly_rate=hourly_r,
            earnings=earned,
            status=s.get("status", "completed")
        ))

    total_shifts_count = len(shifts_list)
    total_hours_count = round(sum(s.rounded_hours_worked for s in shifts_list), 2)
    total_regular = round(sum(s.regular_hours for s in shifts_list), 2)
    total_overtime = round(sum(s.overtime_hours for s in shifts_list), 2)
    gross_earnings = round(sum(s.earnings for s in shifts_list), 2)

    # Fetch existing invoices for this worker for target month/year
    invoices_cursor = db["worker_invoices"].find({
        "worker_id": w_uid,
        "period_month": target_month,
        "period_year": target_year
    }).sort("created_at", -1)

    invoices_docs = await invoices_cursor.to_list(length=100)
    invoices_list: List[WorkerInvoiceItem] = []
    total_paid = 0.0

    for inv in invoices_docs:
        amt = float(inv.get("amount_paid") or inv.get("net_payout") or 0.0)
        if inv.get("payment_status") in ["paid", "partial"]:
            total_paid += amt
        invoices_list.append(WorkerInvoiceItem(
            invoice_id=str(inv.get("invoice_id") or inv.get("_id")),
            invoice_number=inv.get("invoice_number") or inv.get("invoice_id") or "INV",
            period_start=inv.get("period_start") or f"{target_year}-{target_month:02d}-01",
            period_end=inv.get("period_end") or f"{target_year}-{target_month:02d}-28",
            hours_worked=float(inv.get("hours_worked") or total_hours_count),
            hourly_rate=resolve_hourly_rate(inv, default=hourly_r),
            gross_amount=float(inv.get("gross_amount") or gross_earnings),
            bonus_amount=float(inv.get("bonus_amount") or 0.0),
            deductions=float(inv.get("deductions") or 0.0),
            net_payout=amt,
            currency="EUR",
            status=inv.get("payment_status", "paid"),
            payment_method=inv.get("payment_method", "Bank Transfer"),
            paid_at=inv.get("created_at"),
            download_url=inv.get("download_url")
        ))

    total_paid = round(total_paid, 2)
    balance_due = round(max(0.0, gross_earnings - total_paid), 2)

    if total_paid >= gross_earnings and gross_earnings > 0:
        pay_st = "paid"
    elif total_paid > 0:
        pay_st = "partial"
    else:
        pay_st = "unpaid"

    return ManagerWorkerMonthlyEarningsResponse(
        worker_id=w_uid,
        worker_name=w_name,
        email=w_email,
        phone=w_phone,
        worker_type=w_type,
        position=w_pos,
        hourly_rate=hourly_r,
        currency="EUR",
        month=target_month,
        year=target_year,
        month_name=month_label,
        total_shifts_worked=total_shifts_count,
        total_hours_worked=total_hours_count,
        regular_hours=total_regular,
        overtime_hours=total_overtime,
        gross_earnings=gross_earnings,
        total_paid=total_paid,
        balance_due=balance_due,
        payment_status=pay_st,
        shifts=shifts_list,
        invoices=invoices_list
    )


# ============================================================================
# 2. Get All Workers Monthly Earnings Overview Table
# ============================================================================
@worker_earnings_invoices_router.get(
    "/worker-earnings",
    response_model=ManagerWorkerEarningsPaginatedResponse,
    summary="Get All Workers Monthly Earnings (Manager Overview Table)",
    description="Returns paginated monthly earnings overview across all approved workers with gross totals and balances."
)
async def get_all_workers_monthly_earnings(
    month: Optional[int] = Query(None, ge=1, le=12, description="Month (1-12)"),
    year: Optional[int] = Query(None, ge=2020, description="Year (e.g. 2026)"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    target_month = month or now.month
    month_str = f"{target_year:04d}-{target_month:02d}"

    worker_query = {
        "role": "worker",
        "account_status": {"$ne": "deleted"}
    }
    if search:
        worker_query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]

    cnt_task = db["users"].count_documents(worker_query)
    skip = (page - 1) * limit
    workers_task = db["users"].find(worker_query).sort("full_name", 1).skip(skip).limit(limit).to_list(length=limit)

    # Pre-fetch shifts for this month using date range index & small projection
    shifts_task = db["shift_executions"].find(
        {
            "date": {"$gte": f"{month_str}-01", "$lte": f"{month_str}-31"},
            "status": {"$ne": "cancelled"}
        },
        projection={"assigned_workers": 1, "workers": 1, "duration_minutes": 1, "date": 1}
    ).to_list(length=1000)

    total_workers_cnt, workers_docs, all_execs = await asyncio.gather(cnt_task, workers_task, shifts_task)

    w_ids = [str(w["_id"]) for w in workers_docs]

    # Pre-fetch invoices for current page workers
    all_invoices = await db["worker_invoices"].find({
        "worker_id": {"$in": w_ids},
        "period_month": target_month,
        "period_year": target_year
    }).to_list(length=500)

    # Compute month-wide totals across all shift executions and invoices
    total_gross_all = 0.0
    for s in all_execs:
        for w_rec in (s.get("assigned_workers") or s.get("workers") or []):
            hw = float(w_rec.get("hours_worked") or 0.0)
            hr = rate_for_shift_record(w_rec)
            total_gross_all += (hw * hr)

    total_paid_all = sum(float(inv.get("amount_paid") or inv.get("net_payout") or 0.0) for inv in all_invoices if inv.get("payment_status") in ["paid", "partial"])
    total_balance_all = max(0.0, total_gross_all - total_paid_all)

    # Aggregate paginated worker metrics
    overview_items = []

    for w in workers_docs:
        wid = str(w["_id"])
        hourly_r = resolve_hourly_rate(w)

        w_shifts = [
            s for s in all_execs
            if any(str(rec.get("worker_id") or rec.get("id")) == wid for rec in (s.get("assigned_workers") or s.get("workers") or []))
        ]

        total_hw = 0.0
        reg_hw = 0.0
        ot_hw = 0.0

        for s in w_shifts:
            w_rec = next((rec for rec in (s.get("assigned_workers") or s.get("workers") or []) if str(rec.get("worker_id") or rec.get("id")) == wid), None)
            hw = float((w_rec and w_rec.get("hours_worked")) or 0.0)
            sched_mins = float(s.get("duration_minutes") or 0.0)
            r_h, o_h = evaluate_shift_overtime(hw, sched_mins)
            total_hw += hw
            reg_hw += r_h
            ot_hw += o_h

        gross_earned = round(total_hw * hourly_r, 2)

        # Invoices sum for worker
        w_invs = [inv for inv in all_invoices if str(inv.get("worker_id")) == wid]
        w_paid = round(sum(float(inv.get("amount_paid") or inv.get("net_payout") or 0.0) for inv in w_invs if inv.get("payment_status") in ["paid", "partial"]), 2)
        w_bal = round(max(0.0, gross_earned - w_paid), 2)

        if w_paid >= gross_earned and gross_earned > 0:
            pay_st = "paid"
        elif w_paid > 0:
            pay_st = "partial"
        else:
            pay_st = "unpaid"

        overview_items.append(ManagerWorkerEarningsOverviewItem(
            worker_id=wid,
            name=w.get("full_name") or w.get("name") or "Worker",
            email=w.get("email"),
            worker_type=w.get("worker_type") or "employee",
            position=w.get("position") or "Cleaner",
            hourly_rate=hourly_r,
            total_shifts=len(w_shifts),
            total_hours=round(total_hw, 2),
            regular_hours=round(reg_hw, 2),
            overtime_hours=round(ot_hw, 2),
            gross_earnings=gross_earned,
            total_paid=w_paid,
            balance_due=w_bal,
            payment_status=pay_st
        ))

    has_more = (skip + len(overview_items)) < total_workers_cnt

    return ManagerWorkerEarningsPaginatedResponse(
        total_count=total_workers_cnt,
        total_workers=total_workers_cnt,
        total_gross_payout=round(total_gross_all, 2),
        total_paid_payout=round(total_paid_all, 2),
        total_balance_due=round(total_balance_all, 2),
        page=page,
        limit=limit,
        has_more=has_more,
        month=target_month,
        year=target_year,
        workers=overview_items
    )


def _format_invoice_detail(inv: dict, shifts_included: List[WorkerShiftEarningItem] = None) -> ManagerWorkerInvoiceDetailResponse:
    amt_paid = float(inv.get("amount_paid") or inv.get("net_payout") or 0.0)
    gross_amt = float(inv.get("gross_amount") or amt_paid)
    cat = inv.get("created_at") if isinstance(inv.get("created_at"), datetime) else datetime.now(timezone.utc)
    uat = inv.get("updated_at") if isinstance(inv.get("updated_at"), datetime) else datetime.now(timezone.utc)

    return ManagerWorkerInvoiceDetailResponse(
        id=str(inv.get("id") or inv.get("_id") or inv.get("invoice_id")),
        invoice_id=str(inv.get("invoice_id") or inv.get("id") or inv.get("_id")),
        invoice_number=inv.get("invoice_number") or inv.get("invoice_id") or "INV",
        worker_id=str(inv.get("worker_id", "")),
        worker_name=inv.get("worker_name", "Worker"),
        worker_email=inv.get("worker_email"),
        worker_type=inv.get("worker_type", "employee"),
        period_month=int(inv.get("period_month", 8)),
        period_year=int(inv.get("period_year", 2026)),
        period_label=inv.get("period_label", "Period"),
        hours_worked=float(inv.get("hours_worked", 0.0)),
        regular_hours=float(inv.get("regular_hours", 0.0)),
        overtime_hours=float(inv.get("overtime_hours", 0.0)),
        hourly_rate=resolve_hourly_rate(inv),
        gross_amount=gross_amt,
        amount_paid=amt_paid,
        balance_due=float(inv.get("balance_due", 0.0)),
        currency="EUR",
        payment_status=inv.get("payment_status", "paid"),
        payment_method=inv.get("payment_method", "bank_transfer"),
        payment_reference=inv.get("payment_reference"),
        notes=inv.get("notes"),
        created_by_manager_id=inv.get("created_by_manager_id"),
        created_by_manager_name=inv.get("created_by_manager_name"),
        created_at=cat,
        updated_at=uat,
        shifts_included=shifts_included or []
    )


# ============================================================================
# 3. Create Worker Invoice / Payout
# ============================================================================
@worker_earnings_invoices_router.post(
    "/worker-invoices",
    response_model=ManagerWorkerInvoiceDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Worker Invoice / Record Payout (Manager)",
    description="""
### Create Worker Invoice / Record Payout
Records a worker salary payout or invoice. Generates a unique Invoice Number (`INV-YYYYMM-XXXX`), updates worker invoice history, and notifies the worker.
"""
)
@worker_earnings_invoices_router.post(
    "/workers/{worker_id}/invoices",
    response_model=ManagerWorkerInvoiceDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Worker Invoice (Alias)",
    include_in_schema=False
)
async def create_worker_invoice(
    invoice_in: ManagerWorkerInvoiceCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    worker_id = invoice_in.worker_id

    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one({"$and": [user_query, {"role": "worker"}]})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Worker not found")

    w_uid = str(user_doc["_id"])
    w_name = user_doc.get("full_name") or user_doc.get("name") or "Worker"
    w_email = user_doc.get("email")
    w_type = user_doc.get("worker_type") or "employee"
    hourly_r = resolve_hourly_rate(user_doc)

    p_month = invoice_in.period_month
    p_year = invoice_in.period_year
    month_str = f"{p_year:04d}-{p_month:02d}"
    month_label = datetime(p_year, p_month, 1).strftime("%B %Y")

    # Generate unique Invoice ID and Number
    inv_raw_id = f"inv_{p_year}{p_month:02d}_{uuid.uuid4().hex[:6]}"
    inv_number = f"INV-{p_year}{p_month:02d}-{uuid.uuid4().hex[:4].upper()}"

    # Calculate month stats
    shifts_cursor = db["shift_executions"].find({
        "date": {"$regex": f"^{month_str}"},
        "status": {"$ne": "cancelled"},
        "$or": [
            {"assigned_workers.worker_id": w_uid},
            {"workers.worker_id": w_uid},
            {"worker_ids": w_uid}
        ]
    })
    w_shifts = await shifts_cursor.to_list(length=300)

    total_hw = 0.0
    reg_hw = 0.0
    ot_hw = 0.0
    shifts_included = []

    for s in w_shifts:
        w_rec = next((rec for rec in (s.get("assigned_workers") or s.get("workers") or []) if str(rec.get("worker_id") or rec.get("id")) == w_uid), None)
        hw = float((w_rec and w_rec.get("hours_worked")) or 0.0)
        sched_mins = float(s.get("duration_minutes") or 0.0)
        r_h, o_h = evaluate_shift_overtime(hw, sched_mins)
        earned = round(hw * hourly_r, 2)
        total_hw += hw
        reg_hw += r_h
        ot_hw += o_h

        shifts_included.append(WorkerShiftEarningItem(
            shift_id=str(s.get("id") or s.get("_id")),
            title=s.get("title") or s.get("plan_name") or "Cleaning Shift",
            location_name=s.get("location_name") or "Amsterdam Location",
            date=str(s.get("date") or month_str + "-01"),
            start_time=s.get("start_time") or "08:00 AM",
            end_time=s.get("end_time") or "05:00 PM",
            raw_hours_worked=hw,
            rounded_hours_worked=hw,
            regular_hours=r_h,
            overtime_hours=o_h,
            hourly_rate=hourly_r,
            earnings=earned,
            status=s.get("status", "completed")
        ))

    gross_amount = round(total_hw * hourly_r, 2)
    amount_paid = round(float(invoice_in.amount_paid), 2)
    balance_due = round(max(0.0, gross_amount - amount_paid), 2)

    now = datetime.now(timezone.utc)
    manager_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or "")
    manager_name = getattr(current_user, "full_name", "Manager")

    inv_doc = {
        "_id": inv_raw_id,
        "id": inv_raw_id,
        "invoice_id": inv_raw_id,
        "invoice_number": inv_number,
        "worker_id": w_uid,
        "worker_name": w_name,
        "worker_email": w_email,
        "worker_type": w_type,
        "period_month": p_month,
        "period_year": p_year,
        "period_label": month_label,
        "period_start": f"{p_year:04d}-{p_month:02d}-01",
        "period_end": f"{p_year:04d}-{p_month:02d}-28",
        "hours_worked": round(total_hw, 2),
        "regular_hours": round(reg_hw, 2),
        "overtime_hours": round(ot_hw, 2),
        "hourly_rate": hourly_r,
        "gross_amount": gross_amount,
        "amount_paid": amount_paid,
        "balance_due": balance_due,
        "currency": "EUR",
        "payment_status": invoice_in.payment_status or ("paid" if amount_paid >= gross_amount else "partial"),
        "payment_method": invoice_in.payment_method or "bank_transfer",
        "payment_reference": invoice_in.payment_reference,
        "notes": invoice_in.notes,
        "created_by_manager_id": manager_id,
        "created_by_manager_name": manager_name,
        "created_at": now,
        "updated_at": now
    }

    await db["worker_invoices"].insert_one(inv_doc)

    # Notify worker about invoice / payout
    from app.services.notification_service import NotificationService
    notif_service = NotificationService()
    await notif_service.create_notification(
        title="Payout Processed",
        message=f"A salary payout of EUR {amount_paid:.2f} has been issued for {month_label}. Invoice: {inv_number}",
        notification_type="invoice_issued",
        recipient_type="worker",
        user_id=w_uid
    )

    return _format_invoice_detail(inv_doc, shifts_included=shifts_included)


# ============================================================================
# 4. List Worker Invoices (Paginated)
# ============================================================================
@worker_earnings_invoices_router.get(
    "/worker-invoices",
    response_model=ManagerWorkerInvoicePaginatedResponse,
    summary="Get List of Worker Invoices (Paginated)",
    description="Returns paginated list of worker invoices with filters for worker ID, month, year, and payment status."
)
async def list_worker_invoices(
    worker_id: Optional[str] = Query(None, description="Filter by specific worker ID"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Filter by month (1-12)"),
    year: Optional[int] = Query(None, ge=2020, description="Filter by year (e.g. 2026)"),
    payment_status: Optional[str] = Query(None, description="Filter: 'paid', 'partial', 'pending'"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    query = {}
    if worker_id:
        query["worker_id"] = worker_id
    if month:
        query["period_month"] = month
    if year:
        query["period_year"] = year
    if payment_status and payment_status.lower() != "all":
        query["payment_status"] = payment_status.lower()

    total_count = await db["worker_invoices"].count_documents(query)
    skip = (page - 1) * limit
    cursor = db["worker_invoices"].find(query).sort("created_at", -1).skip(skip).limit(limit)
    invoices_raw = await cursor.to_list(length=limit)

    invoices_list = []
    total_invoiced = 0.0
    total_paid = 0.0

    for inv in invoices_raw:
        amt_paid = float(inv.get("amount_paid") or inv.get("net_payout") or 0.0)
        gross_amt = float(inv.get("gross_amount") or amt_paid)
        total_invoiced += gross_amt
        total_paid += amt_paid
        invoices_list.append(_format_invoice_detail(inv))

    has_more = (skip + len(invoices_list)) < total_count

    return ManagerWorkerInvoicePaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        has_more=has_more,
        total_invoiced_amount=round(total_invoiced, 2),
        total_paid_amount=round(total_paid, 2),
        invoices=invoices_list
    )


# ============================================================================
# 5. Get Single Worker Invoice Detail
# ============================================================================
@worker_earnings_invoices_router.get(
    "/worker-invoices/{invoice_id}",
    response_model=ManagerWorkerInvoiceDetailResponse,
    summary="Get Worker Invoice Detail",
    description="Returns detailed invoice information including full breakdown of shifts and hours."
)
async def get_worker_invoice_detail(
    invoice_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    inv = await db["worker_invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}, {"invoice_number": invoice_id}]
    })
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return _format_invoice_detail(inv)


# ============================================================================
# 6. Update Worker Invoice (PATCH)
# ============================================================================
@worker_earnings_invoices_router.patch(
    "/worker-invoices/{invoice_id}",
    response_model=ManagerWorkerInvoiceDetailResponse,
    summary="Update Worker Invoice (Manager)",
    description="Updates invoice payment status, amount paid, payment method, reference, and notes."
)
async def update_worker_invoice(
    invoice_id: str,
    invoice_update: ManagerWorkerInvoiceUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    inv = await db["worker_invoices"].find_one({
        "$or": [{"_id": invoice_id}, {"id": invoice_id}, {"invoice_id": invoice_id}, {"invoice_number": invoice_id}]
    })
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    now = datetime.now(timezone.utc)
    set_fields = {"updated_at": now}

    gross_amt = float(inv.get("gross_amount", 0.0))
    current_paid = float(inv.get("amount_paid", 0.0))

    if invoice_update.amount_paid is not None:
        new_paid = float(invoice_update.amount_paid)
        set_fields["amount_paid"] = new_paid
        set_fields["net_payout"] = new_paid
        set_fields["balance_due"] = round(max(0.0, gross_amt - new_paid), 2)
        current_paid = new_paid

    if invoice_update.payment_status is not None:
        set_fields["payment_status"] = invoice_update.payment_status
    elif invoice_update.amount_paid is not None:
        if current_paid >= gross_amt and gross_amt > 0:
            set_fields["payment_status"] = "paid"
        elif current_paid > 0:
            set_fields["payment_status"] = "partial"

    if invoice_update.payment_method is not None:
        set_fields["payment_method"] = invoice_update.payment_method
    if invoice_update.payment_reference is not None:
        set_fields["payment_reference"] = invoice_update.payment_reference
    if invoice_update.notes is not None:
        set_fields["notes"] = invoice_update.notes

    await db["worker_invoices"].update_one({"_id": inv["_id"]}, {"$set": set_fields})
    updated_inv = await db["worker_invoices"].find_one({"_id": inv["_id"]})

    return _format_invoice_detail(updated_inv)

