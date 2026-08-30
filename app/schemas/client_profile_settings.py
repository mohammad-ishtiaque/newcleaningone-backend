from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List

class ContactInformation(BaseModel):
    company_name: str
    contact_person: str
    email_address: str
    phone_number: str

class AccountDetails(BaseModel):
    client_id: str
    member_since: str
    contract_type: str
    account_status: str

class SecurityCardInfo(BaseModel):
    last_password_changed: str

class ClientProfileSettingsResponse(BaseModel):
    full_name: str
    account_type: str = "Client Account"
    company_name: str
    profile_picture: Optional[str] = None
    contact_information: ContactInformation
    account_details: AccountDetails
    security_info: SecurityCardInfo

ClientProfileResponse = ClientProfileSettingsResponse

class ClientProfileUpdate(BaseModel):
    company_name: Optional[str] = Field(None, json_schema_extra={"example": "Apex Tech Ltd."})
    contact_person: Optional[str] = Field(None, json_schema_extra={"example": "Alexandra Thompson"})
    contact_email: Optional[str] = Field(None, json_schema_extra={"example": "apex@company.com"})
    phone_number: Optional[str] = Field(None, json_schema_extra={"example": "+1 (555) 019-2834"})
    profile_picture: Optional[str] = None

class NotificationAlerts(BaseModel):
    email_notifications: bool = True
    email_notifications_description: str = "Receive summary reports after visits"
    sms_cleaning_alerts: bool = False
    sms_cleaning_alerts_description: str = "Get texts when cleaning sessions start"

class PortalPreferences(BaseModel):
    portal_language: str = "English (US)"

class ClientSettingsResponse(BaseModel):
    profile_settings: dict
    notification_alerts: NotificationAlerts
    portal_preferences: PortalPreferences

class ClientSettingsUpdate(BaseModel):
    email_notifications: Optional[bool] = None
    sms_cleaning_alerts: Optional[bool] = None
    portal_language: Optional[str] = Field(None, json_schema_extra={"example": "English (US)"})

class ClientChangePasswordRequest(BaseModel):
    current_password: str = Field(..., json_schema_extra={"example": "OldPassword123!"})
    new_password: str = Field(..., json_schema_extra={"example": "NewStrongPassword456!"})
