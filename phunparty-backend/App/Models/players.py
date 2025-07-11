from pydantic import BaseModel

class Player(BaseModel):
    player_id: str
    game_code: str
    player_name: str
    score: int = 0