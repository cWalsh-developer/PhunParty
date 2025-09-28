from pydantic import BaseModel
from app.models.enums import ResultType


class Scores(BaseModel):
    score_id: str
    score: int
    result: ResultType | None = None
    player_id: str
    session_code: str


class PlayerScores(BaseModel):
    player_id: str
    total_score: int
    games_played: int
    wins: int
    losses: int
    draws: int
