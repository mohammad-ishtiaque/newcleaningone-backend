import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Optional, List, Literal
from bson import ObjectId
from app.core.database import get_database
from app.services.worker_salary import resolve_hourly_rate
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.schemas.worker_modules import (
    WorkerShiftEarningItem, WorkerDailyEarningItem, WorkerEstimatedEarningsResponse
)
from app.api.worker_shift_utils import (
    calculate_rounded_work_hours, evaluate_shift_overtime
)

router = APIRouter(prefix="/worker", tags=["Worker Profile & Earnings"])


def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role not in [RoleEnum.worker, "worker"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user


@router.get(
    "/earnings",
    response_model=WorkerEstimatedEarningsResponse,
    summary="Get Worker Estimated Earnings (Profile Section)",
    description="""
### Get Worker Estimated Earnings
Calculates worker estimated gross earnings by multiplying daily worked hours (with 30-minute block rounding) by the worker's per hour salary.

#### Rounding & Overtime Rules:
- **30-Minute Blocks**: Extra minutes in `(0, 30]` round to `0.5h`, extra minutes in `(30, 60]` round to `1.0h`.
- **Regular Hours**: `min(rounded_hours, scheduled_hours)`
- **Overtime Hours**: `max(0, rounded_hours - scheduled_hours)`
- **Estimated Earnings**: `rounded_hours * hourly_rate`

#### Query Parameters:
- **`timeframe`**: `"today"`, `"week"`, `"month"`, or `"all"` (default: `"month"`).
- **`month`**: Specific month `1-12` (optional, defaults to current month).
- **`year`**: Specific year e.g. `2026` (optional, defaults to current year).
"""
)
@router.get("/profile/earnings", response_model=WorkerEstimatedEarningsResponse, include_in_schema=False)
@router.get("/earnings/estimate", response_model=WorkerEstimatedEarningsResponse, include_in_schema=False)
async def get_worker_estimated_earnings(
    timeframe: Optional[str] = Query("month", description="Filter timeframe: 'today', 'week', 'month', 'all'"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Month (1-12)"),
    year: Optional[int] = Query(None, ge=2020, description="Year (e.g. 2026)"),
    current_user: UserInDB = Depends(require_worker)
):
    db = get_database()
    worker_id = str(getattr(current_user, "id", None) or getattr(current_user, "_id", None) or getattr(current_user, "mongo_id", None) or "")
    worker_name = getattr(current_user, "full_name", "Worker")

    user_query = {"_id": ObjectId(worker_id)} if ObjectId.is_valid(worker_id) else {"$or": [{"_id": worker_id}, {"id": worker_id}]}
    user_doc = await db["users"].find_one(user_query) or {}

    hourly_r = resolve_hourly_rate(user_doc)

    now = datetime.now(timezone.utc)
    target_year = year or now.year
    target_month = month or now.month

    # Date range filters based on timeframe
    tf = (timeframe or "month").lower()
    if tf == "today":
        today_str = now.strftime("%Y-%m-%d")
        date_query = {"$regex": f"^{today_str}"}
        period_label = f"Today ({today_str})"
    elif tf == "week":
        start_of_week = now - timedelta(days=now.weekday())
        week_dates = [(start_of_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        date_query = {"$in": week_dates}
        period_label = f"Week of {week_dates[0]} to {week_dates[-1]}"
    elif tf == "all":
        date_query = {"$exists": True}
        period_label = "All Time"
    else:  # default 'month'
        month_str = f"{target_year:04d}-{target_month:02d}"
        date_query = {"$regex": f"^{month_str}"}
        period_label = f"{datetime(target_year, target_month, 1).strftime('%B %Y')}"

    # Search completed / checked-in shifts across shift_executions, shifts, extra_services
    shift_query = {
        "date": date_query,
        "status": {"$ne": "cancelled"},
        "$or": [
            {"assigned_workers.worker_id": worker_id},
            {"workers.worker_id": worker_id},
            {"worker_ids": worker_id}
        ]
    }

    execs = await db["shift_executions"].find(shift_query).sort("date", -1).to_list(length=300)
    direct_shifts = await db["shifts"].find(shift_query).sort("date", -1).to_list(length=300)

    all_raw_shifts = execs + direct_shifts

    shifts_earnings_list: List[WorkerShiftEarningItem] = []
    daily_map = {}
    seen_shift_ids = set()

    for s in all_raw_shifts:
        s_id = str(s.get("id") or s.get("_id"))
        if s_id in seen_shift_ids:
            continue
        seen_shift_ids.add(s_id)

        w_list = s.get("assigned_workers") or s.get("workers") or []
        w_rec = next((w for w in w_list if str(w.get("worker_id") or w.get("id")) == worker_id), None)
        if not w_rec:
            continue

        c_time = w_rec.get("checkin_time")
        co_time = w_rec.get("checkout_time")
        s_date = str(s.get("date") or (c_time.strftime("%Y-%m-%d") if isinstance(c_time, datetime) else now.strftime("%Y-%m-%d")))

        # Check if duration already computed
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

        # Scheduled duration
        sched_mins = float(s.get("duration_minutes") or 0.0)
        reg_hw, ot_hw = evaluate_shift_overtime(rounded_hw, sched_mins)
        earned = round(rounded_hw * hourly_r, 2)

        s_item = WorkerShiftEarningItem(
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
        )
        shifts_earnings_list.append(s_item)

        # Aggregate into daily_map
        if s_date not in daily_map:
            day_dt = datetime.strptime(s_date, "%Y-%m-%d") if len(s_date) == 10 else now
            daily_map[s_date] = {
                "date": s_date,
                "day_name": day_dt.strftime("%A"),
                "shifts_count": 0,
                "total_hours_worked": 0.0,
                "regular_hours": 0.0,
                "overtime_hours": 0.0,
                "daily_earnings": 0.0
            }
        daily_map[s_date]["shifts_count"] += 1
        daily_map[s_date]["total_hours_worked"] = round(daily_map[s_date]["total_hours_worked"] + rounded_hw, 2)
        daily_map[s_date]["regular_hours"] = round(daily_map[s_date]["regular_hours"] + reg_hw, 2)
        daily_map[s_date]["overtime_hours"] = round(daily_map[s_date]["overtime_hours"] + ot_hw, 2)
        daily_map[s_date]["daily_earnings"] = round(daily_map[s_date]["daily_earnings"] + earned, 2)

    # Sort daily items chronologically
    daily_items = [WorkerDailyEarningItem(**d) for d in sorted(daily_map.values(), key=lambda x: x["date"], reverse=True)]

    total_shifts = len(shifts_earnings_list)
    total_hours = round(sum(s.rounded_hours_worked for s in shifts_earnings_list), 2)
    total_regular = round(sum(s.regular_hours for s in shifts_earnings_list), 2)
    total_overtime = round(sum(s.overtime_hours for s in shifts_earnings_list), 2)
    total_gross = round(sum(s.earnings for s in shifts_earnings_list), 2)

    # Fetch total paid amount from worker_invoices collection
    invoices_cursor = db["worker_invoices"].find({
        "worker_id": worker_id,
        "payment_status": {"$in": ["paid", "partial"]}
    })
    total_paid = 0.0
    async for inv in invoices_cursor:
        if tf == "month":
            if inv.get("period_month") == target_month and inv.get("period_year") == target_year:
                total_paid += float(inv.get("amount_paid") or inv.get("net_payout") or 0.0)
        else:
            total_paid += float(inv.get("amount_paid") or inv.get("net_payout") or 0.0)

    total_paid = round(total_paid, 2)
    pending_balance = round(max(0.0, total_gross - total_paid), 2)

    return WorkerEstimatedEarningsResponse(
        worker_id=worker_id,
        worker_name=worker_name,
        hourly_rate=hourly_r,
        currency="EUR",
        timeframe=tf,
        period_label=period_label,
        total_shifts_worked=total_shifts,
        total_hours_worked=total_hours,
        regular_hours=total_regular,
        overtime_hours=total_overtime,
        estimated_gross_earnings=total_gross,
        total_paid_earnings=total_paid,
        pending_balance=pending_balance,
        daily_breakdown=daily_items,
        shifts=shifts_earnings_list
    )
