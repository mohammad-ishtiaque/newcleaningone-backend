"""
Worker hourly rate — single source of truth.

`hourly_rate` (float, EUR per hour) is the ONE canonical salary field across the
whole system: every request body, every response body, the `users` collection,
`admin_workers`, the embedded `shifts.workers_list[]` / `assigned_workers[]`
records and the invoice documents.

The legacy integer field `per_hour_salary` was removed. It used to be written
alongside `hourly_rate` as a rounded duplicate, which meant a rate of 25.50 was
displayed as 26 by any reader that happened to prefer the integer field while
payroll still paid 25.50. Nothing writes `per_hour_salary` any more.

Documents persisted before the removal may still carry it, so
`resolve_hourly_rate()` reads it as a fallback. That fallback is the only place
in the codebase that is allowed to mention the legacy field.

Read vs. write, deliberately asymmetric:

* **Write** (`validate_hourly_rate`) enforces the business bounds. A request
  outside them is rejected with a 422 rather than silently clamped.
* **Read** (`resolve_hourly_rate`) returns whatever is stored, even if it now
  falls outside those bounds. Clamping a stored value would silently restate
  what a worker was actually paid, so historical documents are reported
  faithfully. Only unusable values (missing, zero, negative, non-numeric)
  fall through to `default`.
"""

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

# Business bounds, in EUR per hour.
DEFAULT_HOURLY_RATE: float = 25.0
MIN_HOURLY_RATE: float = 0.01
MAX_HOURLY_RATE: float = 1000.0

CANONICAL_RATE_FIELD = "hourly_rate"
LEGACY_RATE_FIELD = "per_hour_salary"

#: Shown to any caller still sending the removed field, so an outdated client
#: fails loudly instead of silently falling back to DEFAULT_HOURLY_RATE and
#: paying the worker the wrong amount.
LEGACY_FIELD_ERROR = (
    "'per_hour_salary' has been removed. Send 'hourly_rate' instead "
    "(number, EUR per hour, decimals allowed, e.g. 25.5)."
)

RATE_FIELD_DESCRIPTION = (
    "Worker pay rate in EUR per hour. Decimals are allowed (e.g. 25.5 for "
    f"EUR 25.50/hour). Must be greater than 0 and at most {MAX_HOURLY_RATE:g}. "
    "Stored to 2 decimal places. This is the only salary field — the former "
    "`per_hour_salary` was removed and is rejected with a 422."
)


def coerce_hourly_rate(value: Any) -> Optional[float]:
    """
    Best-effort conversion of a stored or inbound value to a usable rate.

    Returns ``None`` — never raises — for anything that cannot serve as a rate:
    ``None``, booleans, non-numeric strings, NaN/infinity, and values <= 0.
    Zero and negatives are treated as "not set" because that is how the
    pre-consolidation code behaved (it used ``or`` chains) and a zero rate is
    not a real salary.

    Handles the shapes that actually reach us: int and float from JSON, str
    from CSV import, ``Decimal``, and BSON ``Decimal128`` (duck-typed via
    ``to_decimal`` so this module stays dependency-free).
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, Decimal):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        # BSON Decimal128 and anything else exposing a Decimal conversion.
        to_decimal = getattr(value, "to_decimal", None)
        if not callable(to_decimal):
            return None
        try:
            number = float(to_decimal())
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            return None

    if not math.isfinite(number) or number <= 0:
        return None

    return round(number, 2)


def resolve_hourly_rate(
    doc: Optional[Mapping[str, Any]],
    default: float = DEFAULT_HOURLY_RATE,
) -> float:
    """
    Read the hourly rate off a Mongo document (or any mapping).

    Prefers the canonical ``hourly_rate``, falls back to the legacy
    ``per_hour_salary`` for documents written before the consolidation, and
    finally to ``default``. Safe against a missing document, a ``None``
    document and a document holding garbage in either field.
    """
    if not isinstance(doc, Mapping):
        return default

    rate = coerce_hourly_rate(doc.get(CANONICAL_RATE_FIELD))
    if rate is None:
        rate = coerce_hourly_rate(doc.get(LEGACY_RATE_FIELD))
    return rate if rate is not None else default


def validate_hourly_rate(value: Any) -> float:
    """
    Validate an inbound ``hourly_rate`` for a request body.

    Raises ``ValueError`` on a bad value; inside a Pydantic validator that
    surfaces to the caller as a 422 with this message. Use only on the write
    path — reads go through :func:`resolve_hourly_rate`.
    """
    rate = coerce_hourly_rate(value)
    if rate is None:
        raise ValueError(
            "hourly_rate must be a number greater than 0 "
            "(EUR per hour, e.g. 25.5)."
        )
    if rate < MIN_HOURLY_RATE or rate > MAX_HOURLY_RATE:
        raise ValueError(
            f"hourly_rate must be between {MIN_HOURLY_RATE:g} and "
            f"{MAX_HOURLY_RATE:g} EUR per hour, got {rate:g}."
        )
    return rate


def reject_legacy_salary_field(data: Any) -> Any:
    """
    Pydantic ``mode="before"`` model validator hook.

    An outdated client sending ``per_hour_salary`` must not be silently ignored
    — that would create or update the worker at ``DEFAULT_HOURLY_RATE`` and pay
    them the wrong amount. Fail with a 422 that names the replacement field.
    """
    if isinstance(data, Mapping) and LEGACY_RATE_FIELD in data:
        raise ValueError(LEGACY_FIELD_ERROR)
    return data


def rate_for_shift_record(
    worker_record: Optional[Mapping[str, Any]],
    user_doc: Optional[Mapping[str, Any]] = None,
    default: float = DEFAULT_HOURLY_RATE,
) -> float:
    """
    Resolve the rate to bill a worked shift at.

    The rate snapshotted onto the shift's worker record at checkout wins, so a
    later change to the worker's rate never retroactively rewrites past
    earnings. Falls back to the worker's current rate for shift records written
    before snapshotting existed, then to ``default``.
    """
    if isinstance(worker_record, Mapping):
        snapshot = coerce_hourly_rate(worker_record.get(CANONICAL_RATE_FIELD))
        if snapshot is None:
            snapshot = coerce_hourly_rate(worker_record.get(LEGACY_RATE_FIELD))
        if snapshot is not None:
            return snapshot
    return resolve_hourly_rate(user_doc, default=default)
