from datetime import datetime
from typing import Optional

from app.security.input_validation import (
    normalize_friend_code,
    validate_friend_request_message,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FriendCodeResponse(BaseModel):
    friend_code: Optional[str] = None
    allow_friend_code_search: bool
    allow_phone_discovery: bool
    friend_request_notifications_enabled: bool


class FriendSearchRequest(BaseModel):
    friend_code: str = Field(..., min_length=3, max_length=20)

    @field_validator("friend_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return normalize_friend_code(value)


class FriendRequestCreate(BaseModel):
    friend_code: str = Field(..., min_length=3, max_length=20)
    message: Optional[str] = Field(default=None, max_length=240)

    @field_validator("friend_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return normalize_friend_code(value)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: Optional[str]) -> Optional[str]:
        return validate_friend_request_message(value)


class FriendProfileResponse(BaseModel):
    player_id: str
    player_name: Optional[str] = None
    player_email: Optional[str] = None
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None
    friend_code: Optional[str] = None
    relationship_status: str = "none"
    profile_visibility: Optional[str] = None
    can_view_profile: Optional[bool] = None
    can_view_game_stats: Optional[bool] = None
    share_game_stats: Optional[bool] = None
    show_online_status: Optional[bool] = None
    is_online: Optional[bool] = None
    last_seen_at: Optional[datetime] = None


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
