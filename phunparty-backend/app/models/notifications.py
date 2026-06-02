from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PushTokenRegistrationRequest(BaseModel):
    expo_push_token: str = Field(..., min_length=10)
    device_id: Optional[str] = None
    platform: Optional[str] = None


class PushTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    player_id: str
    expo_push_token: str
    device_id: Optional[str] = None
    platform: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recipient_player_id: str
    actor_player_id: Optional[str] = None
    type: str
    title: str
    body: str
    data: Optional[dict] = None
    is_read: bool
    created_at: datetime
    read_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]


class NotificationSettingsUpdate(BaseModel):
    friend_request_notifications_enabled: Optional[bool] = None
    allow_friend_code_search: Optional[bool] = None
    allow_phone_discovery: Optional[bool] = None


class NotificationSettingsResponse(BaseModel):
    friend_request_notifications_enabled: bool
    allow_friend_code_search: bool
    allow_phone_discovery: bool
