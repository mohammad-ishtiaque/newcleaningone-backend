import sys
import uvicorn
from app.app_builder import create_app
from app.core.config import settings

# Determine service name from command line arguments (e.g., python main.py admin)
service_name = sys.argv[1].lower().strip() if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "all"

if service_name not in ("admin", "client", "worker", "all"):
    print(f"Unknown service '{service_name}'. Valid options: admin, client, worker, all")
    print("Defaulting to 'all'...")
    service_name = "all"

app = create_app(service_name)

def get_port(service: str) -> int:
    if service == "admin":
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
