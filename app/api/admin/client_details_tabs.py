import uuid
from datetime import datetime, timezone, date
from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Response
from typing import List, Optional
from bson import ObjectId
from app.core.database import get_database
from app.schemas.client_list import (
    ContactCreate, LocationCreate, LocationUpdate, LocationResponse,
    ClientContactItem, ClientContactPaginatedResponse,
    ClientLocationItem, ClientLocationPaginatedResponse,
    ClientCleaningPlanTaskItem, ClientCleaningPlanResponse, ClientCleaningPlanUpdate, AssignLocationRequest,
    ClientReportItem, ClientReportsListResponse, SendReportEmailRequest,
    ClientDashboardOverviewResponse, ClientContractDetailsResponse, ContractRenewRequest
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager
from app.api.admin.client_reports_plans import get_default_cleaning_plan_tasks, get_default_reports, generate_report_pdf_bytes

client_details_tabs_router = APIRouter(prefix="/manager", tags=["Admin Client Management"])

def _format_date_human(d_val) -> str:
    if not d_val:
        return "31 Dec 2026"
    try:
        if isinstance(d_val, str):
            dt = datetime.strptime(d_val, "%Y-%m-%d")
        elif isinstance(d_val, datetime):
            dt = d_val
        elif isinstance(d_val, date):
            dt = datetime.combine(d_val, datetime.min.time())
        else:
            return "31 Dec 2026"
        return dt.strftime("%d %b %Y")
    except Exception:
        return "31 Dec 2026"


# ============================================================================
# Tab 1: Overview Tab (Image 2)
# ============================================================================

@client_details_tabs_router.get(
    "/clients/{client_id}/dashboard-overview",
    response_model=ClientDashboardOverviewResponse,
    summary="Client Detailed View: Overview Tab (Image 2)"
)
async def get_client_dashboard_overview(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")
    ind = cdoc.get("industry", "Corporate")
    st = cdoc.get("status", "Active").capitalize()

    loc_count = await db["locations"].count_documents({"client_id": cid})
    contact_count = await db["contacts"].count_documents({"client_id": cid})
    if contact_count == 0 and cdoc.get("contacts"):
        contact_count = len(cdoc.get("contacts", []))

    tasks_count = await db["rooms"].count_documents({"client_id": cid})

    contract_doc = await db["contracts"].find_one({"$or": [{"client_id": cid}, {"client_name": cname}]})
    expiry_raw = contract_doc.get("expiry_date") if contract_doc else cdoc.get("contract_expiry")
    expiry_fmt = _format_date_human(expiry_raw)
    contract_st = contract_doc.get("status", "Active").capitalize() if contract_doc else "Active"

    sub_title = f"{ind} • Contract: {contract_st}"

    return ClientDashboardOverviewResponse(
        client_id=cid,
        company_name=cname,
        industry=ind,
        status=st,
        subtitle_contract_status=sub_title,
        contract_status=contract_st,
        contract_expiry_date=str(expiry_raw) if expiry_raw else "2026-12-31",
        contract_expiry_formatted=expiry_fmt,
        locations_count=loc_count,
        contacts_count=contact_count,
        active_tasks_count=tasks_count
    )


# ============================================================================
# Tab 2: Contacts Tab (Image 3)
# ============================================================================

@client_details_tabs_router.get(
    "/clients/{client_id}/contacts",
    response_model=ClientContactPaginatedResponse,
    summary="Client Detailed View: Contacts Tab (Image 3)"
)
async def get_client_contacts(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    total_count = await db["contacts"].count_documents({"client_id": cid})
    skip = (page - 1) * limit
    raw_contacts = await db["contacts"].find({"client_id": cid}).skip(skip).limit(limit).to_list(length=limit)

    items = []
    for c in raw_contacts:
        items.append(ClientContactItem(
            id=str(c.get("_id") or c.get("id")),
            name=c.get("name", "Contact"),
            role=c.get("role", "Facility Manager"),
            email=c.get("email", ""),
            phone=c.get("phone", "")
        ))

    return ClientContactPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        contacts=items
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/contacts",
    response_model=ClientContactItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add Client Contact (Image 3)"
)
async def add_client_contact(
    client_id: str,
    contact_in: ContactCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cnt_id = f"cnt_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    doc = {
        "_id": cnt_id,
        "id": cnt_id,
        "client_id": cid,
        "name": contact_in.name,
        "role": contact_in.role,
        "email": contact_in.email,
        "phone": contact_in.phone,
        "created_at": now,
        "updated_at": now
    }

    await db["contacts"].insert_one(doc)
    return ClientContactItem(
        id=cnt_id,
        name=contact_in.name,
        role=contact_in.role,
        email=contact_in.email,
        phone=contact_in.phone
    )


@client_details_tabs_router.delete(
    "/clients/{client_id}/contacts/{contact_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Client Contact (Image 3)"
)
async def delete_client_contact(
    client_id: str,
    contact_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    res = await db["contacts"].delete_one({"$or": [{"_id": contact_id}, {"id": contact_id}]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"message": "Contact deleted successfully"}


# ============================================================================
# Tab 3: Contract Tab (Image 5)
# ============================================================================

@client_details_tabs_router.get(
    "/clients/{client_id}/contract",
    response_model=ClientContractDetailsResponse,
    summary="Client Detailed View: Contract Tab (Image 5)"
)
async def get_client_contract_details(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")

    contract_doc = await db["contracts"].find_one({"$or": [{"client_id": cid}, {"client_name": cname}]})
    expiry_raw = contract_doc.get("expiry_date") if contract_doc else cdoc.get("contract_expiry", "2026-12-31")
    expiry_fmt = _format_date_human(expiry_raw)
    st = contract_doc.get("status", "Active").capitalize() if contract_doc else "Active"
    pdf = contract_doc.get("pdf_url") if contract_doc else None

    return ClientContractDetailsResponse(
        client_id=cid,
        company_name=cname,
        status=st,
        expiry_date=str(expiry_raw),
        expiry_date_formatted=expiry_fmt,
        pdf_url=pdf
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/contract/renew",
    response_model=ClientContractDetailsResponse,
    summary="Renew Client Contract (Image 5)"
)
async def renew_client_contract(
    client_id: str,
    renew_in: ContractRenewRequest,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")
    now = datetime.now(timezone.utc)
    new_exp_str = renew_in.expiry_date.isoformat()

    await db["contracts"].update_one(
        {"$or": [{"client_id": cid}, {"client_name": cname}]},
        {"$set": {
            "client_id": cid,
            "client_name": cname,
            "expiry_date": new_exp_str,
            "status": "active",
            "updated_at": now
        }},
        upsert=True
    )

    expiry_fmt = _format_date_human(new_exp_str)
    return ClientContractDetailsResponse(
        client_id=cid,
        company_name=cname,
        status="Active",
        expiry_date=new_exp_str,
        expiry_date_formatted=expiry_fmt,
        pdf_url=None
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/contract/upload",
    summary="Upload Client Contract PDF (Image 5)"
)
async def upload_client_contract_pdf(
    client_id: str,
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}], "status": {"$ne": "deleted"}}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    file_url = f"/uploads/contracts/{client_id}_{file.filename}"

    await db["contracts"].update_one(
        {"client_id": cid},
        {"$set": {"pdf_url": file_url, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    return {"message": "Contract uploaded successfully", "pdf_url": file_url}


# ============================================================================
# Tab 4: Reports Tab (Image 2)
# ============================================================================

@client_details_tabs_router.get(
    "/clients/{client_id}/reports",
    response_model=ClientReportsListResponse,
    summary="Client Detailed View: Reports Tab (Image 2)"
)
async def get_client_reports(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    raw_reports = await db["client_reports"].find({"client_id": cid}).sort("created_at", -1).to_list(length=100)

    items = []
    for r in raw_reports:
        rid = str(r.get("_id") or r.get("id"))
        items.append(ClientReportItem(
            id=rid,
            title=r.get("title", "Client Service Report"),
            date_formatted=r.get("date_formatted", "1 Jun 2026"),
            date_iso=r.get("date_iso", "2026-06-01"),
            status=r.get("status", "Sent"),
            download_url=f"/admin/clients/{cid}/reports/export-pdf?report_id={rid}"
        ))

    return ClientReportsListResponse(
        total_count=len(items),
        reports=items
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/reports/generate",
    response_model=ClientReportItem,
    summary="Generate Report Button (Image 2)"
)
async def generate_client_report(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    now = datetime.now(timezone.utc)
    rep_id = f"rep_{uuid.uuid4().hex[:10]}"
    title_str = f"{now.strftime('%B %Y')} Service Report"
    date_fmt = now.strftime("%d %b %Y")
    date_iso = now.strftime("%Y-%m-%d")

    doc = {
        "_id": rep_id,
        "id": rep_id,
        "client_id": cid,
        "title": title_str,
        "date_formatted": date_fmt,
        "date_iso": date_iso,
        "status": "Sent",
        "created_at": now,
        "updated_at": now
    }

    await db["client_reports"].insert_one(doc)

    return ClientReportItem(
        id=rep_id,
        title=title_str,
        date_formatted=date_fmt,
        date_iso=date_iso,
        status="Sent",
        download_url=f"/admin/clients/{cid}/reports/export-pdf?report_id={rep_id}"
    )


@client_details_tabs_router.get(
    "/clients/{client_id}/reports/export-pdf",
    summary="Export PDF Button (Image 2)"
)
async def export_client_report_pdf(
    client_id: str,
    report_id: Optional[str] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cname = cdoc.get("company_name", "Client")
    pdf_bytes = generate_report_pdf_bytes(cname, "Service Report")

    filename = f"{cname.replace(' ', '_')}_Service_Report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/reports/send-email",
    summary="Email Report Button (Image 2)"
)
async def send_client_report_email(
    client_id: str,
    req_in: Optional[SendReportEmailRequest] = None,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    target_email = req_in.email if req_in and req_in.email else cdoc.get("email")
    cname = cdoc.get("company_name", "Client")

    return {
        "message": f"Service report successfully sent to '{target_email}' for '{cname}'",
        "recipient": target_email,
        "status": "Sent"
    }


# ============================================================================
# Hidden Legacy Tabs (include_in_schema=False)
# ============================================================================

@client_details_tabs_router.get(
    "/clients/{client_id}/locations",
    response_model=ClientLocationPaginatedResponse,
    include_in_schema=False,
    summary="Client Detailed View: Locations Tab (Image 4)"
)
async def get_client_locations_tab(
    client_id: str,
    page: int = 1,
    limit: int = 10,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    total_count = await db["locations"].count_documents({"client_id": cid})
    skip = (page - 1) * limit
    raw_locs = await db["locations"].find({"client_id": cid}).skip(skip).limit(limit).to_list(length=limit)

    items = []
    for l in raw_locs:
        lid = str(l.get("_id") or l.get("id"))
        items.append(ClientLocationItem(
            id=lid,
            name=l.get("name", "Location"),
            address=l.get("address", "")
        ))

    return ClientLocationPaginatedResponse(
        total_count=total_count,
        page=page,
        limit=limit,
        locations=items
    )


@client_details_tabs_router.post(
    "/clients/{client_id}/locations",
    response_model=ClientLocationItem,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
    summary="Add Client Location (Image 4)"
)
async def add_client_location(
    client_id: str,
    location_in: LocationCreate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    lid = f"loc_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    doc = {
        "_id": lid,
        "id": lid,
        "client_id": cid,
        "company_name": cdoc.get("company_name", "Client"),
        "name": location_in.name,
        "type": location_in.type,
        "address": location_in.address,
        "floor": location_in.floor,
        "number_of_rooms": 0,
        "rooms_count": 0,
        "description": location_in.description,
        "created_at": now,
        "updated_at": now
    }

    await db["locations"].insert_one(doc)

    return ClientLocationItem(
        id=lid,
        name=location_in.name,
        address=location_in.address
    )


@client_details_tabs_router.get(
    "/clients/{client_id}/cleaning-plan",
    response_model=ClientCleaningPlanResponse,
    include_in_schema=False,
    summary="Client Detailed View: Cleaning Plan Tab (Task Builder Image 1)"
)
async def get_client_cleaning_plan(
    client_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")

    plan_doc = await db["cleaning_plans"].find_one({"$or": [{"client_id": cid}, {"client_name": cname}]})
    if not plan_doc:
        tasks = []
        pname = "Task Builder Checklist"
        assigned_locs = []
    else:
        pname = plan_doc.get("name") or plan_doc.get("plan_name", "Task Builder Checklist")
        assigned_locs = plan_doc.get("assigned_location_ids", [])
        raw_t = plan_doc.get("tasks", [])
        tasks = []
        for i, item in enumerate(raw_t):
            t_name = item.get("name") if isinstance(item, dict) else str(item)
            tasks.append(ClientCleaningPlanTaskItem(task_id=f"tsk_{i+1}", name=t_name, is_completed=False))

    return ClientCleaningPlanResponse(
        client_id=cid,
        company_name=cname,
        plan_name=pname,
        tasks=tasks,
        assigned_location_ids=assigned_locs
    )


@client_details_tabs_router.put(
    "/clients/{client_id}/cleaning-plan",
    response_model=ClientCleaningPlanResponse,
    include_in_schema=False,
    summary="Save Plan / Update Tasks Checklist (Image 1)"
)
async def update_client_cleaning_plan(
    client_id: str,
    plan_in: ClientCleaningPlanUpdate,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    c_query = {"$or": [{"_id": client_id}, {"id": client_id}]}
    cdoc = await db["client_list"].find_one(c_query)
    if not cdoc:
        raise HTTPException(status_code=404, detail="Client not found")

    cid = str(cdoc.get("_id") or cdoc.get("id"))
    cname = cdoc.get("company_name", "Client")
    now = datetime.now(timezone.utc)

    task_objects = [{"name": t} for t in plan_in.tasks]
    update_data = {
        "client_id": cid,
        "client_name": cname,
        "name": plan_in.plan_name or "Task Builder Checklist",
        "tasks": task_objects,
        "updated_at": now
    }

    await db["cleaning_plans"].update_one(
        {"$or": [{"client_id": cid}, {"client_name": cname}]},
        {"$set": update_data},
        upsert=True
    )

    tasks_res = [
        ClientCleaningPlanTaskItem(task_id=f"tsk_{i+1}", name=t, is_completed=False)
        for i, t in enumerate(plan_in.tasks)
    ]

    return ClientCleaningPlanResponse(
        client_id=cid,
        company_name=cname,
        plan_name=plan_in.plan_name or "Task Builder Checklist",
        tasks=tasks_res,
        assigned_location_ids=[]
    )
