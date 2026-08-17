import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import List, Optional
from app.core.database import get_database
from app.schemas.client_list import (
    LocationDrawerOverviewResponse, AssignedEmployeeItem,
    LocationDrawerRoomsResponse, LocationDrawerRoomItem
)
from app.models.user import UserInDB
from app.api.admin.profile_company import require_manager

location_drawer_router = APIRouter(prefix="/manager", tags=["Manager Location Management"])

@location_drawer_router.get("/locations/{location_id}/overview", response_model=LocationDrawerOverviewResponse, summary="Location Details Drawer: Overview Tab (Image 4)")
async def get_location_drawer_overview(
    location_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    ldoc = await db["locations"].find_one(l_query)
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    lid = str(ldoc.get("_id") or ldoc.get("id"))
    lname = ldoc.get("name", "Location")
    cid = ldoc.get("client_id", "")
    cdoc = await db["client_list"].find_one({"$or": [{"_id": cid}, {"id": cid}]}) if cid else None
    cname = cdoc.get("company_name", "Client Company") if cdoc else ldoc.get("company_name", "Client Company")

    address = ldoc.get("address", "")
    floors = ldoc.get("number_of_floors") or ldoc.get("floor") or 1
    rooms_count = await db["rooms"].count_documents({"location_id": lid})
    if rooms_count == 0:
        rooms_count = ldoc.get("number_of_rooms") or ldoc.get("rooms_count") or 0

    req_hours_val = ldoc.get("required_hours_per_month", 0.0)
    req_hours_str = f"{int(req_hours_val)}h" if req_hours_val > 0 else "0h"

    code = f"L{lid[-3:].upper()}" if len(lid) >= 3 else "L001"
    sub_title = f"{cname} • {code}"

    # Query assigned employees
    assigned_worker_ids = ldoc.get("assigned_worker_ids", [])
    assigned_employees = []

    if assigned_worker_ids:
        workers = await db["users"].find({"$or": [{"_id": {"$in": assigned_worker_ids}}, {"id": {"$in": assigned_worker_ids}}]}).to_list(length=50)
        for w in workers:
            wid = str(w.get("_id") or w.get("id"))
            assigned_employees.append(AssignedEmployeeItem(
                worker_id=wid,
                name=w.get("full_name", "Worker"),
                profile_picture=w.get("profile_photo"),
                status="Assigned"
            ))

    cov_label = f"{req_hours_str} required hours per month"

    return LocationDrawerOverviewResponse(
        location_id=lid,
        location_code=code,
        location_name=lname,
        client_name=cname,
        subtitle=sub_title,
        address=address,
        floors=floors,
        rooms=rooms_count,
        required_hours_month=req_hours_str,
        assigned_employees=assigned_employees,
        assigned_employees_count=len(assigned_employees),
        coverage_label=cov_label
    )

@location_drawer_router.get("/locations/{location_id}/rooms-tab", response_model=LocationDrawerRoomsResponse, summary="Location Details Drawer: Rooms Tab (Image 5)")
async def get_location_drawer_rooms(
    location_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    ldoc = await db["locations"].find_one(l_query)
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    lid = str(ldoc.get("_id") or ldoc.get("id"))
    lname = ldoc.get("name", "Location")

    raw_rooms = await db["rooms"].find({"location_id": lid}).sort("floor", 1).to_list(length=100)

    room_items = []
    for r in raw_rooms:
        rid = str(r.get("_id") or r.get("id"))
        fl = r.get("floor", 1)
        rname = r.get("name") or r.get("room_name") or f"Room {len(room_items)+1}"
        ctype = r.get("clean_type") or r.get("cleaning_type", "Standard cleaning")
        clean_label = "Daily priority" if "priority" in ctype.lower() else "Standard cleaning"

        room_items.append(LocationDrawerRoomItem(
            room_id=rid,
            title=f"Floor {fl} • {rname}",
            floor=fl,
            cleaning_type_label=clean_label,
            status="Active"
        ))

    return LocationDrawerRoomsResponse(
        location_id=lid,
        location_name=lname,
        total_rooms_count=len(room_items),
        rooms=room_items
    )

@location_drawer_router.get("/locations/{location_id}/cleaning-plans-tab", summary="Location Details Drawer: Cleaning Plans Tab (Image 4)")
async def get_location_drawer_cleaning_plans(
    location_id: str,
    current_user: UserInDB = Depends(require_manager)
):
    db = get_database()
    l_query = {"$or": [{"_id": location_id}, {"id": location_id}]}
    ldoc = await db["locations"].find_one(l_query)
    if not ldoc:
        raise HTTPException(status_code=404, detail="Location not found")

    lid = str(ldoc.get("_id") or ldoc.get("id"))
    lname = ldoc.get("name", "Location")

    raw_plans = await db["cleaning_plans"].find({"$or": [{"location_id": lid}, {"location_id": location_id}]}).to_list(length=50)

    items = []
    for p in raw_plans:
        pid = str(p.get("_id") or p.get("id"))
        items.append({
            "plan_id": pid,
            "title": p.get("title") or p.get("plan_name") or p.get("name", "Cleaning Plan"),
            "subtitle": p.get("description") or f"{p.get('rooms_count', 0)} rooms",
            "status": "Active"
        })

    return {
        "location_id": lid,
        "location_name": lname,
        "total_plans_count": len(items),
        "plans": items
    }


