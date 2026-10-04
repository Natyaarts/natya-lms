import os
import logging
import requests
import base64
from django.conf import settings

logger = logging.getLogger(__name__)


class ZoomService:
    """
    Handles Zoom Server-to-Server OAuth authentication and automatic meeting generation.
    """

    @classmethod
    def get_credentials(cls):
        account_id = getattr(settings, 'ZOOM_ACCOUNT_ID', None) or os.environ.get('ZOOM_ACCOUNT_ID', 'btl6dp5ATMCqve1PVjtUbQ')
        client_id = getattr(settings, 'ZOOM_CLIENT_ID', None) or os.environ.get('ZOOM_CLIENT_ID', 'ESC0pUcUSnSJXTsZS7xRgQ')
        client_secret = getattr(settings, 'ZOOM_CLIENT_SECRET', None) or os.environ.get('ZOOM_CLIENT_SECRET', 'FWiwOex9oFrzDQe5taK24SegAA9uvi77')
        return account_id.strip(), client_id.strip(), client_secret.strip()

    @classmethod
    def get_access_token(cls):
        account_id, client_id, client_secret = cls.get_credentials()
        auth_str = f"{client_id}:{client_secret}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"
        headers = {
            "Authorization": f"Basic {b64_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        res = requests.post(url, headers=headers, timeout=10)
        if res.status_code != 200:
            logger.error(f"Failed to get Zoom OAuth token: {res.status_code} {res.text}")
            raise ValueError(f"Zoom authentication failed: {res.text}")

        return res.json().get('access_token')

    @classmethod
    def create_meeting(cls, topic, start_time_iso=None, duration_minutes=60):
        token = cls.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "topic": topic or "Natya Arts Academy Live Class",
            "type": 2 if start_time_iso else 1,
            "duration": int(duration_minutes) if duration_minutes else 60,
            "timezone": "Asia/Kolkata",
            "settings": {
                "host_video": True,
                "participant_video": True,
                "join_before_host": False,
                "mute_upon_entry": True,
                "waiting_room": True
            }
        }
        if start_time_iso:
            payload["start_time"] = start_time_iso

        res = requests.post("https://api.zoom.us/v2/users/me/meetings", headers=headers, json=payload, timeout=12)
        if res.status_code != 201:
            logger.error(f"Failed to create Zoom meeting: {res.status_code} {res.text}")
            raise ValueError(f"Zoom meeting creation failed: {res.text}")

        data = res.json()
        return {
            "meeting_id": str(data.get("id")),
            "join_url": data.get("join_url"),
            "start_url": data.get("start_url"),
            "password": data.get("password")
        }
