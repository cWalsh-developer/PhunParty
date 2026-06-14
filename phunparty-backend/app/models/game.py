from typing import Optional

from pydantic import BaseModel, ConfigDict


class GameCreation(BaseModel):
    genre: str
    rules: str


class GameSessionCreation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host_name: str
    number_of_questions: int
    game_code: str
    ispublic: bool = True
    difficulty: Optional[str] = None
    beat_clock_duration_seconds: Optional[int] = None
    duration_seconds: Optional[int] = None
    timer_seconds: Optional[int] = None


class GameJoinRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_code: str
