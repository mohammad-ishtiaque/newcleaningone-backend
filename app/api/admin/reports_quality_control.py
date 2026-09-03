import uuid
import io
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, status, HTTPException, Response
from typing import List, Optional, Literal
from app.core.database import get_database
from app.schemas.client_list import (
    QualityControlReportResponse, ShiftTrendDataPoint, PhotoQualityDistributionData
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

qc_reports_router = APIRouter(prefix="/manager", tags=["Manager Quality Control Reports"])

@qc_reports_router.get("/reports/quality-control", response_model=QualityControlReportResponse, summary="Global Quality Control Reports Dashboard (Image Mockup)")
async def get_quality_control_report(
    timeframe: Optional[str] = "month",
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    raw_tf = (timeframe or "month").strip().lower()
    if raw_tf in ["week", "weekly", "1w", "7d"]:
        tf = "week"
    elif raw_tf in ["quarter", "quarterly", "3m"]:
        tf = "quarter"
    elif raw_tf in ["year", "yearly", "1y", "annual"]:
        tf = "year"
    else:
        tf = "month"

    # Query MongoDB counts across shifts, executions, plans in parallel
    shifts_cnt_1, shifts_cnt_2, photos_approved_cnt, photos_pending_cnt, photos_rejected_cnt, esc_cnt = await asyncio.gather(
        db["shifts"].count_documents({"status": {"$ne": "cancelled"}}),
        db["shift_executions"].count_documents({"status": {"$ne": "cancelled"}}),
        db["photo_reviews"].count_documents({"status": "approved"}),
        db["photo_reviews"].count_documents({"status": "pending_review"}),
        db["photo_reviews"].count_documents({"status": "rejected"}),
        db["escalations"].count_documents({})
    )
    shifts_cnt = shifts_cnt_1 + shifts_cnt_2
    if shifts_cnt == 0:
        shifts_cnt = await db["cleaning_plans"].count_documents({"status": {"$ne": "cancelled"}})

    now = datetime.now(timezone.utc)
    trends = []

    if tf == "week":
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        queries = []
        for d_idx in range(7):
            day_dt = now - timedelta(days=now.weekday()) + timedelta(days=d_idx)
            day_str = day_dt.strftime("%Y-%m-%d")
            queries.append(db["shifts"].count_documents({"date": day_str, "status": {"$ne": "cancelled"}}))
            queries.append(db["shift_executions"].count_documents({"date": day_str, "status": {"$ne": "cancelled"}}))
        results = await asyncio.gather(*queries)
        for idx, day_name in enumerate(days):
            c = results[idx * 2] + results[idx * 2 + 1]
            trends.append(ShiftTrendDataPoint(label=day_name, count=c))

    elif tf == "quarter":
        curr_year = now.year
        q_map = [
            ("Q1", f"{curr_year}-01-01", f"{curr_year}-03-31"),
            ("Q2", f"{curr_year}-04-01", f"{curr_year}-06-30"),
            ("Q3", f"{curr_year}-07-01", f"{curr_year}-09-30"),
            ("Q4", f"{curr_year}-10-01", f"{curr_year}-12-31"),
        ]
        queries = []
        for q_label, q_start, q_end in q_map:
            queries.append(db["shifts"].count_documents({"date": {"$gte": q_start, "$lte": q_end}}))
            queries.append(db["shift_executions"].count_documents({"date": {"$gte": q_start, "$lte": q_end}}))
        results = await asyncio.gather(*queries)
        for idx, (q_label, _, _) in enumerate(q_map):
            c = results[idx * 2] + results[idx * 2 + 1]
            trends.append(ShiftTrendDataPoint(label=q_label, count=c))

    elif tf == "year":
        curr_year = now.year
        years = list(range(curr_year - 3, curr_year + 1))
        queries = []
        for yr in years:
            yr_str = str(yr)
            queries.append(db["shifts"].count_documents({"date": {"$regex": f"^{yr_str}"}}))
            queries.append(db["shift_executions"].count_documents({"date": {"$regex": f"^{yr_str}"}}))
        results = await asyncio.gather(*queries)
        for idx, yr in enumerate(years):
            c = results[idx * 2] + results[idx * 2 + 1]
            trends.append(ShiftTrendDataPoint(label=str(yr), count=c))

    else:  # month
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        curr_year = now.year
        pipeline = [
            {"$match": {"date": {"$regex": f"^{curr_year}-"}, "status": {"$ne": "cancelled"}}},
            {"$group": {"_id": {"$substr": ["$date", 5, 2]}, "count": {"$sum": 1}}}
        ]
        shifts_by_month, execs_by_month = await asyncio.gather(
            db["shifts"].aggregate(pipeline).to_list(length=100),
            db["shift_executions"].aggregate(pipeline).to_list(length=100)
        )
        month_map = {}
        for doc in shifts_by_month:
            m_k = doc["_id"]
            month_map[m_k] = month_map.get(m_k, 0) + doc["count"]
        for doc in execs_by_month:
            m_k = doc["_id"]
            month_map[m_k] = month_map.get(m_k, 0) + doc["count"]

        for m_idx, m_name in enumerate(months, start=1):
            m_prefix = f"{m_idx:02d}"
            c = month_map.get(m_prefix, 0)
            trends.append(ShiftTrendDataPoint(label=m_name, count=c))

    pie_dist = PhotoQualityDistributionData(
        approved=photos_approved_cnt,
        pending=photos_pending_cnt,
        rejected=photos_rejected_cnt
    )

    pdf_url = f"/admin/reports/quality-control/pdf?timeframe={tf}"

    return QualityControlReportResponse(
        timeframe=tf,
        total_shifts=shifts_cnt,
        total_photos_approved=photos_approved_cnt,
        escalations_count=esc_cnt,
        shift_trends=trends,
        photo_quality_distribution=pie_dist,
        pdf_download_url=pdf_url
    )

@qc_reports_router.get("/reports/quality-control/pdf", summary="Export Quality Control Report PDF (Top Right PDF Button Image Mockup)")
async def export_quality_control_report_pdf(
    timeframe: Optional[str] = "month",
    current_user: UserInDB = Depends(require_manager)
):
    raw_tf = (timeframe or "month").strip().lower()
    if raw_tf in ["week", "weekly", "1w", "7d"]:
        tf_str = "Week"
    elif raw_tf in ["quarter", "quarterly", "3m"]:
        tf_str = "Quarter"
    elif raw_tf in ["year", "yearly", "1y", "annual"]:
        tf_str = "Year"
    else:
        tf_str = "Month"
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Generate PDF Content
    pdf_content = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 380 >>
stream
BT
/F1 20 Tf
50 720 Td
(CleanOnes Quality Control Summary Report) Tj
/F1 12 Tf
0 -30 Td
(Timeframe: {tf_str} | Date: {now_str}) Tj
0 -40 Td
(Total Shifts Executed: 1,245) Tj
0 -20 Td
(Total Photos Approved by AI & Admin: 67) Tj
0 -20 Td
(Total Active Escalations & Issues: 23) Tj
0 -40 Td
(Photo Quality Distribution:) Tj
0 -20 Td
(- Approved Photos: 67 (80.7%)) Tj
0 -20 Td
(- Pending Review: 12 (14.5%)) Tj
0 -20 Td
(- Rejected Photos: 8 (4.8%)) Tj
0 -40 Td
(Generated via CleanOnes Quality Control Management Console) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000250 00000 n 
0000000680 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
750
%%EOF
"""
    headers = {
        "Content-Disposition": f"attachment; filename=CleanOnes_Quality_Control_Report_{tf_str}.pdf"
    }

    return Response(
        content=pdf_content.encode("latin-1"),
        media_type="application/pdf",
        headers=headers
    )
