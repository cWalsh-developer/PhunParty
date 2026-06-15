from typing import Optional

from app.security.input_validation import normalize_session_code, validate_player_name
from pydantic import BaseModel, ConfigDict, Field, field_validator


class GameCreation(BaseModel):
    genre: str
    rules: str


class GameSessionCreation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host_name: str = Field(..., max_length=40)
    # Beat the Clock uses a large question pool sentinel from the website.
    number_of_questions: int = Field(..., ge=1, le=1000)
    game_code: str
    ispublic: bool = True
    difficulty: Optional[str] = None
    beat_clock_duration_seconds: Optional[int] = None
    duration_seconds: Optional[int] = None
    timer_seconds: Optional[int] = None

    @field_validator("host_name")
    @classmethod
    def validate_host_name(cls, value: str) -> str:
        return validate_player_name(value)


class GameJoinRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_code: str

    @field_validator("session_code")
    @classmethod
    def validate_session_code(cls, value: str) -> str:
        return normalize_session_code(value)
