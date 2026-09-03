from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, HTTPException
from typing import Optional
from bson import ObjectId
from app.core.database import get_database
from app.dependencies.auth import get_current_user
from app.security.password import verify_password, get_password_hash
from app.models.user import UserInDB, RoleEnum
from app.schemas.client_profile_settings import (
    ClientProfileResponse, ClientProfileUpdate, ContactInformation, AccountDetails, SecurityCardInfo,
    ClientSettingsResponse, ClientSettingsUpdate, NotificationAlerts, PortalPreferences,
    ClientChangePasswordRequest
)

router = APIRouter(tags=["Client Profile and Settings"])


def require_client(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    user_role = current_user.role.value if isinstance(current_user.role, RoleEnum) else str(current_user.role)
    if user_role not in ["client", RoleEnum.client.value]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client role required")
    return current_user


# ==========================================
# 1. CLIENT PROFILE ENDPOINTS (/client/profile)
# ==========================================

@router.get("/client/profile", response_model=ClientProfileResponse, summary="Get Client Profile Details")
async def get_client_profile(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)

    c_doc = await db["client_list"].find_one({"$or": [{"_id": user_id}, {"id": user_id}, {"email": current_user.email}]})

    client_id_val = f"C-{user_id}"
    if c_doc:
        cid = str(c_doc.get("_id") or c_doc.get("id") or user_id)
        client_id_val = cid if (cid.startswith("C-") or cid.startswith("#")) else f"C-{cid}"

    company_name = getattr(current_user, "company_name", None) or (c_doc.get("company_name") if c_doc else None) or "N/A"
    contact_person = getattr(current_user, "full_name", None) or (c_doc.get("primary_contact_name") if c_doc else None) or (c_doc.get("contact_person") if c_doc else None) or "N/A"

    email_addr = current_user.email or (c_doc.get("email") if c_doc else "N/A")
    phone_num = (
        getattr(current_user, "phone", None) or
        getattr(current_user, "phone_number", None) or
        (c_doc.get("phone") if c_doc else None) or
        "N/A"
    )

    created_at_dt = getattr(current_user, "created_at", None) or (c_doc.get("created_at") if c_doc else None)
    if isinstance(created_at_dt, datetime):
        member_since = created_at_dt.strftime("%b %Y")
    elif isinstance(created_at_dt, str):
        member_since = created_at_dt[:7]
    else:
        member_since = getattr(current_user, "member_since", None) or "N/A"

    contract_type = getattr(current_user, "contract_type", None)
    if not contract_type and c_doc:
        contracts = c_doc.get("contracts", [])
        if contracts and isinstance(contracts[0], dict):
            contract_type = contracts[0].get("contract_type") or contracts[0].get("type")
        contract_type = contract_type or c_doc.get("contract_type")
    contract_type = contract_type or "Standard Contract"

    account_status = getattr(current_user, "account_status", None) or (c_doc.get("status") if c_doc else None) or ("active" if getattr(current_user, "is_active", True) else "inactive")

    last_pw_dt = getattr(current_user, "last_password_changed_at", None)
    if last_pw_dt and isinstance(last_pw_dt, datetime):
        days_ago = (datetime.now(timezone.utc) - last_pw_dt.replace(tzinfo=timezone.utc)).days
        last_changed_str = f"Last changed {days_ago} days ago" if days_ago > 0 else "Last changed today"
    else:
        last_changed_str = "Password set at registration"

    return ClientProfileResponse(
        full_name=contact_person,
        account_type="Client Account",
        company_name=company_name,
        profile_picture=getattr(current_user, "profile_photo", None),
        contact_information=ContactInformation(
            company_name=company_name,
            contact_person=contact_person,
            email_address=email_addr,
            phone_number=phone_num
        ),
        account_details=AccountDetails(
            client_id=client_id_val,
            member_since=member_since,
            contract_type=contract_type,
            account_status=account_status
        ),
        security_info=SecurityCardInfo(
            last_password_changed=last_changed_str
        )
    )


@router.patch("/client/profile", response_model=ClientProfileResponse, summary="Update Client Profile Info")
async def update_client_profile(
    profile_in: ClientProfileUpdate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)
    obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

    update_user_fields = {}
    update_client_list_fields = {}

    if profile_in.company_name is not None:
        update_user_fields["company_name"] = profile_in.company_name
        update_client_list_fields["company_name"] = profile_in.company_name

    if profile_in.contact_person is not None:
        update_user_fields["full_name"] = profile_in.contact_person
        update_client_list_fields["contact_person"] = profile_in.contact_person

    if profile_in.contact_email is not None:
        update_user_fields["email"] = profile_in.contact_email
        update_client_list_fields["email"] = profile_in.contact_email

    if profile_in.phone_number is not None:
        update_user_fields["phone"] = profile_in.phone_number
        update_user_fields["phone_number"] = profile_in.phone_number
        update_client_list_fields["phone"] = profile_in.phone_number

    if profile_in.profile_picture is not None:
        update_user_fields["profile_photo"] = profile_in.profile_picture

    if update_user_fields:
        update_user_fields["updated_at"] = datetime.now(timezone.utc)
        await db["users"].update_one(
            {"$or": [{"_id": obj_id}, {"id": user_id}]},
            {"$set": update_user_fields}
        )

    if update_client_list_fields:
        update_client_list_fields["updated_at"] = datetime.now(timezone.utc)
        await db["client_list"].update_many(
            {"$or": [{"_id": user_id}, {"id": user_id}, {"email": current_user.email}]},
            {"$set": update_client_list_fields}
        )

    updated_user_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": user_id}]})
    if updated_user_doc and "_id" in updated_user_doc:
        updated_user_doc["_id"] = str(updated_user_doc["_id"])
    updated_user = UserInDB(**updated_user_doc) if updated_user_doc else current_user
    return await get_client_profile(current_user=updated_user)


# ==========================================
# 2. CLIENT SETTINGS ENDPOINTS (/client/settings)
# ==========================================

@router.get("/client/settings", response_model=ClientSettingsResponse, summary="Get Client Settings & Preferences")
async def get_client_settings(
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)
    obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

    user_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": user_id}]}) or {}

    profile_data = await get_client_profile(current_user=current_user)

    email_notifs = user_doc.get("email_notifications", getattr(current_user, "email_notifications", True))
    sms_alerts = user_doc.get("sms_cleaning_alerts", getattr(current_user, "sms_cleaning_alerts", False))
    language = user_doc.get("portal_language", getattr(current_user, "portal_language", "English (US)"))

    return ClientSettingsResponse(
        profile_settings={
            "company_name": profile_data.contact_information.company_name,
            "client_number": profile_data.account_details.client_id,
            "contact_email": profile_data.contact_information.email_address,
            "phone_number": profile_data.contact_information.phone_number
        },
        notification_alerts=NotificationAlerts(
            email_notifications=bool(email_notifs),
            email_notifications_description="Receive summary reports after visits",
            sms_cleaning_alerts=bool(sms_alerts),
            sms_cleaning_alerts_description="Get texts when cleaning sessions start"
        ),
        portal_preferences=PortalPreferences(
            portal_language=str(language)
        )
    )


@router.patch("/client/settings", response_model=ClientSettingsResponse, summary="Update Notification & Portal Settings")
async def update_client_settings(
    settings_in: ClientSettingsUpdate,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)
    obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

    update_fields = {}
    if settings_in.email_notifications is not None:
        update_fields["email_notifications"] = settings_in.email_notifications
    if settings_in.sms_cleaning_alerts is not None:
        update_fields["sms_cleaning_alerts"] = settings_in.sms_cleaning_alerts
    if settings_in.portal_language is not None:
        update_fields["portal_language"] = settings_in.portal_language

    if update_fields:
        update_fields["updated_at"] = datetime.now(timezone.utc)
        await db["users"].update_one(
            {"$or": [{"_id": obj_id}, {"id": user_id}]},
            {"$set": update_fields}
        )

    updated_user_doc = await db["users"].find_one({"$or": [{"_id": obj_id}, {"id": user_id}]})
    if updated_user_doc and "_id" in updated_user_doc:
        updated_user_doc["_id"] = str(updated_user_doc["_id"])
    updated_user = UserInDB(**updated_user_doc) if updated_user_doc else current_user
    return await get_client_settings(current_user=updated_user)


@router.post("/client/settings/change-password", summary="Update Password (Security & Authentication)", include_in_schema=False)
async def change_client_password(
    pwd_in: ClientChangePasswordRequest,
    current_user: UserInDB = Depends(require_client)
):
    db = get_database()
    user_id = str(current_user.id or current_user.mongo_id)
    obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

    if not verify_password(pwd_in.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = get_password_hash(pwd_in.new_password)
    now = datetime.now(timezone.utc)

    await db["users"].update_one(
        {"$or": [{"_id": obj_id}, {"id": user_id}]},
        {"$set": {
            "hashed_password": new_hash,
            "is_temporary_password": False,
            "last_password_changed_at": now,
            "updated_at": now
        }}
    )

    return {"message": "Password updated successfully"}
