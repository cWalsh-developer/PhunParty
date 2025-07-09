from pydantic import BaseModel

class GameCreation(BaseModel):
    host_name: str
    players: list = []
    scores: dict = {}

class GameJoinRequest(BaseModel):
    game_code: str
    player_name: str