from pydantic import BaseModel

class GameCreation(BaseModel):
    host_name: str
    players: list[str] = [] 
    scores: dict[str, int] = {}

class GameJoinRequest(BaseModel):
    game_code: str
    player_name: str