import httpx
from typing import List, Optional
from app.core.config import settings

class OneSignalService:
    def __init__(self):
        self.app_id = settings.ONESIGNAL_APP_ID
        self.api_key = settings.ONESIGNAL_REST_API_KEY
        self.base_url = "https://onesignal.com/api/v1/notifications"

    async def send_notification(
        self,
        headings: str,
        contents: str,
        player_ids: Optional[List[str]] = None,
        external_user_ids: Optional[List[str]] = None,
        is_broadcast: bool = False,
        data: Optional[dict] = None
    ):
        if not self.app_id or not self.api_key:
            print(f"[OneSignal Disabled] {headings} | {contents} | data={data}")
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
        
        # Determine audience targeting safely
        valid_player_ids = [p for p in (player_ids or []) if p]
        valid_external_ids = [str(u) for u in (external_user_ids or []) if u]

        if valid_player_ids:
            payload["include_player_ids"] = valid_player_ids
        elif valid_external_ids:
            payload["include_aliases"] = {"external_id": valid_external_ids}
        elif is_broadcast:
            payload["included_segments"] = ["Active Users"]
        else:
            # Targeted notification with no active device tokens registered
            print(f"[OneSignal Warning] No player_ids or external_user_ids for targeted push '{headings}'. Skipping push dispatch.")
            return
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(self.base_url, json=payload, headers=headers)
                response.raise_for_status()
                print(f"[OneSignal Success] Notification sent: {headings}")
            except httpx.HTTPStatusError as e:
                print(f"[OneSignal HTTP Error] status={e.response.status_code}, response={e.response.text}")
            except Exception as e:
                print(f"[OneSignal Exception] Failed to send push notification: {e}")
