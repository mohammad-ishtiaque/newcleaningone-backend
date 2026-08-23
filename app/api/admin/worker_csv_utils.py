import csv
import io
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
from app.security.password import get_password_hash
from app.schemas.user import WorkerBulkImportResult

CSV_HEADERS = ["full_name", "email", "phone", "worker_type", "position", "base_location", "languages", "status"]

def generate_csv_template() -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    writer.writerow(["Lisa Visser", "lisa.visser@cleanones.com", "+31 20 000 0001", "employee", "Senior Cleaner", "Amsterdam-Centrum", "Nederlands;English", "active"])
    writer.writerow(["Emma Smit", "emma.smit@cleanones.com", "+31 20 000 0002", "employee", "Senior Cleaner", "Rotterdam-Noord", "Nederlands;English", "active"])
    writer.writerow(["Noah Bos", "noah.bos@cleanones.com", "+31 20 000 0003", "freelancer", "Cleaner", "Groningen-Centrum", "Nederlands;Engels", "active"])
    return output.getvalue()

async def parse_and_validate_worker_csv(file_bytes: bytes, db) -> WorkerBulkImportResult:
    text = file_bytes.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    total_rows = 0
    imported_count = 0
    failed_count = 0
    errors = []

    default_hashed_pwd = get_password_hash("WorkerPass123!")

    for row_idx, row in enumerate(reader, start=2):
        total_rows += 1
        full_name = (row.get("full_name") or row.get("name") or "").strip()
        email = (row.get("email") or "").strip().lower()
        phone = (row.get("phone") or "").strip()
        worker_type = (row.get("worker_type") or "employee").strip().lower()
        position = (row.get("position") or "Cleaner").strip()
        base_location = (row.get("base_location") or row.get("location") or "Amsterdam-Centrum").strip()
        languages_raw = row.get("languages") or "Nederlands;English"
        status_val = (row.get("status") or "active").strip().lower()

        if not full_name:
            failed_count += 1
            errors.append(f"Row {row_idx}: Missing full_name")
            continue

        if not email or "@" not in email:
            failed_count += 1
            errors.append(f"Row {row_idx}: Invalid or missing email '{email}'")
            continue

        # Check existing user
        existing = await db["users"].find_one({"email": email})
        if existing:
            failed_count += 1
            errors.append(f"Row {row_idx}: Worker email '{email}' already exists")
            continue

        # Parse languages
        lang_list = [l.strip() for l in languages_raw.replace(",", ";").split(";") if l.strip()]
        if not lang_list:
            lang_list = ["Nederlands", "English"]

        if worker_type not in ["employee", "freelancer"]:
            worker_type = "employee"

        is_active = (status_val != "suspended" and status_val != "banned")

        now = datetime.now(timezone.utc)
        user_doc = {
            "full_name": full_name,
            "email": email,
            "phone": phone or "+31 20 000 0000",
            "hashed_password": default_hashed_pwd,
            "role": "worker",
            "worker_type": worker_type,
            "position": position,
            "location": base_location,
            "base_location": base_location,
            "languages": lang_list,
            "account_status": status_val if status_val in ["active", "suspended", "banned"] else "active",
            "approval_status": "approved",
            "is_approved": True,
            "is_admin_created": True,
            "is_temporary_password": True,
            "temporary_password_created_at": now,
            "is_active": is_active,
            "created_at": now,
            "updated_at": now
        }

        await db["users"].insert_one(user_doc)

        # Sync pre-creation entry in admin_workers collection
        await db["admin_workers"].update_one(
            {"email": email},
            {"$set": {
                "name": full_name,
                "email": email,
                "phone": phone or "+31 20 000 0000",
                "worker_type": worker_type,
                "position": position,
                "base_location": base_location,
                "languages": lang_list,
                "created_at": now
            }},
            upsert=True
        )
        imported_count += 1

    return WorkerBulkImportResult(
        total_rows=total_rows,
        imported_count=imported_count,
        failed_count=failed_count,
        errors=errors
    )

def export_workers_to_csv(workers: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)

    export_headers = ["worker_id", "full_name", "email", "phone", "worker_type", "position", "location", "languages", "hours_worked", "status", "approval_status"]
    writer.writerow(export_headers)

    for w in workers:
        wid = str(w.get("_id") or w.get("id"))
        wname = w.get("full_name", "")
        email = w.get("email", "")
        phone = w.get("phone", "")
        wtype = w.get("worker_type", "employee")
        pos = w.get("position", "")
        loc = w.get("location") or w.get("base_location", "")
        langs = ";".join(w.get("languages", []))
        hw = f"{w.get('hours_worked', 0)}h"
        st = w.get("status", "Active")
        appr = w.get("approval_status", "approved")

        writer.writerow([wid, wname, email, phone, wtype, pos, loc, langs, hw, st, appr])

    return output.getvalue()
