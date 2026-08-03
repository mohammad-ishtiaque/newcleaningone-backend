from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api import (
    auth, worker, client, admin, profile, worker_shifts,
    admin_shift_monitoring, admin_dashboard, client_live_status,
    client_schedule, extra_services, chat, client_profile_settings
)
import os

from app.api.chat_admin_endpoints import chat_admin_router

def create_app(service_name: str = "all") -> FastAPI:
    service_name = service_name.lower().strip()
    
    title_map = {
        "admin": "Cleaning One Admin API",
        "client": "Cleaning One Client API",
        "worker": "Cleaning One Worker API",
        "all": "Cleaning One Monolith API"
    }
    
    app = FastAPI(
        title=title_map.get(service_name, "Cleaning One API"),
        description=f"Production-ready FastAPI {service_name.capitalize()} System",
        version="1.0.0"
    )
    
    origins = [
        "http://localhost",
        "http://localhost:8000",
        "http://localhost:8080",
        "http://localhost:8081",
        "http://localhost:8082",
        "http://localhost:8083",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
        "http://127.0.0.1:8083",
    ]
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Common routers across all services
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(chat.router)
    app.include_router(chat_admin_router)

    if service_name in ("admin", "all"):
        app.include_router(admin.router)
        app.include_router(admin.client_mgmt_router)
        app.include_router(admin.client_details_tabs_router)
        app.include_router(admin.location_mgmt_router)
        app.include_router(admin.location_drawer_router)
        app.include_router(admin.room_mgmt_router)
        app.include_router(admin.rooms_global_router)
        app.include_router(admin.cleaning_plan_mgmt_router)
        app.include_router(admin.worker_mgmt_router)
        app.include_router(admin.shift_mgmt_router)
        app.include_router(admin.roster_mgmt_router)
        app.include_router(admin.photo_reviews_router)
        app.include_router(admin.escalations_router)
        app.include_router(admin.qc_reports_router)
        app.include_router(admin.admin_notifications_router)
        app.include_router(admin_shift_monitoring.shift_monitoring_router)
        app.include_router(admin_dashboard.admin_dashboard_router)

    if service_name in ("client", "all"):
        app.include_router(client.router)
        app.include_router(client_live_status.router)
        app.include_router(client_schedule.router)
        app.include_router(extra_services.router)
        app.include_router(client_profile_settings.router)

    if service_name in ("worker", "all"):
        app.include_router(worker.router)
        app.include_router(worker_shifts.worker_shift_router)

    os.makedirs("uploads", exist_ok=True)
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

    @app.get("/")
    async def root():
        return {
            "message": f"Welcome to Cleaning One {service_name.capitalize()} API",
            "service": service_name
        }

    return app
