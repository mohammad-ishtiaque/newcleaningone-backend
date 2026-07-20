import time
from fastapi import Request, HTTPException, status
from typing import Dict

# In-memory dictionary to store the last request time for each IP address.
# Since the time window is short (2 seconds), memory usage won't grow significantly fast,
# but it's a good practice to occasionally clear it or use an expiring cache if the window was larger.
_ip_rate_limits: Dict[str, float] = {}

def rate_limit_verify_email(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    current_time = time.time()
    
    last_request_time = _ip_rate_limits.get(client_ip)
    if last_request_time and current_time - last_request_time < 2.0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait 2 seconds before trying again."
        )
        
    _ip_rate_limits[client_ip] = current_time
