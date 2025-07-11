from pydantic import BaseModel
from typing import List, Dict, Optional

class GameResponse(BaseModel):
    game_code: str
    host_name: str
    rules: str
    genre: str
    players: List[str] = []
    scores: Dict[str, int] = {}

class PlayerResponse(BaseModel):
    player_id: str
    player_name: str
    player_email: str
    player_mobile: str = None
    game_code: Optional[str] = None


    class Config:
        orm_mode = True
