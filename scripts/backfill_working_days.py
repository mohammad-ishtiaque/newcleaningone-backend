"""
Repair `users.working_days` after the working-days save bug.

Background: `PATCH /worker/profile/working-days` filtered `users` on a string
`_id` while Mongo stores an ObjectId, so `update_one` matched zero documents and
saved nothing to `users` — while silently succeeding. The same request's writes
to `worker_availability` used a string `worker_id` and did land. So
`worker_availability.weekly_availability` holds each worker's real choice and
`users.working_days` is stale.

The manager dropdown reads `users.working_days` first and only falls back to
`worker_availability` when the field is absent, so a stale value is actively
served to managers.

This copies the real schedule from `worker_availability` into
`users.working_days`, but only where `worker_availability` is strictly newer
than the user document. Workers who never touched their schedule are untouched.

    python scripts/backfill_working_days.py           # read-only report
    python scripts/backfill_working_days.py --apply   # perform the writes
"""

import asyncio
import sys

from motor.motor_asyncio import AsyncIOMotorClient

from app.api.worker_shift_utils import normalize_working_days
from app.core.config import settings

DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def days_from_availability(avail_doc):
    """Derive 3-letter working days from a worker_availability document."""
    slots = (avail_doc or {}).get("weekly_availability") or []
    days = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        day = slot.get("day")
        if not day or not slot.get("is_available", True):
            continue
        code = str(day).strip().lower()[:3]
        if code in DAY_ORDER:
            days.append(code)
    return [d for d in DAY_ORDER if d in set(days)]


def as_naive(dt):
    """Compare timestamps regardless of whether Mongo stored them tz-aware."""
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


async def main(apply_changes: bool):
    client = AsyncIOMotorClient(settings.MONGO_URL, serverSelectionTimeoutMS=8000)
    db = client[settings.MONGO_DB_NAME]
    try:
        await db.command("ping")
    except Exception as exc:
        print(f"ERROR: cannot reach MongoDB at {settings.MONGO_DB_NAME}: {exc}")
        return 1

    workers = await db["users"].find({"role": "worker"}).to_list(length=None)
    avails = await db["worker_availability"].find({}).to_list(length=None)
    avail_map = {str(a.get("worker_id")): a for a in avails}

    print(f"Database : {settings.MONGO_DB_NAME}")
    print(f"Workers  : {len(workers)}   worker_availability docs: {len(avails)}")
    print(f"Mode     : {'APPLY (writes enabled)' if apply_changes else 'DRY RUN (read-only)'}\n")

    stale, agreed, no_record, ambiguous = [], [], [], []

    for user in workers:
        wid = str(user.get("_id"))
        name = user.get("full_name") or user.get("email") or wid
        stored, _ = normalize_working_days(user.get("working_days"))

        avail = avail_map.get(wid)
        if not avail:
            no_record.append((wid, name, stored))
            continue

        real = days_from_availability(avail)
        if not real:
            no_record.append((wid, name, stored))
            continue

        if real == stored:
            agreed.append((wid, name, stored))
            continue

        u_at = as_naive(user.get("updated_at"))
        a_at = as_naive(avail.get("updated_at"))
        # Only trust worker_availability when it is demonstrably the newer write.
        if a_at and u_at and a_at > u_at:
            stale.append((wid, name, stored, real, u_at, a_at))
        elif a_at and not u_at:
            stale.append((wid, name, stored, real, u_at, a_at))
        else:
            ambiguous.append((wid, name, stored, real, u_at, a_at))

    print(f"--- STALE: users.working_days older than the worker's real choice ({len(stale)}) ---")
    for wid, name, stored, real, u_at, a_at in stale:
        print(f"  {name} ({wid})")
        print(f"      users.working_days  = {stored}   (updated {u_at})")
        print(f"      worker_availability = {real}   (updated {a_at})  <-- will be applied")

    print(f"\n--- ALREADY CORRECT ({len(agreed)}) ---")
    for wid, name, stored in agreed:
        print(f"  {name} ({wid}): {stored}")

    print(f"\n--- NO AVAILABILITY RECORD, left as-is ({len(no_record)}) ---")
    for wid, name, stored in no_record:
        print(f"  {name} ({wid}): {stored}")

    if ambiguous:
        print(f"\n--- AMBIGUOUS, NOT touched: users doc is newer or timestamps missing ({len(ambiguous)}) ---")
        for wid, name, stored, real, u_at, a_at in ambiguous:
            print(f"  {name} ({wid}): users={stored} (updated {u_at}) vs availability={real} (updated {a_at})")

    if not stale:
        print("\nNothing to repair.")
        client.close()
        return 0

    if not apply_changes:
        print(f"\nDRY RUN — no writes performed. Re-run with --apply to update {len(stale)} worker(s).")
        client.close()
        return 0

    from bson import ObjectId
    updated = 0
    for wid, name, stored, real, _u, _a in stale:
        oid = ObjectId(wid) if ObjectId.is_valid(wid) else wid
        res = await db["users"].update_one({"_id": oid}, {"$set": {"working_days": real}})
        if res.modified_count:
            updated += 1
            print(f"  updated {name} ({wid}): {stored} -> {real}")
        else:
            print(f"  WARNING: no document modified for {name} ({wid})")

    print(f"\nApplied to {updated}/{len(stale)} worker(s).")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
