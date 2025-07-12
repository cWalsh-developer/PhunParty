from pydantic import BaseModel
from datetime import datetime as DateTime

class GameCreation(BaseModel):
    genre : str
    rules: str

class GameSessionCreation(BaseModel):
    host_name: str
    number_of_questions: int
    game_code: str

class GameJoinRequest(BaseModel):
    game_code: str
    player_id: str