from pydantic import BaseModel
from sqlalchemy import Enum


class Scores(BaseModel):
    score_id: str
    score: int
    result: Enum  # 'win', 'lose', or 'draw'
    player_id: str
    session_code: str
