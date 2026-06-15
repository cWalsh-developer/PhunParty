import re
from typing import Optional

from app.utils.phone_numbers import normalize_phone_number

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
CODE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
AVATAR_SEED_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def reject_control_chars(value: str, field_name: str) -> str:
    if CONTROL_CHARS.search(value):
        raise ValueError(f"{field_name} cannot contain control characters")
    return value


def validate_display_text(
    value: Optional[str],
    *,
    field_name: str,
    max_length: int,
    allow_angle_brackets: bool = False,
) -> Optional[str]:
    if value is None:
        return None

    value = value.strip()
    reject_control_chars(value, field_name)

    if not allow_angle_brackets and ("<" in value or ">" in value):
        raise ValueError(f"{field_name} cannot contain angle brackets")

    if len(value) > max_length:
        raise ValueError(f"{field_name} cannot be longer than {max_length} characters")

    return value


def validate_player_name(value: str) -> str:
    value = validate_display_text(
        value,
        field_name="player_name",
        max_length=40,
    )
    if not value:
        raise ValueError("player_name is required")
    return value


def normalize_email(value: str) -> str:
    value = (value or "").strip().lower()
    reject_control_chars(value, "player_email")
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise ValueError("Invalid email address")
    return value


def validate_password(value: str) -> str:
    if value is None or len(value) < 8 or len(value) > 128:
        raise ValueError("Password must be between 8 and 128 characters")
    return value


def normalize_mobile(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    return normalize_phone_number(value)


def normalize_code(value: str, field_name: str) -> str:
    value = (value or "").replace(" ", "").upper().strip()
    reject_control_chars(value, field_name)
    if not CODE_RE.match(value):
        raise ValueError(f"{field_name} must be 6 to 12 uppercase letters or digits")
    return value


def normalize_friend_code(value: str) -> str:
    return normalize_code(value.replace("PHUN-", ""), "friend_code")


def normalize_session_code(value: str) -> str:
    return normalize_code(value, "session_code")


def validate_friend_request_message(value: Optional[str]) -> Optional[str]:
    return validate_display_text(
        value,
        field_name="message",
        max_length=200,
        allow_angle_brackets=False,
    )


def validate_avatar_seed(value: str) -> str:
    value = (value or "default").strip()
    reject_control_chars(value, "avatar_seed")
    if not AVATAR_SEED_RE.match(value):
        raise ValueError("avatar_seed may only contain letters, numbers, _ and -")
    return value
