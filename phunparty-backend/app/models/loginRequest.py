from typing import Optional

from app.security.input_validation import (
    SanitizedRequestModel,
    normalize_email,
    validate_password,
)
from pydantic import Field, field_validator


class LoginRequest(SanitizedRequestModel):
    player_email: str
    password: str
    device_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("player_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_login_password(cls, value: str) -> str:
        return validate_password(value)


class ChangePasswordRequest(SanitizedRequestModel):
    current_password: str
    new_password: str

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


class RefreshTokenRequest(SanitizedRequestModel):
    refresh_token: str = Field(..., min_length=40)
    device_id: Optional[str] = Field(default=None, max_length=128)


class LogoutRequest(SanitizedRequestModel):
    refresh_token: str = Field(..., min_length=40)
