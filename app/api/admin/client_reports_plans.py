import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any
from app.schemas.client_list import ClientCleaningPlanTaskItem, ClientReportItem

DEFAULT_TASKS = [
    "Vacuum Floor",
    "Clean Mirrors",
    "Empty Trash",
    "Replace Towels",
    "Mop Floor"
]

def get_default_cleaning_plan_tasks() -> List[ClientCleaningPlanTaskItem]:
    return [
        ClientCleaningPlanTaskItem(task_id=f"tsk_{i+1}", name=t, is_completed=False)
        for i, t in enumerate(DEFAULT_TASKS)
    ]

def get_default_reports(client_id: str) -> List[ClientReportItem]:
    return [
        ClientReportItem(
            id=f"rep_1",
            title="May 2026 Service Report",
            date_formatted="1 Jun 2026",
            date_iso="2026-06-01",
            status="Sent",
            download_url=f"/admin/clients/{client_id}/reports/export-pdf?report_id=rep_1"
        ),
        ClientReportItem(
            id=f"rep_2",
            title="April 2026 Service Report",
            date_formatted="1 May 2026",
            date_iso="2026-05-01",
            status="Archived",
            download_url=f"/admin/clients/{client_id}/reports/export-pdf?report_id=rep_2"
        ),
        ClientReportItem(
            id=f"rep_3",
            title="Q1 2026 Summary",
            date_formatted="1 Apr 2026",
            date_iso="2026-04-01",
            status="Archived",
            download_url=f"/admin/clients/{client_id}/reports/export-pdf?report_id=rep_3"
        )
    ]

def generate_report_pdf_bytes(company_name: str, report_title: str = "Client Service Report") -> bytes:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 120 >>
stream
BT
/F1 18 Tf
50 700 Td
({company_name} - {report_title}) Tj
/F1 12 Tf
0 -30 Td
(Generated on: {now_str}) Tj
0 -20 Td
(Summary: Service performed cleanly with 100% attendance & quality checks.) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000204 00000 n 
trailer
<< /Size 5 /Root 1 0 R >>
startxref
375
%%EOF"""
    return content.encode("utf-8")
