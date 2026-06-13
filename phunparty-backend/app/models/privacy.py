from typing import Literal, Optional

from pydantic import BaseModel

ProfileVisibility = Literal["public", "friends", "private"]


class PrivacySettingsUpdate(BaseModel):
    profile_visibility: Optional[ProfileVisibility] = None
    show_online_status: Optional[bool] = None
    allow_friend_requests: Optional[bool] = None
    share_game_stats: Optional[bool] = None
    data_collection: Optional[bool] = None
    crash_reporting: Optional[bool] = None


class PrivacySettingsResponse(BaseModel):
    profile_visibility: ProfileVisibility
    show_online_status: bool
    allow_friend_requests: bool
    share_game_stats: bool
    data_collection: bool
    crash_reporting: bool
