from pydantic import BaseModel
from typing import Optional


class GameResponse(BaseModel):
    game_code: str
    rules: str
    genre: str


class PlayerResponse(BaseModel):
    player_id: str
    player_name: str
    player_email: str
    player_mobile: str = None
    active_game_code: Optional[str] = None


class AnswerVerificationResponseModel(BaseModel):
    player_answer: str
    is_correct: bool

    class Config:
        orm_mode = True
