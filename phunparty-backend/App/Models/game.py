from pydantic import BaseModel
from datetime import datetime as DateTime

class GameCreation(BaseModel):
    host_name: str
    players: list[str] = [] 
    scores: dict[str, int] = {}
    genre : str
    rules: str

class GameJoinRequest(BaseModel):
    game_code: str
    player_name: str

class GameHistory(BaseModel):
    history_code: str
    game_code: str
    players: list[str]
    results: dict[str, int]
    date_played: DateTime