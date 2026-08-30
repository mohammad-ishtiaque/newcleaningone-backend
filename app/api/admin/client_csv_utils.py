import csv
import io
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Any
from app.schemas.client_list import ClientBulkImportResult

CLIENT_CSV_HEADERS = [
    "company_name",
    "primary_contact_name",
    "email",
    "phone",
    "industry",
    "address",
    "city",
    "country",
    "postal_code"
]

SAMPLE_CLIENT_ROWS = [
    ["NH Hotels Nederland", "Sophie van Dijk", "sophie@nhhotels.nl", "+31205551234", "Hospitality", "Stadhouderskade 7", "Amsterdam", "Netherlands", "1071 ZD"],
    ["Betopia Tech Group", "Lars de Boer", "lars@betopia.nl", "+31105555678", "Technology", "Weena 505", "Rotterdam", "Netherlands", "3013 AL"],
    ["Medical Care Utrecht", "Anouk Bakker", "anouk@medcare.nl", "+31305559012", "Healthcare", "Heidelberglaan 100", "Utrecht", "Netherlands", "3584 CX"]
]

def generate_client_csv_template() -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CLIENT_CSV_HEADERS)
    for row in SAMPLE_CLIENT_ROWS:
        writer.writerow(row)
    return output.getvalue()

async def parse_and_validate_client_csv(content: str, db) -> ClientBulkImportResult:
    stream = io.StringIO(content)
    reader = csv.DictReader(stream)

    if not reader.fieldnames:
        return ClientBulkImportResult(total_rows=0, imported_count=0, failed_count=1, errors=["Empty CSV file"])

    imported = 0
    failed = 0
    errors = []
    now = datetime.now(timezone.utc)

    rows = list(reader)
    total_rows = len(rows)

    for i, row in enumerate(rows, start=2):
        company_name = (row.get("company_name") or row.get("company") or "").strip()
        contact_name = (row.get("primary_contact_name") or row.get("contact_name") or row.get("name") or "Contact Person").strip()
        email = (row.get("email") or "").strip().lower()
        phone = (row.get("phone") or row.get("phone_number") or "").strip()
        industry = (row.get("industry") or "Corporate").strip()
        address = (row.get("address") or "").strip()
        city = (row.get("city") or "Amsterdam").strip()
        country = (row.get("country") or "Netherlands").strip()
        postal_code = (row.get("postal_code") or "").strip()

        if not company_name:
            failed += 1
            errors.append(f"Line {i}: Missing company_name")
            continue

        if not email:
            failed += 1
            errors.append(f"Line {i}: Missing email for '{company_name}'")
            continue

        existing_c = await db["client_list"].find_one({"$or": [{"email": email}, {"company_name": company_name}]})
        if existing_c:
            failed += 1
            errors.append(f"Line {i}: Client with email '{email}' or company '{company_name}' already exists")
            continue

        cid = f"client_{uuid.uuid4().hex[:10]}"
        client_doc = {
            "_id": cid,
            "id": cid,
            "company_name": company_name,
            "primary_contact_name": contact_name,
            "email": email,
            "phone": phone,
            "industry": industry,
            "address": address,
            "city": city,
            "country": country,
            "postal_code": postal_code,
            "status": "active",
            "is_active": True,
            "is_signup": True,
            "contract_status": "active",
            "total_locations_count": 0,
            "created_at": now,
            "updated_at": now
        }
        await db["client_list"].insert_one(client_doc)
        imported += 1

    return ClientBulkImportResult(
        total_rows=total_rows,
        imported_count=imported,
        failed_count=failed,
        errors=errors
    )

def export_clients_to_csv(clients: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CLIENT_CSV_HEADERS)
    for c in clients:
        writer.writerow([
            c.get("company_name", ""),
            c.get("primary_contact_name", ""),
            c.get("email", ""),
            c.get("phone", ""),
            c.get("industry", ""),
            c.get("address", ""),
            c.get("city", ""),
            c.get("country", ""),
            c.get("postal_code", "")
        ])
    return output.getvalue()
