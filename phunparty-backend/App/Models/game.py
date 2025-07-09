from pydantic import BaseModel

class GameCreation(BaseModel):
    host_name: str
    players: list = []
    scores: dict = {}