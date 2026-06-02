from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class FriendCodeResponse(BaseModel):
    friend_code: str
    allow_friend_code_search: bool
    allow_phone_discovery: bool
    friend_request_notifications_enabled: bool


class FriendSearchRequest(BaseModel):
    friend_code: str = Field(..., min_length=3, max_length=20)


class FriendRequestCreate(BaseModel):
    friend_code: str = Field(..., min_length=3, max_length=20)
    message: Optional[str] = Field(default=None, max_length=240)


class FriendProfileResponse(BaseModel):
    player_id: str
    player_name: Optional[str] = None
    profile_photo_url: Optional[str] = None
    friend_code: str
    relationship_status: str = "none"


class FriendRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sender_player_id: str
    receiver_player_id: str
    status: str
    message: Optional[str] = None
    created_at: datetime
    responded_at: Optional[datetime] = None
    sender: Optional[FriendProfileResponse] = None
    receiver: Optional[FriendProfileResponse] = None


class FriendsListResponse(BaseModel):
    friends: list[FriendProfileResponse]
