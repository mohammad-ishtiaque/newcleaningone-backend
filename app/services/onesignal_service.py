import httpx
from typing import List
from app.core.config import settings

class OneSignalService:
    def __init__(self):
        self.app_id = settings.ONESIGNAL_APP_ID
        self.api_key = settings.ONESIGNAL_REST_API_KEY
        self.base_url = "https://onesignal.com/api/v1/notifications"

    async def send_notification(self, headings: str, contents: str, player_ids: List[str] = None, data: dict = None):
        if not self.app_id or not self.api_key:
            print(f"Mock OneSignal Notification: {headings} | {contents} | data={data}")
            return
            
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "app_id": self.app_id,
            "contents": {"en": contents},
            "headings": {"en": headings},
        }
        if data:
            payload["data"] = data
        
        if player_ids:
            payload["include_player_ids"] = player_ids
        else:
            payload["included_segments"] = ["Active Users"]
            
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.base_url, json=payload, headers=headers)
                response.raise_for_status()
                print("Successfully sent OneSignal notification.")
            except Exception as e:
                print(f"Failed to send OneSignal notification: {e}")
