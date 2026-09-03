import os
import sys
import uvicorn
from app.app_builder import create_app
from app.core.config import settings

# Determine service name from env var or CLI arguments
# Reload trigger client
cli_arg = sys.argv[1].lower().strip() if len(sys.argv) > 1 and not sys.argv[1].startswith("-") and sys.argv[1] not in ("main:app",) else None
service_name = cli_arg or os.environ.get("SERVICE_NAME", "all").lower().strip()

if service_name not in ("manager", "admin", "client", "worker", "all"):
    service_name = "all"

os.environ["SERVICE_NAME"] = service_name
app = create_app(service_name)

def get_port(service: str) -> int:
    if service == "manager":
        return settings.MANAGER_PORT
    elif service == "admin":
        return settings.ADMIN_PORT
    elif service == "client":
        return settings.CLIENT_PORT
    elif service == "worker":
        return settings.WORKER_PORT
    return settings.APP_PORT

if __name__ == "__main__":
    port = get_port(service_name)
    print(f"Starting Cleaning One {service_name.upper()} service on {settings.APP_HOST}:{port}...")
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=port,
        reload=True,
        access_log=True,
        log_level="info"
    )

