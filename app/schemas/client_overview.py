from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List

class TodaysOverallProgress(BaseModel):
    hours_completed: float = 4.8
    total_hours: float = 7.5
    hours_completed_str: str = "4h 48m completed"
    hours_remaining_str: str = "2h 42m remaining"
    rooms_completed: int = 12
    total_rooms: int = 15
    progress_percentage: float = 64.0
    status_badge: str = "Service in progress"
    location_name: Optional[str] = "Main office"
    service_time_slot: Optional[str] = "08:00 - 15:30"
    tracking_note: Optional[str] = "Room progress is available because this location uses room tracking."

class NextVisitCard(BaseModel):
    time_str: str = "Tomorrow, 09:00"
    team_name: str = "Team Alpha"
    specialists_count: int = 3

class OnSiteNowCard(BaseModel):
    specialists_count: int = 2
    sub_text: str = "Both currently active"

class LastCompletedCard(BaseModel):
    worked_str: str = "7h 18m worked"
    sub_text: str = "Thursday · data available"

class MetricsGrid(BaseModel):
    next_visit: NextVisitCard
    on_site_now: OnSiteNowCard
    last_completed: LastCompletedCard

class SpecialistOnSiteItem(BaseModel):
    worker_id: str
    name: str
    avatar: Optional[str] = None
    role_title: str = "Cleaning specialist"
    arrived_time_str: str = "arrived 08:32"
    time_worked_str: str = "3h 12m"
    percentage_assigned_time: float = 77.0

class LiveStatusSection(BaseModel):
    active_count: int = 2
    specialists: List[SpecialistOnSiteItem] = Field(default_factory=list)

class NextVisitorItem(BaseModel):
    scheduled_time_str: str = "Tomorrow at 09:00"
    team_name: str = "Team Alpha"
    specialists_count: int = 3
    team_avatars: List[str] = Field(default_factory=list)
    description: str = "Regular cleaning • approximately 7.5 hours"

class QuickActionItem(BaseModel):
    id: str
    title: str
    subtitle: str
    action_type: str

class ClientOverviewResponse(BaseModel):
    greeting_name: str = "Apex Technology Ltd."
    current_date_str: str = "Saturday, 25 July • Here's today's service at a glance."
    todays_progress: TodaysOverallProgress
    metrics_grid: MetricsGrid
    live_status: LiveStatusSection
    quick_actions: List[QuickActionItem] = Field(default_factory=list)
    next_visitors: NextVisitorItem

