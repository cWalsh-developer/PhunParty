from pydantic import BaseModel
from typing import List, Dict

class GameResponse(BaseModel):
    game_code: str
    host_name: str
    players: List[str] = []
    scores: Dict[str, int] = {}

    class Config:
        orm_mode = True
