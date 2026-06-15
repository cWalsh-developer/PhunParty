from typing import Optional

from app.security.input_validation import (
    normalize_email,
    normalize_mobile,
    validate_password,
    validate_player_name,
)
from pydantic import BaseModel, field_validator


class Player(BaseModel):
    game_code: Optional[str] = None
    player_name: str
    player_email: str
    hashed_password: str
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None

    @field_validator("player_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_player_name(value)

    @field_validator("player_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("player_mobile")
    @classmethod
    def validate_mobile(cls, value: Optional[str]) -> Optional[str]:
        return normalize_mobile(value)

    @field_validator("hashed_password")
    @classmethod
    def validate_plain_password(cls, value: str) -> str:
        return validate_password(value)


class PlayerUpdate(BaseModel):
    game_code: Optional[str] = None
    player_name: Optional[str] = None
    player_email: Optional[str] = None
    hashed_password: Optional[str] = None  # Now optional
    player_mobile: Optional[str] = None
    profile_photo_url: Optional[str] = None

    @field_validator("player_name")
    @classmethod
    def validate_optional_name(cls, value: Optional[str]) -> Optional[str]:
        return validate_player_name(value) if value is not None else None

    @field_validator("player_email")
    @classmethod
    def validate_optional_email(cls, value: Optional[str]) -> Optional[str]:
        return normalize_email(value) if value is not None else None

    @field_validator("player_mobile")
    @classmethod
    def validate_optional_mobile(cls, value: Optional[str]) -> Optional[str]:
        return normalize_mobile(value)

    @field_validator("hashed_password")
    @classmethod
    def validate_optional_password(cls, value: Optional[str]) -> Optional[str]:
        return validate_password(value) if value is not None else None
