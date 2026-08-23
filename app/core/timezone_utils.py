from datetime import datetime, timezone, timedelta
from typing import Optional, Union, Any, Dict
from zoneinfo import ZoneInfo
import re
from app.core.config import settings


def parse_time_to_minutes(time_str: str) -> int:
    """Parses '08:00 AM', '02:30 PM', or '14:00' to minutes from midnight."""
    if not time_str:
        return 480  # Default 8:00 AM
    clean_str = time_str.strip().upper()
    try:
        match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", clean_str)
        if match:
            h, m, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
            if meridiem:
                if meridiem == "PM" and h < 12:
                    h += 12
                elif meridiem == "AM" and h == 12:
                    h = 0
            return h * 60 + m
        parts = clean_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 480


def get_timezone(tz_identifier: Optional[Union[str, ZoneInfo]] = None) -> ZoneInfo:
    """
    Returns a valid ZoneInfo instance.
    Supports IANA names (e.g., 'Europe/Amsterdam', 'Asia/Dhaka', 'UTC')
    and common offset formats (e.g. '+06:00', 'UTC+6', 'GMT+6').
    Falls back gracefully to settings.DEFAULT_TIMEZONE or UTC.
    """
    if isinstance(tz_identifier, ZoneInfo):
        return tz_identifier

    default_tz_name = getattr(settings, "DEFAULT_TIMEZONE", "Europe/Amsterdam") or "Europe/Amsterdam"

    if not tz_identifier or not str(tz_identifier).strip():
        try:
            return ZoneInfo(default_tz_name)
        except Exception:
            return ZoneInfo("UTC")

    raw = str(tz_identifier).strip()

    # Direct IANA check
    try:
        return ZoneInfo(raw)
    except Exception:
        pass

    # Normalize common abbreviations / offsets
    upper_raw = raw.upper()
    if upper_raw in ("UTC", "GMT", "Z"):
        return ZoneInfo("UTC")

    # Offset like +06:00, -05:00, UTC+6, GMT+06:00
    offset_match = re.match(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$", upper_raw)
    if offset_match:
        sign = 1 if offset_match.group(1) == "+" else -1
        hours = int(offset_match.group(2))
        mins = int(offset_match.group(3) or 0)
        # Construct timezone with fixed offset
        delta = timedelta(minutes=sign * (hours * 60 + mins))
        # Find closest standard IANA or return fixed offset timezone
        return ZoneInfo("UTC") if delta == timedelta(0) else timezone(delta)  # type: ignore

    # Fallback to configured default timezone
    try:
        return ZoneInfo(default_tz_name)
    except Exception:
        return ZoneInfo("UTC")


def now_in_tz(tz: Optional[Union[str, ZoneInfo]] = None) -> datetime:
    """Returns timezone-aware datetime.now() in the requested timezone."""
    zone = get_timezone(tz)
    return datetime.now(zone)


def get_today_str(tz: Optional[Union[str, ZoneInfo]] = None) -> str:
    """Returns 'YYYY-MM-DD' representing today in the requested timezone."""
    return now_in_tz(tz).strftime("%Y-%m-%d")


def parse_plan_start_datetime(
    date_str: str,
    time_str: str,
    tz: Optional[Union[str, ZoneInfo]] = None
) -> datetime:
    """
    Returns timezone-aware datetime object for shift start localized in the target timezone.
    """
    zone = get_timezone(tz)
    try:
        y, mon, d = map(int, date_str.strip().split("-"))
        total_mins = parse_time_to_minutes(time_str)
        hour = total_mins // 60
        minute = total_mins % 60
        return datetime(y, mon, d, hour, minute, tzinfo=zone)
    except Exception:
        return now_in_tz(zone)


def human_time_until(target_dt: datetime, from_dt: Optional[datetime] = None) -> str:
    """
    Calculates human-readable time until target_dt (e.g., 'Starting today', 'In 2 hour(s)').
    Both datetimes are converted to timezone-aware if needed.
    """
    if from_dt is None:
        from_dt = datetime.now(target_dt.tzinfo or timezone.utc)
    elif from_dt.tzinfo is None and target_dt.tzinfo is not None:
        from_dt = from_dt.replace(tzinfo=target_dt.tzinfo)

    mins_until = int((target_dt - from_dt).total_seconds() // 60)
    if mins_until <= 0:
        return "Starting today"
    elif mins_until < 60:
        return f"In {mins_until} mins"
    elif mins_until < 1440:
        hours = mins_until // 60
        return f"In {hours} hour(s)"
    else:
        days = mins_until // 1440
        return f"In {days} day(s)"


def resolve_timezone_from_request(
    request: Any = None,
    explicit_tz: Optional[str] = None,
    user_doc: Optional[Dict[str, Any]] = None,
    entity_doc: Optional[Dict[str, Any]] = None
) -> ZoneInfo:
    """
    Resolves timezone in priority order:
    1. explicit_tz (query parameter)
    2. Request Header 'X-Timezone' or 'X-Time-Zone'
    3. entity_doc['timezone'] (e.g. shift, cleaning plan, location)
    4. user_doc['timezone'] (user profile)
    5. settings.DEFAULT_TIMEZONE
    """
    if explicit_tz and str(explicit_tz).strip():
        return get_timezone(explicit_tz)

    if request is not None and hasattr(request, "headers"):
        header_tz = request.headers.get("X-Timezone") or request.headers.get("X-Time-Zone")
        if header_tz and str(header_tz).strip():
            return get_timezone(header_tz)

    if entity_doc and entity_doc.get("timezone"):
        return get_timezone(entity_doc.get("timezone"))

    if user_doc and user_doc.get("timezone"):
        return get_timezone(user_doc.get("timezone"))

    return get_timezone(getattr(settings, "DEFAULT_TIMEZONE", "Europe/Amsterdam"))
