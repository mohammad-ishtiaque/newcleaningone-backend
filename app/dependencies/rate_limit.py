import time
from fastapi import Request, HTTPException, status
from app.core.database import get_database
from datetime import datetime, timezone, timedelta

async def check_rate_limit(request: Request, action: str, max_requests: int, window_seconds: int):
    client_ip = request.client.host if request.client else "unknown"
    db = get_database()
    
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)
    
    count = await db["rate_limits"].count_documents({
        "ip": client_ip,
        "action": action,
        "timestamp": {"$gte": window_start}
    })
    
    if count >= max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Please try again later."
        )
        
    await db["rate_limits"].insert_one({
        "ip": client_ip,
        "action": action,
        "timestamp": now
    })

async def rate_limit_verify_email(request: Request):
    await check_rate_limit(request, action="verify_email", max_requests=5, window_seconds=900)

async def rate_limit_signup(request: Request):
    await check_rate_limit(request, action="signup", max_requests=3, window_seconds=3600)
