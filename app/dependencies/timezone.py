from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import Request, Query, Header
from app.core.timezone_utils import resolve_timezone_from_request, get_timezone


def get_request_timezone(
    request: Request,
    tz: Optional[str] = Query(None, description="Optional IANA timezone name (e.g., 'Europe/Amsterdam', 'Asia/Dhaka', 'UTC')"),
    timezone_param: Optional[str] = Query(None, alias="timezone", description="Optional timezone query parameter"),
    x_timezone: Optional[str] = Header(None, alias="X-Timezone", description="Client timezone header (e.g. 'Europe/Amsterdam')")
) -> ZoneInfo:
    """
    FastAPI dependency to resolve client timezone from query param, X-Timezone header, or server default.
    """
    chosen = tz or timezone_param or x_timezone
    return resolve_timezone_from_request(request=request, explicit_tz=chosen)
