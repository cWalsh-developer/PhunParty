from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PresenceResponse(BaseModel):
    player_id: str
    is_online: bool
    show_online_status: bool
    last_seen_at: Optional[datetime] = None


class FriendsPresenceResponse(BaseModel):
    presence: list[PresenceResponse]
