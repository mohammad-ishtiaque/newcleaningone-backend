import csv
import io
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Any
from app.schemas.client_list import LocationBulkImportResult

CSV_HEADERS = [
    "location_name",
    "client_company_name",
    "address",
    "floors",
    "rooms",
    "required_hours_per_month"
]

SAMPLE_ROWS = [
    ["NH Hotel Amsterdam Centrum", "NH Hotels Nederland", "Stadhouderskade 7, Amsterdam", "4", "48", "240"],
    ["Hilton Rotterdam", "Kantoorschoonmaak Rotterdam", "Weena 10, Rotterdam", "3", "36", "192"],
    ["Zorg & Schoon - UMC Utrecht", "Zorg & Schoon Utrecht", "Heidelberglaan 100, Utrecht", "15", "120", "304"]
]

def generate_location_csv_template() -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for row in SAMPLE_ROWS:
        writer.writerow(row)
    return output.getvalue()

async def parse_and_validate_location_csv(content: str, db) -> LocationBulkImportResult:
    stream = io.StringIO(content)
    reader = csv.DictReader(stream)

    if not reader.fieldnames:
        return LocationBulkImportResult(total_rows=0, imported_count=0, failed_count=1, errors=["Empty CSV file"])

    imported = 0
    failed = 0
    errors = []
    now = datetime.now(timezone.utc)

    rows = list(reader)
    total_rows = len(rows)

    for i, row in enumerate(rows, start=2):
        loc_name = (row.get("location_name") or row.get("name") or "").strip()
        c_name = (row.get("client_company_name") or row.get("client") or "").strip()
        address = (row.get("address") or "").strip()

        if not loc_name:
            failed += 1
            errors.append(f"Line {i}: Missing location_name")
            continue

        if not address:
            failed += 1
            errors.append(f"Line {i}: Missing address for location '{loc_name}'")
            continue

        # Find or link client
        client_id = f"cli_{uuid.uuid4().hex[:10]}"
        if c_name:
            cdoc = await db["client_list"].find_one({"company_name": {"$regex": f"^{c_name}$", "$options": "i"}})
            if cdoc:
                client_id = str(cdoc.get("_id") or cdoc.get("id"))
            else:
                new_c = {
                    "_id": client_id,
                    "id": client_id,
                    "company_name": c_name,
                    "industry": "Corporate",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now
                }
                await db["client_list"].insert_one(new_c)

        try:
            floors = int(row.get("floors") or 1)
        except ValueError:
            floors = 1

        try:
            rooms = int(row.get("rooms") or 1)
        except ValueError:
            rooms = 1

        try:
            req_hours = float(row.get("required_hours_per_month") or row.get("required_hours") or 0.0)
        except ValueError:
            req_hours = 0.0

        loc_id = f"loc_{uuid.uuid4().hex[:10]}"
        loc_doc = {
            "_id": loc_id,
            "id": loc_id,
            "client_id": client_id,
            "name": loc_name,
            "address": address,
            "floor": floors,
            "number_of_floors": floors,
            "number_of_rooms": rooms,
            "rooms_count": rooms,
            "required_hours_per_month": req_hours,
            "is_active": True,
            "assigned_worker_ids": [],
            "created_at": now,
            "updated_at": now
        }

        await db["locations"].insert_one(loc_doc)
        imported += 1

    return LocationBulkImportResult(
        total_rows=total_rows,
        imported_count=imported,
        failed_count=failed,
        errors=errors
    )

def export_locations_to_csv(locations_data: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)

    for l in locations_data:
        writer.writerow([
            l.get("location_name", ""),
            l.get("client_company_name", ""),
            l.get("address", ""),
            l.get("floors", 1),
            l.get("rooms", 1),
            l.get("required_hours_numeric", 0.0)
        ])

    return output.getvalue()
