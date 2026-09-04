from app.api.admin.profile_company import router, require_manager
from app.api.admin.overview_users import get_user_service
from app.api.admin.clients import client_mgmt_router
from app.api.admin.client_details_tabs import client_details_tabs_router
from app.api.admin.locations_rooms import location_mgmt_router
from app.api.admin.rooms import room_mgmt_router
from app.api.admin.location_drawer_endpoints import location_drawer_router
from app.api.admin.rooms_global import rooms_global_router
from app.api.admin.cleaning_plans import cleaning_plan_mgmt_router
from app.api.admin.cleaning_plan_dropdowns import cleaning_plan_dropdowns_router
from app.api.admin.workers import worker_mgmt_router
from app.api.admin.roster import roster_mgmt_router
from app.api.admin.photo_reviews import photo_reviews_router
from app.api.admin.escalations import escalations_router
from app.api.admin.reports_quality_control import qc_reports_router
from app.api.admin.notifications import admin_notifications_router
from app.api.admin.worker_approvals import worker_approvals_router
from app.api.admin.worker_earnings_invoices import worker_earnings_invoices_router

__all__ = [
    "router",
    "client_mgmt_router",
    "client_details_tabs_router",
    "location_mgmt_router",
    "location_drawer_router",
    "room_mgmt_router",
    "rooms_global_router",
    "cleaning_plan_mgmt_router",
    "cleaning_plan_dropdowns_router",
    "worker_mgmt_router",
    "worker_approvals_router",
    "worker_earnings_invoices_router",
    "roster_mgmt_router",
    "photo_reviews_router",
    "escalations_router",
    "qc_reports_router",
    "admin_notifications_router",
    "require_manager",
    "get_user_service"
]



